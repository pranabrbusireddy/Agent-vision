#!/usr/bin/env bash
# Phase 5 setup: copy the Remotion template into <run>/remotion, bring in the
# user's ./assets, and install dependencies there (nothing global).
#
#   bash scripts/new_build.sh <run-dir> [assets-dir]     (assets default: ./assets)
#
# Remotion downloads its own headless browser into the project's node_modules
# on first render; it never uses or touches an installed Chrome.
set -euo pipefail
SKILL="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$(cd "${1:?usage: new_build.sh <run-dir> [assets-dir]}" && pwd)"
ASSETS="${2:-$PWD/assets}"
DEST="$RUN/remotion"

[ -e "$DEST/package.json" ] && { echo "already set up: $DEST"; exit 0; }
mkdir -p "$DEST"
cp -R "$SKILL/templates/remotion/." "$DEST/"
mkdir -p "$DEST/public/assets" "$DEST/public/audio" "$DEST/out"
if [ -d "$ASSETS" ]; then
  cp -R "$ASSETS/." "$DEST/public/assets/"
  echo "assets: $(find "$DEST/public/assets" -type f | wc -l | tr -d ' ') file(s) from $ASSETS"
else
  echo "assets: none found at $ASSETS — the storyboard must use text/shape layers or you add files to public/assets"
fi
(cd "$DEST" && npm install --no-audit --no-fund --loglevel=error)
echo "$DEST"
