#!/usr/bin/env bash
# Phase 1: get the actual video file for a link (or take a local file), validate it.
#
#   bash scripts/download.sh <url|local-file> [run-id]
#
# Creates agent-vision-runs/<run-id>/ under the CURRENT directory, writes
# input/reference.mp4 and input/source.json, and prints the run folder path on
# the last line. On any failure it stops with a specific reason (exit 2) and
# writes input/FAILED.txt — it never continues on partial data and never retries
# silently. Only yt-dlp is used, anonymously: no browser, no login, no cookies.
set -uo pipefail
SKILL="$(cd "$(dirname "$0")/.." && pwd)"
PY="$SKILL/.venv/bin/python"
YTDLP="$SKILL/.venv/bin/yt-dlp"
SRC="${1:?usage: download.sh <url|local-file> [run-id]}"
RUN_ID="${2:-$(date +%Y%m%d-%H%M%S)}"
RUN="$PWD/agent-vision-runs/$RUN_ID"
IN="$RUN/input"
mkdir -p "$IN"

fail() {
  printf '%s\n' "$1" > "$IN/FAILED.txt"
  echo "STOPPED: $1" >&2
  echo "  run folder: $RUN" >&2
  exit 2
}

if [[ "$SRC" =~ ^https?:// ]]; then
  # Anonymous only. A session cookie is a live login (account takeover if it
  # leaks), so none is ever used: --ignore-config stops a personal yt-dlp config
  # from adding one, --no-cookies / --no-cookies-from-browser block both sources.
  # Best video + best audio, merged to MP4. --no-playlist: a carousel/profile URL
  # must not quietly turn into many downloads.
  "$YTDLP" --ignore-config --no-cookies --no-cookies-from-browser \
    --no-playlist --no-progress --no-warnings \
    -f "bv*+ba/b" --merge-output-format mp4 \
    --write-info-json -o "$IN/download.%(ext)s" "$SRC" > "$IN/yt-dlp.log" 2>&1
  code=$?
  log=$(cat "$IN/yt-dlp.log")
  if [ $code -ne 0 ]; then
    reason=$(grep -m1 'ERROR' "$IN/yt-dlp.log" | sed 's/^ERROR: //')
    case "$log" in
      *"login required"*|*"Use --cookies"*|*"rate-limit reached or login required"*|*"empty media response"*)
        fail "the site only serves this video to logged-in users, and Agent Vision never logs in. Screen-record the reel on your phone (iPhone/Android 11+ record the app's sound too), copy the file here and pass its path instead of the link. yt-dlp said: $reason" ;;
      *"Private"*|*"private"*)
        fail "the post is private or from a private account — only its owner's followers can see it. yt-dlp said: $reason" ;;
      *"Unsupported URL"*)
        fail "this link isn't a video yt-dlp can fetch (unsupported site or a page with no video). yt-dlp said: $reason" ;;
      *"404"*|*"not available"*|*"does not exist"*|*"removed"*)
        fail "the video isn't available (removed, wrong link, or region-locked). yt-dlp said: $reason" ;;
      *"There is no video in this post"*)
        fail "this post has no video (image or carousel of stills). Agent Vision needs a video. yt-dlp said: $reason" ;;
      *)
        fail "download failed: ${reason:-see input/yt-dlp.log}" ;;
    esac
  fi
  file=$(ls "$IN"/download.mp4 "$IN"/download.mkv "$IN"/download.webm 2>/dev/null | head -1)
  [ -n "$file" ] || fail "yt-dlp exited OK but wrote no video file — see input/yt-dlp.log"
  info="$IN/download.info.json"
else
  [ -f "$SRC" ] || fail "file not found: $SRC"
  file="$SRC"
  info=""
fi

# Validate before anything else uses it: opens cleanly, has a video stream, non-zero duration.
probe=$(ffprobe -v error -show_entries format=duration:stream=codec_type -of json "$file" 2>&1) \
  || fail "ffprobe can't open the file — it is corrupt or not a video: $(echo "$probe" | head -2)"
IN="$IN" "$PY" - "$probe" <<'PYEOF' || fail "$(cat "$IN/.why" 2>/dev/null)"
import json, sys, os
p = json.loads(sys.argv[1])
why = os.path.join(os.environ.get("IN", "."), ".why")
kinds = [s["codec_type"] for s in p.get("streams", [])]
dur = float(p.get("format", {}).get("duration") or 0)
msg = None
if "video" not in kinds:
    msg = "the file has no video stream"
elif dur <= 0:
    msg = "the file has zero duration"
if msg:
    open(why, "w").write(msg)
    sys.exit(1)
PYEOF

# Normalise to H.264/AAC MP4 at the source frame rate so every later tool reads it
# the same way. Stream copy when already compatible (no generation loss).
vcodec=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 "$file")
rates=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate,avg_frame_rate -of csv=p=0 "$file")
# Phone screen recordings are variable-frame-rate (frames only when the screen
# changes). Lay those onto a constant grid at the nearest standard rate, or every
# frame-accurate time downstream drifts.
fps=$("$PY" - "$rates" <<'PYEOF'
import sys
vals = []
for r in sys.argv[1].replace("\n", ",").split(","):
    if "/" in r:
        n, d = (float(x) for x in r.split("/"))
        if d and n:
            vals.append(n / d)
# r_frame_rate (first) is the nominal rate; the average sinks when a recorder
# skips static frames, so it must not decide the grid.
nominal = vals[0] if vals else 30.0
std = [23.976, 24, 25, 29.97, 30, 50, 59.94, 60]
snapped = min(std, key=lambda s: abs(s - min(nominal, 60)))
# Constant only if nominal and average agree (within rounding).
cfr = len(vals) >= 2 and abs(vals[0] - vals[1]) < 0.01 * vals[0]
print(snapped, "yes" if cfr else "no")
PYEOF
)
read -r fps cfr <<< "$fps"
if [ "$vcodec" = "h264" ] && [[ "$file" == *.mp4 ]] && [ "$cfr" = yes ]; then
  cp "$file" "$IN/reference.mp4"
else
  ffmpeg -hide_banner -loglevel error -y -i "$file" -map 0:v:0 -map '0:a:0?' \
    -fps_mode cfr -r "$fps" -c:v libx264 -crf 12 -preset veryfast -pix_fmt yuv420p \
    -c:a aac -b:a 192k "$IN/reference.mp4" \
    || fail "could not convert the video to MP4"
  [ "$cfr" = no ] && echo "note: variable frame rate input — normalised to constant $fps fps" >&2
fi
[ "$file" != "$SRC" ] && [ "$file" != "$IN/reference.mp4" ] && rm -f "$file"

# Keep the facts about where it came from. Caption/description are page text,
# labelled as such — they are not the analysed media.
IN="$IN" "$PY" - "$SRC" "$info" <<'PYEOF'
import json, os, sys
src, info_path = sys.argv[1], sys.argv[2]
out = {"source": src, "kind": "url" if src.startswith("http") else "local file"}
if info_path and os.path.exists(info_path):
    i = json.load(open(info_path))
    out.update({k: i.get(k) for k in ("extractor", "id", "webpage_url", "uploader", "upload_date", "duration", "width", "height", "fps", "ext")})
    out["page_text_unanalysed"] = (i.get("description") or i.get("title") or "")[:1000]
    os.remove(info_path)
json.dump(out, open(os.path.join(os.environ["IN"], "source.json"), "w"), indent=2)
PYEOF

dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$IN/reference.mp4")
audio=$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$IN/reference.mp4")
echo "OK: $(printf '%.2f' "$dur") s, audio: $([ -n "$audio" ] && echo yes || echo NO — visuals only)"
echo "$RUN"
