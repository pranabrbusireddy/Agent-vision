#!/usr/bin/env bash
# End-to-end test with known answers. No Instagram, no browser.
#
# A local HTTP server stands in for a video host; yt-dlp downloads from it through
# the same download.sh a real link uses. The reference clip has cuts and beeps at
# KNOWN times, so every analysis number can be checked, not eyeballed.
#
#   bash scripts/selftest.sh            analysis + negative tests + brief gate
#   bash scripts/selftest.sh --render   …plus Remotion build, render and verify (slow first time)
set -uo pipefail
SKILL="$(cd "$(dirname "$0")/.." && pwd)"
S="$SKILL/scripts"
PY="$SKILL/.venv/bin/python"
OUT="$SKILL/selftest-runs"
PORT=8766
# One run at a time: runs share selftest-runs/ and the port, so a second run
# would wipe the first's files mid-flight. mkdir is atomic, so it works as a lock.
LOCK="$SKILL/.selftest.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "another selftest is running (lock: $LOCK, pid $(cat "$LOCK/pid" 2>/dev/null || echo ?)); if it isn't, remove the lock dir" >&2
  exit 3
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT
rm -rf "$OUT" && mkdir -p "$OUT/www" "$OUT/assets"
cd "$OUT"
status=0
pass() { echo "  PASS  $1"; }
fail() { echo "  FAIL  $1"; status=1; }

# ------------------------------------------------------------------ fixtures
CUTS=(1.0 2.5 4.2 4.4 7.3 9.0 10.4 12.8)
DUR=15
BEAT=0.5
printf '{"cuts":[%s],"duration":%s,"beat":%s}\n' "$(IFS=,; echo "${CUTS[*]}")" "$DUR" "$BEAT" > truth.json
bounds=(0 "${CUTS[@]}" "$DUR")
inputs=(); filters=""; labels=""
for i in $(seq 0 $((${#bounds[@]} - 2))); do
  d=$(python3 -c "print(round(${bounds[$((i+1))]} - ${bounds[$i]}, 3))")
  inputs+=(-f lavfi -i "testsrc2=s=720x1280:r=30:d=$d")
  neg=$([ $((i % 2)) -eq 1 ] && echo ",negate" || echo "")
  filters+="[$i:v]hue=h=$((i * 75)):s=1.6$neg,setsar=1[v$i];"
  labels+="[v$i]"
done
n=$((${#bounds[@]} - 1))
ffmpeg -hide_banner -loglevel error -y "${inputs[@]}" \
  -f lavfi -i "aevalsrc='0.8*sin(2*PI*880*t)*lt(mod(t\,$BEAT)\,0.05)':s=48000:d=$DUR" \
  -filter_complex "${filters}${labels}concat=n=$n:v=1:a=0[v]" -map "[v]" -map "$n:a" \
  -c:v libx264 -crf 18 -pix_fmt yuv420p -c:a aac -b:a 192k www/clip.mp4
ffmpeg -hide_banner -loglevel error -y -i www/clip.mp4 -an -c copy www/silent.mp4
echo '<!doctype html><title>no video here</title><p>Just a page.</p>' > www/page.html
# A stand-in "brand logo" for the build test.
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "color=c=0xf97316:s=400x400,format=rgba" -frames:v 1 assets/logo.png

python3 -m http.server "$PORT" --bind 127.0.0.1 --directory www >/dev/null 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true; rm -rf "$LOCK"' EXIT
sleep 1
URL="http://127.0.0.1:$PORT"

# ------------------------------------------------------------------ preflight
echo "== preflight"
bash "$S/preflight.sh" >/dev/null && pass "preflight ready" || fail "preflight not ready"

# ------------------------------------------------------------------ happy path
echo "== download + analysis (reference clip)"
if bash "$S/download.sh" "$URL/clip.mp4" ref > dl.log 2>&1; then pass "download: $(tail -2 dl.log | head -1)"; else fail "download: $(cat dl.log)"; echo "SELFTEST FAIL"; exit 1; fi
RUN="$OUT/agent-vision-runs/ref"
"$PY" "$S/probe.py" "$RUN"
"$PY" "$S/audio.py" "$RUN"
"$PY" "$S/transcribe.py" "$RUN" --model mlx-community/whisper-tiny
"$PY" "$S/align.py" "$RUN"
"$PY" "$S/palette.py" "$RUN" >/dev/null
"$PY" "$S/selftest_check.py" analysis truth.json "$RUN" || status=1

# ------------------------------------------------------------------ negative tests
echo "== negative tests"
expect_stop() {  # name, source, expected substring in the reason
  if bash "$S/download.sh" "$2" "neg-$1" > "neg-$1.log" 2>&1; then
    fail "$1: should have stopped, but succeeded"
  elif grep -qi "$3" "neg-$1.log"; then
    pass "$1: stopped — $(grep STOPPED "neg-$1.log" | cut -c1-110)"
  else
    fail "$1: stopped but without a clear reason: $(cat "neg-$1.log")"
  fi
}
expect_stop missing-file "$OUT/nope.mp4" "file not found"
expect_stop dead-link "$URL/gone.mp4" "isn't available"
expect_stop non-video "$URL/page.html" "isn't a video\|no video"
if bash "$S/download.sh" "$URL/silent.mp4" silent > silent.log 2>&1; then
  "$PY" "$S/probe.py" agent-vision-runs/silent >/dev/null
  "$PY" "$S/audio.py" agent-vision-runs/silent >/dev/null
  "$PY" "$S/transcribe.py" agent-vision-runs/silent >/dev/null
  "$PY" "$S/align.py" agent-vision-runs/silent >/dev/null
  "$PY" -c "import json,sys; a=json.load(open('agent-vision-runs/silent/analysis/audio.json')); sys.exit(a['available'])" \
    && pass "audio-less clip: downloads, audio reported unavailable ($(grep -o 'audio: .*' silent.log))" \
    || fail "audio-less clip: audio not reported unavailable"
else
  fail "audio-less clip: download failed: $(cat silent.log)"
fi

# A phone-style screen recording: HEVC .mov, variable frame rate. Must normalise
# to constant fps with every cut still on its frame.
# Drop every 5th frame but keep timestamps (what recorders do on static screens).
ffmpeg -hide_banner -loglevel error -y -i www/clip.mp4 -vf "select='not(eq(mod(n\,5)\,2))'" -fps_mode vfr \
  -c:v hevc_videotoolbox -b:v 6M -tag:v hvc1 -c:a aac phone-recording.mov 2>/dev/null \
  || ffmpeg -hide_banner -loglevel error -y -i www/clip.mp4 -vf "select='not(eq(mod(n\,5)\,2))'" -fps_mode vfr -c:v libx265 -tag:v hvc1 -c:a aac phone-recording.mov
echo "  fixture: $(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,r_frame_rate,avg_frame_rate -of csv=p=0 phone-recording.mov)"
if bash "$S/download.sh" "$OUT/phone-recording.mov" phone > phone.log 2>&1; then
  "$PY" "$S/probe.py" agent-vision-runs/phone >/dev/null
  "$PY" - <<'PYEOF' && pass "phone recording (.mov, HEVC, VFR): normalised to CFR, all cuts on their frame" || fail "phone recording: cuts drifted after normalisation"
import json
t = json.load(open("truth.json"))["cuts"]
m = json.load(open("agent-vision-runs/phone/analysis/media.json"))
c = json.load(open("agent-vision-runs/phone/analysis/shots.json"))["cuts"]
assert m["fps"] == 30, m["fps"]
assert len(c) == len(t) and all(abs(a - b) <= 1 / 30 + 1e-3 for a, b in zip(c, t)), (c, t)
PYEOF
else
  fail "phone recording: $(cat phone.log)"
fi
# Same, as H.264 .mp4 — must not be copied through just because the codec fits.
ffmpeg -hide_banner -loglevel error -y -i www/clip.mp4 -vf "select='not(eq(mod(n\,5)\,2))'" -fps_mode vfr \
  -c:v libx264 -crf 18 -c:a aac phone-vfr.mp4
if bash "$S/download.sh" "$OUT/phone-vfr.mp4" phone-mp4 > phone-mp4.log 2>&1 && grep -q "variable frame rate" phone-mp4.log; then
  "$PY" "$S/probe.py" agent-vision-runs/phone-mp4 >/dev/null
  "$PY" -c "import json; m=json.load(open('agent-vision-runs/phone-mp4/analysis/media.json')); c=json.load(open('agent-vision-runs/phone-mp4/analysis/shots.json'))['cuts']; t=json.load(open('truth.json'))['cuts']; assert m['fps']==30 and len(c)==len(t) and all(abs(a-b)<=1/30+1e-3 for a,b in zip(c,t)), (m['fps'], c)" \
    && pass "VFR H.264 .mp4: detected as variable, normalised, cuts on their frame" || fail "VFR .mp4: cuts drifted"
else
  fail "VFR .mp4 not detected as variable: $(cat phone-mp4.log)"
fi

# ------------------------------------------------------------------ near-hit + decoy-kick fixture (#107, #108)
echo "== near hits and attack-snapped beats"
"$PY" - "$OUT" <<'PYEOF'
import subprocess, sys, wave
import numpy as np
out = sys.argv[1]; SR = 48000; DUR = 8.0
y = np.zeros(int(DUR * SR))
t = lambda sec: np.arange(int(sec * SR)) / SR
def add(at, x):
    i = int(round(at * SR)); y[i:i + len(x)] += x[: len(y) - i]
# Kicks on the 0.5 s grid: sharp attack to 30 %, then a ~70 ms rise to the peak (beat trackers mark the peak).
k = t(0.25); env = np.where(k < 0.001, k / 0.001 * 0.3, np.where(k < 0.07, 0.3 + 0.7 * (k - 0.001) / 0.069, np.exp(-(k - 0.07) / 0.06)))
kick = 0.9 * np.sin(2 * np.pi * 60 * k) * env
c = t(0.005); click = 0.12 * np.sin(2 * np.pi * 2000 * c)          # decoy: weak click 100 ms before each kick
for i in range(1, 16):
    add(i * 0.5, kick); add(i * 0.5 - 0.1, click)
b = t(0.04); beep = 0.8 * np.sin(2 * np.pi * 880 * b) * np.exp(-b / 0.02)
add(4.30 + 0.040, beep)                                              # near: 40 ms after the 4.30 s cut (off the 120 and 240 bpm grids) (hit tol 25 ms, near 50 ms at 60 fps)
pcm = (np.clip(y / np.abs(y).max() * 0.9, -1, 1) * 32767).astype(np.int16)
with wave.open(f"{out}/fx2.wav", "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
cuts = [0, 1.0, 2.0, 3.5, 4.30, 5.0, 6.5, DUR]
args, filt, lab = [], "", ""
for i in range(len(cuts) - 1):
    args += ["-f", "lavfi", "-i", f"testsrc2=s=360x640:r=60:d={cuts[i+1]-cuts[i]:.3f}"]
    filt += f"[{i}:v]hue=h={i*75}:s=1.6{',negate' if i % 2 else ''},setsar=1[v{i}];"; lab += f"[v{i}]"
n = len(cuts) - 1
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args, "-i", f"{out}/fx2.wav",
                "-filter_complex", f"{filt}{lab}concat=n={n}:v=1:a=0[v]", "-map", "[v]", "-map", f"{n}:a",
                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", f"{out}/fx2.mp4"], check=True)
PYEOF
if bash "$S/download.sh" "$OUT/fx2.mp4" fx2 > fx2.log 2>&1; then
  "$PY" "$S/probe.py" agent-vision-runs/fx2 >/dev/null && "$PY" "$S/audio.py" agent-vision-runs/fx2 >/dev/null && "$PY" "$S/align.py" agent-vision-runs/fx2 >/dev/null
  "$PY" "$S/selftest_check.py" near agent-vision-runs/fx2 || status=1
else
  fail "fx2 fixture: $(cat fx2.log)"
fi

# ------------------------------------------------------------------ brief gate
echo "== brief validation"
"$PY" "$S/selftest_check.py" brief "$RUN" "$OUT/agent-vision-runs/silent" || status=1

# ------------------------------------------------------------------ build + verify
if [ "${1:-}" = "--render" ]; then
  echo "== build, render, verify"
  bash "$S/new_build.sh" "$RUN" "$OUT/assets" > build.log 2>&1 && pass "new_build: template + assets + npm install" || { fail "new_build: $(tail -5 build.log)"; }
  "$PY" "$S/sfx.py" "$RUN/remotion/public/audio" --bpm 120 --duration "$DUR"
  "$PY" "$S/selftest_check.py" storyboard "$RUN"
  (cd "$RUN/remotion" && npx tsc --noEmit && npm run render --silent > ../render.log 2>&1) && pass "render: remotion/out/final.mp4" || fail "render: $(tail -20 "$RUN/render.log")"
  vdur=$(ffprobe -v error -select_streams v:0 -show_entries stream=duration -of csv=p=0 "$RUN/remotion/out/final.mp4")
  "$PY" -c "import sys; sys.exit(abs($vdur - $DUR) > 0.5/30)" && pass "render video duration $vdur s = $DUR s to the frame (apad mux keeps every frame)" || fail "render video duration $vdur s ≠ $DUR s"
  "$PY" "$S/verify.py" "$RUN" > verify.log 2>&1 && pass "verify: $(head -1 verify.log)" || fail "verify: $(cat verify.log)"
  grep -q "whoosh.*not sync-checked (not a transient)" verify.log && pass "verify: swell cue reported as not sync-checked, not passed" || fail "verify: swell cue not reported as unchecked"
  "$PY" -c "import json,sys; v=json.load(open('$RUN/remotion/out/verify.json')); b=[x for x in v['checks']['beat sync'] if x['pass'] is True and 'found' in x['detail']]; print('   ', len(b), 'transient cues found within 2 ms'); sys.exit(len(b) < 3)" && pass "verify: every hit cue located at sample level" || fail "verify: hit cues not located"
fi

[ $status -eq 0 ] && echo "SELFTEST PASS" || echo "SELFTEST FAIL"
exit $status
