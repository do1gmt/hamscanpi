#!/usr/bin/env bash
# Baut die selbstentpackende Update-Datei hamscanpi-update.sh aus diesem Arbeitsstand.
#   bash devtools/build_update.sh "Kurzbeschreibung"   → ./hamscanpi-update.sh
# Aufbau: devtools/update_head.sh (Installer-Logik) + base64(tar.gz des Repositorys ohne .git, data, log).
# Auf dem Pi: bash hamscanpi-update.sh  (sichert den alten Stand, übernimmt Einstellungen, startet Dienste neu,
# legt einen Git-Commit mit Tag v<VERSION> an und lädt ihn nach GitHub, sofern eingerichtet).
set -euo pipefail
cd "$(dirname "$0")/.."
MSG="${1:-HamScanPi $(cat VERSION)}"
OUT="${OUT:-$PWD/hamscanpi-update.sh}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
tar czf "$TMP/payload.tgz" --exclude='./.git' --exclude='./data' --exclude='./log' --exclude='./recordings' \
    --exclude='./reports' --exclude='./restore-previous' --exclude='./backup' --exclude='./hamscanpi-update.sh' --exclude='./config/auth.json' \
    --exclude='./config/scanner.json' --exclude='./config/web_settings.json' .
sed "0,/^set -e\$/s//set -e\nHSP_MSG=\"${MSG//\//\\/}\"/" devtools/update_head.sh > "$OUT"
base64 "$TMP/payload.tgz" >> "$OUT"
bash -n "$OUT"
echo "gebaut: $OUT  (Version $(cat VERSION), $(du -h "$OUT" | cut -f1))"
