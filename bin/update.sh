#!/bin/bash
# HamScanPi aktualisieren (Installation aus GitHub):  bin/update.sh
#   holt den neuen Stand (git pull), ergänzt die lokale Konfiguration und startet die Dienste neu.
#   Eigene Dateien (config/scanner.json, config/web_settings.json, config/auth.json, data/, recordings/ …)
#   bleiben unberührt.
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
[ -d .git ] || { echo "Kein Git-Repository in $DIR – bitte mit git clone installieren."; exit 1; }
OLD="$(cat VERSION 2>/dev/null || echo ?)"
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Es gibt lokale Änderungen an versionierten Dateien:"; git status --short | grep -v '^??' || true
    echo "Bitte sichern oder mit 'git stash' beiseitelegen, dann erneut starten."; exit 1
fi
git pull --ff-only
NEW="$(cat VERSION 2>/dev/null || echo ?)"
python3 bin/migrate_config.py
chmod +x bin/*.py bin/*.sh install.sh devtools/*.sh 2>/dev/null || true
UNITS=""
for u in hamscanpi-web hamscanpi-scheduler; do systemctl cat "$u" >/dev/null 2>&1 && UNITS="$UNITS $u"; done
if [ -n "$UNITS" ]; then
    sudo systemctl restart $UNITS
    sleep 2
    for u in $UNITS; do echo "  $u: $(systemctl is-active $u)"; done
fi
echo "HamScanPi $OLD → $NEW. Im Browser einmal neu laden (Strg+F5)."
