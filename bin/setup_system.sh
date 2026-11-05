#!/bin/bash
# HamScanPi – Einrichtung des Systems. EINE Stelle für alles, was am System eingerichtet wird; aufgerufen von
# install.sh (Erstinstallation), bin/update.sh (Update per git, auch über den Knopf im Dashboard).
# Mehrfach aufrufbar: richtet nur ein, was fehlt oder sich geändert hat.
#
#   sudo HSP_USER=<benutzer> bash bin/setup_system.sh [--port 8080] [--lang de|en] [--install] [--restart]
#
#   --install   Erstinstallation: apt-get update, Paketliste vollständig
#   --restart   Dienste am Ende neu starten (Update)
#   --lang      Sprache der Meldungen und des Dashboards (Vorgabe: aus den Einstellungen, sonst Systemsprache)
#
# Was eingerichtet wird:
#   Pakete, Sperre des DVB-T-Treibers, Zugriffsrechte für RTL-SDR-Sticks (udev), Konfigurationsdateien und
#   deren Ergänzung (bin/migrate_config.py), ft8_lib, Dienste hamscanpi-web/-scheduler/-rescue/-mdns/-update,
#   sudo-Regel für die Knöpfe im Dashboard, dauerhaftes Systemprotokoll, WLAN ohne Stromsparen,
#   PMSDR-Dienst (wenn dessen Programm vorhanden ist).
set -e
[ "$(id -u)" = 0 ] || { echo "setup_system.sh: bitte mit sudo aufrufen (sudo HSP_USER=\$USER bash bin/setup_system.sh)"; exit 1; }
DIR="${HSP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
U="${HSP_USER:-${SUDO_USER:-}}"
[ -n "$U" ] && [ "$U" != root ] || { echo "setup_system.sh: HSP_USER fehlt (der Benutzer, unter dem HamScanPi läuft)"; exit 1; }
UHOME="$(getent passwd "$U" | cut -d: -f6)"
PORT=8080; LNG=""; INSTALL=0; RESTART=0
while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="$2"; shift 2;;
        --lang) LNG="$2"; shift 2;;
        --install) INSTALL=1; shift;;
        --restart) RESTART=1; shift;;
        *) shift;;
    esac
done
# Port der bestehenden Installation übernehmen
if [ "$PORT" = 8080 ] && [ -f /etc/systemd/system/hamscanpi-web.service ]; then
    P="$(sed -nE 's#^ExecStart=.*webapp\.py ([0-9]+).*#\1#p' /etc/systemd/system/hamscanpi-web.service | head -1)"
    [ -n "$P" ] && PORT="$P"
fi
# Sprache: Parameter > Einstellung im Dashboard > Systemsprache
if [ -z "$LNG" ]; then
    LNG="$(python3 -c "import json;print(json.load(open('$DIR/config/web_settings.json')).get('ui_lang') or '')" 2>/dev/null || true)"
fi
if [ -z "$LNG" ]; then case "${LANG:-}" in de*) LNG=de;; *) LNG=en;; esac; fi
[ "$LNG" = de ] || LNG=en
t() { if [ "$LNG" = de ]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }
if [ -t 1 ]; then                                  # Farben nur im Terminal, nicht im Protokoll/Dashboard
    bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
    ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
    warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
else
    bold() { printf '\n== %s\n' "$*"; }
    ok()   { printf '  ✓ %s\n' "$*"; }
    warn() { printf '  ! %s\n' "$*"; }
fi
asuser() { runuser -u "$U" -- "$@"; }

bold "$(t "Pakete" "Packages")"
PKGS="python3 python3-numpy python3-scipy python3-matplotlib python3-pil python3-soundfile python3-sgp4 rtl-sdr
      multimon-ng sqlite3 alsa-utils util-linux curl git avahi-daemon avahi-utils build-essential iw"
NEED=""
for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || NEED="$NEED $p"; done
if [ -n "$NEED" ]; then
    [ "$INSTALL" = 1 ] && apt-get update -q
    if ! DEBIAN_FRONTEND=noninteractive apt-get install -y -q $NEED >/dev/null; then
        apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q $NEED >/dev/null
    fi
    ok "$(t "installiert:" "installed:")$NEED"
else
    ok "$(t "alle Pakete vorhanden" "all packages present")"
fi

bold "$(t "RTL-SDR-Stick" "RTL-SDR stick")"
mkdir -p /etc/modprobe.d /etc/udev/rules.d /etc/systemd/system /etc/sudoers.d
if ! grep -rqs "dvb_usb_rtl28xxu" /etc/modprobe.d/; then
    echo "blacklist dvb_usb_rtl28xxu" > /etc/modprobe.d/blacklist-rtl-sdr.conf
    modprobe -r dvb_usb_rtl28xxu 2>/dev/null || true
    ok "$(t "DVB-T-Treiber gesperrt" "DVB-T driver blacklisted")"
fi
# Zugriffsrechte: ohne diese Regel meldet rtl_sdr „usb_open error -3“ (die Regel des Pakets gibt einem Dienst
# ohne Anmeldung am Bildschirm je nach Version keinen Zugriff)
RULE=/etc/udev/rules.d/20-hamscanpi-rtlsdr.rules
NEWRULE='# HamScanPi: RTL-SDR-Sticks (RTL2832U) für alle Benutzer – rtl_sdr läuft als Dienst ohne Bildschirm-Anmeldung
SUBSYSTEMS=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666"
SUBSYSTEMS=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE="0666"'
if [ "$(cat "$RULE" 2>/dev/null)" != "$NEWRULE" ]; then
    printf '%s\n' "$NEWRULE" > "$RULE"
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=usb --attr-match=idVendor=0bda --action=add 2>/dev/null || true
    ok "$(t "Zugriffsrechte für den Stick eingerichtet (falls er nicht reagiert: einmal ab- und wieder anstecken)" \
            "access rights for the stick set up (if it does not respond: unplug and plug in once)")"
else
    ok "$(t "Zugriffsrechte vorhanden" "access rights present")"
fi

bold "$(t "Konfiguration" "Configuration")"
asuser mkdir -p "$DIR/data" "$DIR/log" "$DIR/recordings" "$DIR/reports" "$DIR/backup"
[ -f "$DIR/config/scanner.json" ] || asuser cp "$DIR/config/scanner.default.json" "$DIR/config/scanner.json"
[ -f "$DIR/config/web_settings.json" ] || asuser cp "$DIR/config/web_settings.example.json" "$DIR/config/web_settings.json"
chmod 600 "$DIR/config/web_settings.json"
chmod +x "$DIR"/bin/*.py "$DIR"/bin/*.sh "$DIR"/install.sh 2>/dev/null || true
[ -d "$DIR/.git" ] && asuser git -C "$DIR" config core.fileMode false    # Rechte zählen nicht als lokale Änderung
# Sprache des Dashboards (nur bei ausdrücklicher Wahl oder wenn noch keine eingestellt ist)
asuser python3 - "$DIR/config/web_settings.json" "$LNG" <<'PY'
import json, sys
p, lng = sys.argv[1], sys.argv[2]
d = json.load(open(p, encoding="utf-8"))
if not d.get("ui_lang"):
    d["ui_lang"] = lng
    json.dump(d, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
PY
( cd "$DIR" && asuser python3 bin/migrate_config.py | sed 's/^/  /' ) || warn "migrate_config.py"

bold "$(t "FT8/FT4-Dekoder (ft8_lib)" "FT8/FT4 decoder (ft8_lib)")"
if [ -x "$UHOME/ft8_lib/decode_ft8" ]; then
    ok "$(t "vorhanden" "present")"
else
    rm -rf "$UHOME/ft8_lib"
    if asuser git clone -q --depth 1 https://github.com/kgoba/ft8_lib "$UHOME/ft8_lib" \
            && asuser make -s -C "$UHOME/ft8_lib" >/dev/null 2>&1 && [ -x "$UHOME/ft8_lib/decode_ft8" ]; then
        ok "$(t "gebaut" "built"): $UHOME/ft8_lib/decode_ft8"
    else
        warn "$(t "ft8_lib ließ sich nicht bauen (kein Internet?) – FT8/FT4 wird nicht dekodiert; später erneut" \
                  "ft8_lib could not be built (no internet?) – FT8/FT4 is not decoded; try again later")"
    fi
fi

bold "$(t "Dienste" "Services")"
unit() {   # unit <name> <beschreibung> <ExecStart> [zusätzliche Zeilen für [Service]]
    cat > "/etc/systemd/system/$1.service" <<UNIT
[Unit]
Description=$2
After=network-online.target
Wants=network-online.target

[Service]
User=$U
WorkingDirectory=$DIR
ExecStart=$3
Restart=always
RestartSec=3
$4

[Install]
WantedBy=multi-user.target
UNIT
}
unit hamscanpi-web "HamScanPi web dashboard" "/usr/bin/python3 $DIR/bin/webapp.py $PORT" \
"# Wächter: der Webserver prüft sich alle 30 s selbst; antwortet er 3 min nicht, startet systemd ihn neu
NotifyAccess=main
WatchdogSec=180
# Speichergrenze: ein Ausreißer trifft nur den Webserver (Neustart) statt den ganzen Pi
MemoryMax=500M"
unit hamscanpi-scheduler "HamScanPi scheduler (scans and satellite passes)" "/usr/bin/python3 $DIR/bin/scheduler.py" \
"# Wächter: Meldung bei jedem Durchlauf (10 s); hängt die Schleife 5 min, Neustart
NotifyAccess=main
WatchdogSec=300
# Scans, Überflüge und Auswertung laufen in dieser Gruppe: bei Speichermangel trifft es den größten Prozess
# (meist eine Auswertung), der Zeitplaner selbst läuft weiter
MemoryMax=750M
OOMPolicy=continue"
unit hamscanpi-rescue "HamScanPi rescue page (restart services, reboot, diagnostics)" \
     "/usr/bin/python3 $DIR/bin/rescue.py $((PORT + 1))" "MemoryMax=120M"
unit hamscanpi-mdns "HamScanPi name in the network (hamscanpi.local)" "/usr/bin/python3 $DIR/bin/mdns_alias.py $PORT" \
     "MemoryMax=60M"
# Update (Knopf im Dashboard): läuft als root, holt den neuen Stand als $U und richtet dann das System ein
cat > /etc/systemd/system/hamscanpi-update.service <<UNIT
[Unit]
Description=HamScanPi update from GitHub (git pull, set-up, restart)
After=network-online.target

[Service]
Type=oneshot
Environment=HSP_USER=$U
ExecStart=/bin/bash $DIR/bin/update.sh --auto
TimeoutStartSec=3600
UNIT
ok "$(t "hamscanpi-web (Port $PORT), -scheduler, -rescue (Port $((PORT + 1))), -mdns, -update" \
        "hamscanpi-web (port $PORT), -scheduler, -rescue (port $((PORT + 1))), -mdns, -update")"

# sudo-Regel: genau diese Befehle ohne Passwort (Knöpfe im Dashboard und auf der Notfall-Seite)
SC="$(command -v systemctl || echo /usr/bin/systemctl)"
TMPS="$(mktemp)"
printf '# HamScanPi: Dienste neu starten, Update starten, Pi neu starten - nur diese Befehle\n%s ALL=(root) NOPASSWD: %s --no-block restart hamscanpi-web, %s --no-block restart hamscanpi-scheduler, %s --no-block restart hamscanpi-rescue, %s --no-block restart hamscanpi-mdns, %s --no-block start hamscanpi-update, %s reboot\n' \
    "$U" "$SC" "$SC" "$SC" "$SC" "$SC" "$SC" > "$TMPS"
if visudo -cf "$TMPS" >/dev/null 2>&1; then
    install -m 0440 -o root -g root "$TMPS" /etc/sudoers.d/hamscanpi
    ok "$(t "sudo-Regel für die Knöpfe im Dashboard" "sudo rule for the dashboard buttons")"
else
    warn "$(t "sudo-Regel ungültig – Knöpfe bleiben gesperrt" "sudo rule invalid – buttons stay locked")"
fi
rm -f "$TMPS"

# PMSDR (nur wenn dessen Steuerprogramm vorhanden ist)
if [ -x /usr/local/bin/pmsdr ] && ! systemctl cat pmsdr-control.service >/dev/null 2>&1; then
    cat > /etc/systemd/system/pmsdr-control.service <<UNIT
[Unit]
Description=PMSDR Control FIFO
After=network.target

[Service]
Type=simple
User=$U
ExecStart=/usr/local/bin/pmsdr -u /tmp/PMSDRcommands
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT
    mkdir -p /etc/systemd/system/pmsdr-control.service.d
    printf '[Unit]\nBindsTo=sys-subsystem-usb-pmsdr.device\nAfter=sys-subsystem-usb-pmsdr.device\n\n[Service]\nRestartSec=5\n' \
        > /etc/systemd/system/pmsdr-control.service.d/hotplug.conf
    echo 'SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="04d8", ATTR{idProduct}=="000c", MODE="0666", TAG+="systemd", ENV{SYSTEMD_ALIAS}="/sys/subsystem/usb/pmsdr", ENV{SYSTEMD_WANTS}+="pmsdr-control.service"' \
        > /etc/udev/rules.d/90-hamscanpi-pmsdr.rules
    udevadm control --reload-rules; udevadm trigger --subsystem-match=usb --attr-match=idVendor=04d8 --action=add 2>/dev/null || true
    ok "$(t "PMSDR-Dienst eingerichtet (startet beim Anstecken)" "PMSDR service set up (starts when plugged in)")"
fi

bold "$(t "System" "System")"
# Systemprotokoll über Neustarts behalten (höchstens 64 MB, gebündelt geschrieben)
if [ ! -f /etc/systemd/journald.conf.d/hamscanpi.conf ]; then
    mkdir -p /etc/systemd/journald.conf.d /var/log/journal
    printf '[Journal]\nStorage=persistent\nSystemMaxUse=64M\nSyncIntervalSec=5m\n' > /etc/systemd/journald.conf.d/hamscanpi.conf
    systemctl restart systemd-journald 2>/dev/null || true
fi
ok "$(t "Systemprotokoll bleibt über Neustarts erhalten (höchstens 64 MB)" "system journal kept across reboots (max. 64 MB)")"
# WLAN ohne Stromsparen (sonst ruckelt Live und das Dashboard meldet „Server nicht erreichbar“)
if command -v nmcli >/dev/null 2>&1 && [ ! -f /etc/NetworkManager/conf.d/hamscanpi-wifi.conf ]; then
    mkdir -p /etc/NetworkManager/conf.d
    printf '[connection]\nwifi.powersave = 2\n' > /etc/NetworkManager/conf.d/hamscanpi-wifi.conf
fi
for w in $(ls /sys/class/net 2>/dev/null | grep '^wl'); do iw dev "$w" set power_save off 2>/dev/null || true; done
ok "$(t "WLAN-Stromsparen aus" "Wi-Fi power saving off")"

systemctl daemon-reload
systemctl enable --now avahi-daemon >/dev/null 2>&1 || true
systemctl enable hamscanpi-web hamscanpi-scheduler hamscanpi-rescue hamscanpi-mdns >/dev/null 2>&1
if [ "$RESTART" = 1 ]; then
    systemctl restart hamscanpi-web hamscanpi-scheduler hamscanpi-rescue hamscanpi-mdns
else
    systemctl start hamscanpi-web hamscanpi-scheduler hamscanpi-rescue hamscanpi-mdns
fi
sleep 3
for u in hamscanpi-web hamscanpi-scheduler hamscanpi-rescue hamscanpi-mdns; do echo "  $u: $(systemctl is-active $u)"; done
for i in 1 2 3 4 5 6 7 8 9 10; do curl -fs -m 3 "http://127.0.0.1:$PORT/api/ping" >/dev/null 2>&1 && break; sleep 1; done
if curl -fs -m 3 "http://127.0.0.1:$PORT/api/ping" >/dev/null 2>&1; then
    ok "$(t "Dashboard antwortet auf Port $PORT" "dashboard answers on port $PORT")"
else
    warn "$(t "Dashboard antwortet noch nicht – journalctl -u hamscanpi-web -n 30" "dashboard does not answer yet – journalctl -u hamscanpi-web -n 30")"
fi
