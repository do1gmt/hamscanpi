#!/usr/bin/env bash
# Baut die selbstentpackende Datei hamscanpi-publish.sh aus diesem Arbeitsstand (Veröffentlichen auf GitHub vom PC).
#   bash devtools/build_publish.sh "Kurzbeschreibung"   → ./hamscanpi-publish.sh
# Aufbau: devtools/publish_head.sh + base64(tar.gz des Repositorys ohne .git und ohne persönliche/Laufzeitdaten).
set -euo pipefail
cd "$(dirname "$0")/.."
MSG="${1:-HamScanPi $(cat VERSION)}"
OUT="${OUT:-$PWD/hamscanpi-publish.sh}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
tar czf "$TMP/payload.tgz" --exclude='./.git' --exclude='./data' --exclude='./log' --exclude='./recordings' \
    --exclude='./reports' --exclude='./restore-previous' --exclude='./backup' --exclude='./hamscanpi-update.sh' \
    --exclude='./hamscanpi-publish.sh' --exclude='./config/auth.json' --exclude='./config/scanner.json' \
    --exclude='./config/web_settings.json' --exclude='*.bak*' --exclude='./fake_rtl_hf.*' .
{ sed "0,/^set -e\$/s//set -e\nHSP_MSG=\"${MSG//\//\\/}\"/" devtools/publish_head.sh; base64 "$TMP/payload.tgz"; } > "$OUT"
bash -n "$OUT"
echo "gebaut: $OUT  (Version $(cat VERSION), $(du -h "$OUT" | cut -f1))"
