#!/bin/bash
# Hesperos DX – Einrichtung des Systems. EINE Stelle für alles, was am System eingerichtet wird; aufgerufen von
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
#   deren Ergänzung (bin/migrate_config.py), ft8_lib, Dienste hesperosdx-web/-scheduler/-rescue/-mdns/-update
#   (bis 1.8.14 hamscanpi-*: Übergang in bin/migrate_services.sh),
#   sudo-Regel für die Knöpfe im Dashboard, dauerhaftes Systemprotokoll, WLAN ohne Stromsparen,
#   PMSDR-Dienst (wenn dessen Programm vorhanden ist).
set -e
[ "$(id -u)" = 0 ] || { echo "setup_system.sh: bitte mit sudo aufrufen (sudo HSP_USER=\$USER bash bin/setup_system.sh)"; exit 1; }
DIR="${HSP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
U="${HSP_USER:-${SUDO_USER:-}}"
[ -n "$U" ] && [ "$U" != root ] || { echo "setup_system.sh: HSP_USER fehlt (der Benutzer, unter dem Hesperos DX läuft)"; exit 1; }
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
for f in /etc/systemd/system/hesperosdx-web.service /etc/systemd/system/hamscanpi-web.service; do
    if [ "$PORT" = 8080 ] && [ -f "$f" ]; then
        P="$(sed -nE 's#^ExecStart=.*webapp\.py ([0-9]+).*#\1#p' "$f" | head -1)"
        [ -n "$P" ] && PORT="$P" && break
    fi
done
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
# Dienste und Systemdateien heißen seit 1.8.15 hesperosdx-* (vorher hamscanpi-*): Übergang
. "$DIR/bin/migrate_services.sh"
migrate_files

bold "$(t "Pakete" "Packages")"
PKGS="python3 python3-numpy python3-scipy python3-matplotlib python3-pil python3-soundfile python3-sgp4 rtl-sdr
      multimon-ng sqlite3 alsa-utils util-linux curl git avahi-daemon avahi-utils build-essential iw
      libusb-1.0-0-dev libreadline-dev cifs-utils ntfs-3g"
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
RULE=/etc/udev/rules.d/20-hesperosdx-rtlsdr.rules
NEWRULE='# Hesperos DX: RTL-SDR-Sticks (RTL2832U) für alle Benutzer – rtl_sdr läuft als Dienst ohne Bildschirm-Anmeldung
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
# Das GitHub-Repository heißt seit 1.8.08 „hesperos-dx“ (vorher „hamscanpi“). GitHub leitet die alte Adresse um;
# die Arbeitskopie bekommt trotzdem die neue – aber nur, wenn es sie unter demselben Konto wirklich gibt (eine Kopie
# unter altem Namen bei einem anderen Konto bleibt, wie sie ist). Adressen mit Zugangsdaten werden nicht angefasst.
if [ -d "$DIR/.git" ]; then
    OU="$(asuser git -C "$DIR" remote get-url origin 2>/dev/null || true)"
    NU="$(printf '%s' "$OU" | sed -nE 's#^((https://|ssh://git@|git@)github\.com[:/][A-Za-z0-9_.-]+/)hamscanpi(\.git)?/?$#\1hesperos-dx\3#p')"
    if [ -n "$NU" ]; then
        if GIT_TERMINAL_PROMPT=0 timeout 30 runuser -u "$U" -- git -C "$DIR" ls-remote -q --exit-code "$NU" HEAD >/dev/null 2>&1 \
           && asuser git -C "$DIR" remote set-url origin "$NU"; then
            ok "$(t "GitHub-Adresse umgestellt" "GitHub address changed"): $NU"
            asuser python3 "$DIR/bin/applog.py" INFO update repo_url_changed old="$OU" new="$NU" >/dev/null 2>&1 || true
        else
            warn "$(t "GitHub-Adresse bleibt vorerst" "GitHub address stays for now"): $OU"
            asuser python3 "$DIR/bin/applog.py" WARN update repo_url_kept url="$OU" >/dev/null 2>&1 || true
        fi
    fi
fi
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
unit hesperosdx-web "Hesperos DX web dashboard" "/usr/bin/python3 $DIR/bin/webapp.py $PORT" \
"# Wächter: der Webserver prüft sich alle 30 s selbst; antwortet er 3 min nicht, startet systemd ihn neu
NotifyAccess=main
WatchdogSec=180
# Speichergrenze: ein Ausreißer trifft nur den Webserver (Neustart) statt den ganzen Pi
MemoryMax=500M"
unit hesperosdx-scheduler "Hesperos DX scheduler (scans and satellite passes)" "/usr/bin/python3 $DIR/bin/scheduler.py" \
"# Wächter: Meldung bei jedem Durchlauf (10 s); hängt die Schleife 5 min, Neustart
NotifyAccess=main
WatchdogSec=300
# Scans, Überflüge und Auswertung laufen in dieser Gruppe: bei Speichermangel trifft es den größten Prozess
# (meist eine Auswertung), der Zeitplaner selbst läuft weiter
MemoryMax=750M
OOMPolicy=continue"
unit hesperosdx-rescue "Hesperos DX rescue page (restart services, reboot, diagnostics)" \
     "/usr/bin/python3 $DIR/bin/rescue.py $((PORT + 1))" "MemoryMax=120M"
unit hesperosdx-mdns "Hesperos DX name in the network (hamscanpi.local)" "/usr/bin/python3 $DIR/bin/mdns_alias.py $PORT" \
     "MemoryMax=60M"
# Update (Knopf im Dashboard): läuft als root, holt den neuen Stand als $U und richtet dann das System ein
cat > /etc/systemd/system/hesperosdx-update.service <<UNIT
[Unit]
Description=Hesperos DX update from GitHub (git pull, set-up, restart)
After=network-online.target

[Service]
Type=oneshot
Environment=HSP_USER=$U
ExecStart=/bin/bash $DIR/bin/update.sh --auto
TimeoutStartSec=3600
UNIT
ok "$(t "hesperosdx-web (Port $PORT), -scheduler, -rescue (Port $((PORT + 1))), -mdns, -update" \
        "hesperosdx-web (port $PORT), -scheduler, -rescue (port $((PORT + 1))), -mdns, -update")"

# sudo-Regel: genau diese Befehle ohne Passwort (Knöpfe im Dashboard und auf der Notfall-Seite)
SC="$(command -v systemctl || echo /usr/bin/systemctl)"
TMPS="$(mktemp)"
printf '# Hesperos DX: Dienste neu starten, Update starten, Pi neu starten - nur diese Befehle\n%s ALL=(root) NOPASSWD: %s --no-block restart hesperosdx-web, %s --no-block restart hesperosdx-scheduler, %s --no-block restart hesperosdx-rescue, %s --no-block restart hesperosdx-mdns, %s --no-block restart pmsdr-control, %s --no-block start hesperosdx-update, %s reboot, /usr/local/sbin/hesperosdx-net, /usr/local/sbin/hesperosdx-store\n' \
    "$U" "$SC" "$SC" "$SC" "$SC" "$SC" "$SC" "$SC" > "$TMPS"
if visudo -cf "$TMPS" >/dev/null 2>&1; then
    install -m 0440 -o root -g root "$TMPS" /etc/sudoers.d/hesperosdx
    ok "$(t "sudo-Regel für die Knöpfe im Dashboard" "sudo rule for the dashboard buttons")"
else
    warn "$(t "sudo-Regel ungültig – Knöpfe bleiben gesperrt" "sudo rule invalid – buttons stay locked")"
fi
rm -f "$TMPS"

# PMSDR: Steuerprogramm aus den beigelegten Quellen (GPL-3, share/pmsdr) bauen, Dienst mit Befehls-FIFO und Hotplug
# Direkt mit gcc (wie im Kopf von pmsdr.c beschrieben), nicht über ./configure: dessen config.guess von 2009 kennt
# aarch64 nicht (64-Bit-Pi OS) und bricht mit „unable to guess system type“ ab.
bold "$(t "PMSDR (Kurzwelle)" "PMSDR (shortwave)")"
PMSRC="$DIR/share/pmsdr/pmsdr-0.26.tar.gz"
PMSUM="7131124daf136e0b16a10efc50200da484b8078c0faac53d01a96e75a5fca947"
if [ ! -x /usr/local/bin/pmsdr ] && [ -f "$PMSRC" ]; then
    if [ "$(sha256sum "$PMSRC" | cut -d' ' -f1)" != "$PMSUM" ]; then
        warn "$(t "pmsdr-0.26.tar.gz: Prüfsumme stimmt nicht – nicht gebaut" "pmsdr-0.26.tar.gz: checksum mismatch – not built")"
    else
        PB="$(mktemp -d)"; chown "$U" "$PB"
        if asuser tar xzf "$PMSRC" -C "$PB" \
           && asuser sed -i 's/unsigned int nbytes = sizeof szLine - 1;/size_t nbytes = sizeof szLine - 1;/' "$PB/pmsdr-0.26/pmsdr.c" \
           && (cd "$PB/pmsdr-0.26" && asuser gcc -D__USE_GNU -std=gnu99 -fgnu89-inline -O2 -o pmsdr \
                 cmdparser.c pmsdr.c pmsdr_ctrl.c pmsdr_err.c udp_cmd.c -lusb-1.0 -lreadline -lpthread -lm >"$PB/build.log" 2>&1) \
           && install -m 755 "$PB/pmsdr-0.26/pmsdr" /usr/local/bin/pmsdr; then
            ok "$(t "Steuerprogramm gebaut: /usr/local/bin/pmsdr" "control program built: /usr/local/bin/pmsdr")"
        else
            warn "$(t "Steuerprogramm ließ sich nicht bauen – PMSDR bleibt ohne Steuerung" "control program could not be built – PMSDR stays without control")"
            [ -f "$PB/build.log" ] && tail -5 "$PB/build.log" | sed 's/^/    /'
        fi
        rm -rf "$PB"
    fi
elif [ -x /usr/local/bin/pmsdr ]; then
    ok "$(t "Steuerprogramm vorhanden" "control program present")"
fi
if [ -x /usr/local/bin/pmsdr ]; then
    FIFO="$(python3 -c "import json;print(json.load(open('$DIR/config/scanner.json')).get('fifo') or '/tmp/PMSDRcommands')" 2>/dev/null || echo /tmp/PMSDRcommands)"
    cat > /etc/systemd/system/pmsdr-control.service <<UNIT
[Unit]
Description=PMSDR Control FIFO
After=network.target

[Service]
Type=simple
User=$U
Environment=PMSDR_FILE=$FIFO
# Befehls-FIFO anlegen (das Programm tut es nicht; /tmp ist nach jedem Neustart leer)
ExecStartPre=/bin/sh -c '[ -p "$FIFO" ] || { rm -f "$FIFO"; mkfifo -m 660 "$FIFO"; }'
ExecStart=/usr/local/bin/pmsdr
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT
    mkdir -p /etc/systemd/system/pmsdr-control.service.d
    printf '[Unit]\nBindsTo=sys-subsystem-usb-pmsdr.device\nAfter=sys-subsystem-usb-pmsdr.device\n\n[Service]\nRestartSec=5\n' \
        > /etc/systemd/system/pmsdr-control.service.d/hotplug.conf
    echo 'SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="04d8", ATTR{idProduct}=="000c", MODE="0666", TAG+="systemd", ENV{SYSTEMD_ALIAS}="/sys/subsystem/usb/pmsdr", ENV{SYSTEMD_WANTS}+="pmsdr-control.service"' \
        > /etc/udev/rules.d/90-hesperosdx-pmsdr.rules
    systemctl daemon-reload
    udevadm control --reload-rules 2>/dev/null || true
    # steckt die PMSDR schon: Dienst gleich (neu) starten
    udevadm trigger --subsystem-match=usb --attr-match=idVendor=04d8 --action=add 2>/dev/null || true
    if lsusb 2>/dev/null | grep -qi "04d8:000c"; then systemctl restart pmsdr-control 2>/dev/null || true; fi
    ok "$(t "Dienst pmsdr-control eingerichtet (startet beim Anstecken der PMSDR)" "service pmsdr-control set up (starts when the PMSDR is plugged in)")"
fi

# Root-Helfer (gehören root, für den Benutzer nicht beschreibbar; die sudo-Regel erlaubt genau diese Datei)
install -m 755 -o root -g root "$DIR/share/root/hesperosdx-net" /usr/local/sbin/hesperosdx-net
install -m 755 -o root -g root "$DIR/share/root/hesperosdx-store" /usr/local/sbin/hesperosdx-store

bold "$(t "System" "System")"
# Systemprotokoll über Neustarts behalten (höchstens 64 MB, gebündelt geschrieben)
if [ ! -f /etc/systemd/journald.conf.d/hesperosdx.conf ]; then
    mkdir -p /etc/systemd/journald.conf.d /var/log/journal
    printf '[Journal]\nStorage=persistent\nSystemMaxUse=64M\nSyncIntervalSec=5m\n' > /etc/systemd/journald.conf.d/hesperosdx.conf
    systemctl restart systemd-journald 2>/dev/null || true
fi
ok "$(t "Systemprotokoll bleibt über Neustarts erhalten (höchstens 64 MB)" "system journal kept across reboots (max. 64 MB)")"
# Abmelden von SSH darf den RAM-Ordner der Dienste nicht leeren (logind RemoveIPC=yes räumt sonst /dev/shm des
# Benutzers ab – Lebenszeichen des Zeitplaners und Pegelanzeige verschwanden). Gilt ab dem nächsten Neustart;
# bis dahin legen die Dienste den Ordner selbst neu an (bin/rundir.py).
if [ ! -f /etc/systemd/logind.conf.d/hesperosdx.conf ]; then
    mkdir -p /etc/systemd/logind.conf.d
    printf '[Login]\nRemoveIPC=no\n' > /etc/systemd/logind.conf.d/hesperosdx.conf
fi
ok "$(t "RAM-Ordner bleibt beim Abmelden erhalten" "RAM folder kept on logout")"
# WLAN ohne Stromsparen (sonst ruckelt Live und das Dashboard meldet „Server nicht erreichbar“)
if command -v nmcli >/dev/null 2>&1 && [ ! -f /etc/NetworkManager/conf.d/hesperosdx-wifi.conf ]; then
    mkdir -p /etc/NetworkManager/conf.d
    printf '[connection]\nwifi.powersave = 2\n' > /etc/NetworkManager/conf.d/hesperosdx-wifi.conf
fi
for w in $(ls /sys/class/net 2>/dev/null | grep '^wl'); do iw dev "$w" set power_save off 2>/dev/null || true; done
ok "$(t "WLAN-Stromsparen aus" "Wi-Fi power saving off")"

systemctl enable --now avahi-daemon >/dev/null 2>&1 || true
start_services || true                 # Übergang von hamscanpi-*: bei Problemen laufen die alten weiter (Hinweis)
sleep 3
for u in hesperosdx-web hesperosdx-scheduler hesperosdx-rescue hesperosdx-mdns; do echo "  $u: $(systemctl is-active $u)"; done
for i in 1 2 3 4 5 6 7 8 9 10; do curl -fs -m 3 "http://127.0.0.1:$PORT/api/ping" >/dev/null 2>&1 && break; sleep 1; done
if curl -fs -m 3 "http://127.0.0.1:$PORT/api/ping" >/dev/null 2>&1; then
    ok "$(t "Dashboard antwortet auf Port $PORT" "dashboard answers on port $PORT")"
else
    warn "$(t "Dashboard antwortet noch nicht – journalctl -u hesperosdx-web -n 30" "dashboard does not answer yet – journalctl -u hesperosdx-web -n 30")"
fi
