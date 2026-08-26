#!/usr/bin/env bash
# Fetch the full Noto Sans Regular glyph set (all 256 unicode ranges) from the Protomaps
# basemaps-assets repo into web/fonts/. The repo ships with a Latin/Cyrillic/Greek subset
# so labels render offline out of the box; run this once if you want CJK/Arabic/Thai etc.
set -euo pipefail
cd "$(dirname "$0")/.."
FONT="Noto Sans Regular"
DIR="web/fonts/$FONT"
BASE="https://raw.githubusercontent.com/protomaps/basemaps-assets/main/fonts/Noto%20Sans%20Regular"
mkdir -p "$DIR"
for ((i = 0; i < 65536; i += 256)); do
  r="$i-$((i + 255))"
  [[ -s "$DIR/$r.pbf" ]] && continue
  curl -sfL -o "$DIR/$r.pbf" "$BASE/$r.pbf" && echo "$r" || rm -f "$DIR/$r.pbf"
done
echo "done: $(ls "$DIR" | wc -l) ranges"
