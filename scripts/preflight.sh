#!/usr/bin/env bash
# Checks every tool Agent Vision needs and names anything missing. Read-only:
# installs nothing, launches no browser, touches nothing outside this folder.
#   bash scripts/preflight.sh          exit 0 = ready, 1 = something required is missing
set -uo pipefail
SKILL="$(cd "$(dirname "$0")/.." && pwd)"
PY="$SKILL/.venv/bin/python"
missing=0
ok()   { printf '  ok    %-16s %s\n' "$1" "$2"; }
bad()  { printf '  MISS  %-16s %s\n' "$1" "$2"; missing=1; }
opt()  { printf '  --    %-16s %s\n' "$1" "$2"; }

echo "Agent Vision preflight"
for t in ffmpeg ffprobe; do
  if command -v "$t" >/dev/null; then ok "$t" "$("$t" -version | head -1 | cut -d' ' -f1-3)"; else bad "$t" "install: brew install ffmpeg"; fi
done
if [ -x "$PY" ]; then
  v=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' && ok "python venv" "$v ($SKILL/.venv)" || bad "python venv" "$v is older than 3.10"
  for mod in librosa numpy PIL soundfile yt_dlp jsonschema; do
    "$PY" -c "import $mod" 2>/dev/null && ok "$mod" "" || bad "$mod" "install: $PY -m pip install ${mod/PIL/pillow}"
  done
  if "$PY" -c "import mlx_whisper" 2>/dev/null; then ok "whisper" "mlx-whisper"
  elif "$PY" -c "import faster_whisper" 2>/dev/null; then ok "whisper" "faster-whisper"
  else bad "whisper" "install: $PY -m pip install mlx-whisper (Apple Silicon) or faster-whisper"; fi
else
  bad "python venv" "create: python3 -m venv $SKILL/.venv && $SKILL/.venv/bin/pip install librosa soundfile numpy pillow yt-dlp jsonschema mlx-whisper"
fi
if command -v node >/dev/null; then
  major=$(node -p 'process.versions.node.split(".")[0]')
  [ "$major" -ge 18 ] && ok "node" "$(node -v)" || bad "node" "$(node -v) is older than 18"
else bad "node" "install Node 18+ (needed for Remotion)"; fi
command -v npx >/dev/null && ok "npx" "" || bad "npx" "comes with Node"
# Downloads are anonymous by design. Warn if a stray cookie file is lying around:
# it is a live login, and nothing here uses it.
if [ -f "$SKILL/cookies.txt" ]; then
  printf '  WARN  %-16s %s\n' "cookies.txt" "found in the skill folder — it is NOT used; delete it (a leaked session cookie = account takeover)"
fi
[ -n "${GEMINI_API_KEY:-}" ] && opt "video model key" "set (optional cross-check available)" || opt "video model key" "not set (optional, never required)"
[ $missing -eq 0 ] && echo "ready" || echo "NOT READY — fix the MISS lines above"
exit $missing
