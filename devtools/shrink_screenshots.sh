#!/usr/bin/env bash
# Doku-Bilder verkleinern, bevor sie ins Repository kommen (git hebt jede Fassung für immer auf – auch auf jedem Pi).
# Braucht pngquant (sudo apt install pngquant oder pip install pngquant-cli). Ersetzt nur, wenn es mindestens 20 % spart.
#   bash devtools/shrink_screenshots.sh [datei.png …]     ohne Angabe: alle unter docs/screenshots
set -euo pipefail
cd "$(dirname "$0")/.."
command -v pngquant >/dev/null || { echo "pngquant fehlt (sudo apt install pngquant)"; exit 1; }
[ $# -gt 0 ] || set -- docs/screenshots/*.png
for f in "$@"; do
    t="$(mktemp --suffix=.png)"
    if pngquant --quality=75-95 --speed 1 --strip --force --output "$t" "$f" 2>/dev/null \
       && [ "$(stat -c %s "$t")" -lt $(( $(stat -c %s "$f") * 8 / 10 )) ]; then
        echo "$f: $(( $(stat -c %s "$f") / 1024 )) → $(( $(stat -c %s "$t") / 1024 )) KB"; mv "$t" "$f"
    else
        rm -f "$t"; echo "$f: bleibt ($(( $(stat -c %s "$f") / 1024 )) KB)"
    fi
done
