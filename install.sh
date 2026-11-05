#!/bin/bash
# HamScanPi – installation on a Raspberry Pi (Raspberry Pi OS / Debian).
# Run as your normal user from the cloned repository:
#   ./install.sh                 asks for the language (Deutsch / English), everything else runs on its own
#   ./install.sh --lang de       without any question;  --port 8090 for another port (rescue page: port + 1)
# Everything that is set up on the system is in bin/setup_system.sh (the same script runs on every update):
# packages, DVB-T driver blacklist, access rights for RTL-SDR sticks, configuration, ft8_lib, the services
# hamscanpi-web/-scheduler/-rescue/-mdns/-update, a sudo rule for the dashboard buttons, persistent system journal,
# Wi-Fi without power saving. Locator, callsign, password, notifications … are set later in the dashboard.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=8080; LNG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --lang) LNG="$2"; shift 2;;
        --port) PORT="$2"; shift 2;;
        [0-9]*) PORT="$1"; shift;;            # alte Schreibweise: ./install.sh 8090
        *) shift;;
    esac
done
[ "$(id -u)" = 0 ] && { echo "Bitte als normaler Benutzer starten, nicht als root / Please run as your normal user, not as root."; exit 1; }
command -v apt-get >/dev/null || { echo "Raspberry Pi OS / Debian nötig – needs Raspberry Pi OS / Debian."; exit 1; }

if [ -z "$LNG" ]; then
    case "${LANG:-}" in de*) DEF=1;; *) DEF=2;; esac
    echo
    echo "  HamScanPi"
    echo "  Sprache / Language:   [1] Deutsch   [2] English"
    read -r -p "  [$DEF] > " a || a=""
    case "${a:-$DEF}" in 1|d|D|de|DE) LNG=de;; *) LNG=en;; esac
fi
[ "$LNG" = de ] || LNG=en
t() { if [ "$LNG" = de ]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }
echo
echo "  $(t "Installation in $DIR – das dauert ein paar Minuten, es kommen keine weiteren Fragen." \
             "Installing into $DIR – this takes a few minutes, no further questions.")"
echo "  $(t "sudo fragt einmal nach deinem Passwort." "sudo asks for your password once.")"

# Sprache gleich fürs Dashboard festlegen (auch bei einer erneuten Installation)
[ -f "$DIR/config/web_settings.json" ] || cp "$DIR/config/web_settings.example.json" "$DIR/config/web_settings.json"
python3 - "$DIR/config/web_settings.json" "$LNG" <<'PY'
import json, sys
p, lng = sys.argv[1], sys.argv[2]
d = json.load(open(p, encoding="utf-8"))
d["ui_lang"] = lng
json.dump(d, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
PY

sudo env HSP_USER="$(id -un)" HSP_DIR="$DIR" bash "$DIR/bin/setup_system.sh" --install --port "$PORT" --lang "$LNG"

if ! python3 "$DIR/bin/auth.py" status 2>/dev/null | grep -q "aktiv"; then
    printf '  \033[33m!\033[0m %s\n' "$(t "Noch kein Passwort fürs Dashboard – im Dashboard unter Einstellungen → Zugangsschutz setzen (empfohlen)." \
                                        "No dashboard password yet – set one in the dashboard under Settings → Access protection (recommended).")"
fi
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ "$LNG" = de ]; then cat <<EOF

Nächste Schritte
  1. Den RTL-SDR-Stick einmal ab- und wieder anstecken (oder den Pi neu starten).
  2. Dashboard öffnen:  http://hamscanpi.local:$PORT/   oder   http://$IP:$PORT/
     (Android-Handys kennen .local oft nicht – dort die IP-Adresse nehmen)
  3. Einstellungen → Station: Locator (für Satelliten nötig), Rufzeichen.
     Einstellungen → Zugangsschutz: Passwort setzen (empfohlen).
     System → Geräte: den Stick zum Haupt-Stick machen (geschieht beim ersten Stick automatisch).
  4. Scan → Bänder wählen, „Jetzt scannen“. Satelliten: den Chip „Satelliten“ einschalten.
  Updates: im Dashboard unter System → Status („Jetzt aktualisieren“) oder  bin/update.sh
  Alles prüfen:  bash devtools/selftest.sh   (simulierte Signale, die Installation bleibt unberührt)
EOF
else cat <<EOF

Next steps
  1. Unplug the RTL-SDR stick and plug it in again (or reboot the Pi).
  2. Open the dashboard:  http://hamscanpi.local:$PORT/   or   http://$IP:$PORT/
     (Android phones often do not resolve .local – use the IP address there)
  3. Settings → Station: locator (needed for satellites), callsign.
     Settings → Access protection: set a password (recommended).
     System → Devices: make the stick the main stick (happens automatically for the first stick).
  4. Scan → choose the bands, "Scan now". Satellites: switch on the chip "Satellites".
  Updates: in the dashboard under System → Status ("Update now") or  bin/update.sh
  Check everything:  bash devtools/selftest.sh   (simulated signals, does not touch your installation)
EOF
fi
