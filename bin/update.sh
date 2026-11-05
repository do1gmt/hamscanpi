#!/bin/bash
# HamScanPi aktualisieren (Installation aus GitHub):
#   bin/update.sh            von Hand (SSH): wartet auf ein freies Fenster, holt den neuen Stand (git pull),
#                            richtet das System ein (bin/setup_system.sh) und startet die Dienste neu
#   bin/update.sh --now      ohne auf ein freies Fenster zu warten
#   bin/update.sh --auto     vom Knopf im Dashboard (Dienst hamscanpi-update, läuft als root)
# Eigene Dateien (config/scanner.json, config/web_settings.json, config/auth.json, data/, recordings/ …)
# bleiben unberührt. Ausgabe auch in log/update.log (das Dashboard zeigt sie an).
#
# Das Skript kopiert sich vor dem Start nach /tmp: git pull ersetzt diese Datei, und bash liest Skripte
# stückweise – ohne Kopie liefe sonst ein Mischmasch aus alter und neuer Fassung.
if [ -z "${HSP_UPDATE_COPY:-}" ]; then
    C="$(mktemp /tmp/hamscanpi-update.XXXXXX.sh)"; cp "$0" "$C"
    HSP_UPDATE_COPY=1 HSP_DIR="$(cd "$(dirname "$0")/.." && pwd)" exec bash "$C" "$@"
fi
set -e
DIR="${HSP_DIR}"
cd "$DIR"
AUTO=0; NOW=0
for a in "$@"; do case "$a" in --auto) AUTO=1;; --now) NOW=1;; esac; done
if [ "$(id -u)" = 0 ]; then
    U="${HSP_USER:-$(stat -c %U "$DIR")}"
    asuser() { runuser -u "$U" -- "$@"; }
    SUDO=""
else
    U="$(id -un)"
    asuser() { "$@"; }
    SUDO="sudo"
fi
LOG="$DIR/log/update.log"
asuser mkdir -p "$DIR/log"
[ "$AUTO" = 1 ] && : > "$LOG" && chown "$U" "$LOG" 2>/dev/null
exec > >(tee -a "$LOG") 2>&1
LNG="$(python3 -c "import json;print(json.load(open('config/web_settings.json')).get('ui_lang') or '')" 2>/dev/null || true)"
[ -n "$LNG" ] || case "${LANG:-}" in de*) LNG=de;; *) LNG=en;; esac
t() { if [ "$LNG" = de ]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }
applog() { asuser python3 bin/applog.py "$@" >/dev/null 2>&1 || true; }

echo "== $(date '+%Y-%m-%d %H:%M:%S') HamScanPi update"
[ -d .git ] || { echo "$(t "Kein Git-Repository in $DIR – bitte mit git clone installieren." "No git repository in $DIR – please install with git clone.")"; exit 1; }
OLD="$(cat VERSION 2>/dev/null || echo ?)"
# Dateirechte (chmod +x bei der Einrichtung) sind keine lokalen Änderungen – sonst blockierten sie jedes Update
asuser git config core.fileMode false
if ! asuser git diff --quiet || ! asuser git diff --cached --quiet; then
    echo "$(t "Lokale Änderungen an versionierten Dateien:" "Local changes to versioned files:")"
    asuser git status --short | grep -v '^??' || true
    echo "$(t "Bitte sichern oder mit 'git stash' beiseitelegen, dann erneut starten." "Please save them or put them aside with 'git stash', then start again.")"
    applog WARN update update_failed reason=local_changes
    exit 1
fi

# Nicht mitten in einen Scan oder Überflug: warten, bis keine Aufnahme läuft und in den nächsten 3 min keine
# beginnt (bin/update_window.py; Aufnahmen „wenn Stick frei“ zählen nicht)
if [ "$NOW" = 0 ] && [ -f bin/update_window.py ]; then
    first=1
    while :; do
        W="$(HSP_BASE="$DIR" asuser python3 bin/update_window.py 2>/dev/null)" || break
        S="${W%% *}"; R="${W#* }"
        if [ "$S" = STOP ]; then
            P="${R%% *}"; R="${R#* }"
            echo "  $(date +%H:%M) $R – $(t "wird beendet, das Aufgenommene wird noch ausgewertet" "is being ended, what was recorded is still evaluated")"
            kill -TERM "$P" 2>/dev/null || $SUDO kill -TERM "$P" 2>/dev/null || true
            sleep 15; continue
        fi
        case "$S" in ''|*[!0-9]*) break;; esac
        [ "$S" -gt 0 ] || break
        [ "$first" = 1 ] && echo "$(t "Warten auf ein freies Fenster" "Waiting for a free window")" && first=0
        echo "  $(date +%H:%M) $R – $(t "frei ab etwa" "free from about") $(date -d "@$(( $(date +%s) + S ))" +%H:%M)"
        sleep $(( S < 300 ? S : 300 ))
    done
fi

# laufenden Scanlauf zu Ende laufen lassen (höchstens 60 min)
if [ "$NOW" = 0 ] && [ -f data/cycle.lock ]; then
    for i in $(seq 1 60); do
        flock -n data/cycle.lock true 2>/dev/null && break
        [ "$i" = 1 ] && echo "$(t "Ein Scan läuft – das Update wartet, bis er fertig ist" "A scan is running – the update waits until it has finished")"
        sleep 60
    done
fi
# Zeitplaner anhalten, damit zwischen Prüfung und Neustart kein neuer Scan beginnt (die Einrichtung startet ihn
# am Ende wieder) – sonst bricht der Neustart diesen Scan ab
if systemctl is-active -q hamscanpi-scheduler 2>/dev/null && [ -z "${HSP_SETUP_CMD:-}" ]; then
    $SUDO systemctl stop hamscanpi-scheduler 2>/dev/null || true
fi
echo "$(t "Neuer Stand von GitHub …" "Fetching the new version from GitHub …")"
asuser git pull --ff-only
NEW="$(cat VERSION 2>/dev/null || echo ?)"
echo "  $OLD → $NEW"
if [ -n "${HSP_SETUP_CMD:-}" ]; then            # nur für den Selbsttest: Einrichtung nicht wirklich ausführen
    $HSP_SETUP_CMD setup_system.sh --restart --lang "$LNG"
else
    $SUDO env HSP_USER="$U" HSP_DIR="$DIR" bash "$DIR/bin/setup_system.sh" --restart --lang "$LNG"
fi
if [ "$OLD" != "$NEW" ]; then
    MSG="$(asuser git log -1 --format=%s 2>/dev/null || true)"
    asuser python3 - "$NEW" "$MSG" <<'PY' >/dev/null 2>&1 || true
import sys
sys.path.insert(0, "bin")
import notices
notices.event("update", "info", "software", f"Update eingespielt: {sys.argv[1]}", sys.argv[2] if len(sys.argv) > 2 else "")
PY
fi
applog INFO update update_done old="$OLD" new="$NEW"
echo "== $(t "fertig" "done"): HamScanPi $OLD → $NEW. $(t "Im Browser einmal neu laden (Strg+F5)." "Reload the page in the browser once (Ctrl+F5).")"
