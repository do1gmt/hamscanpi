#!/bin/bash
# HamScanPi – installation on a Raspberry Pi (Raspberry Pi OS / Debian).
# Run as your normal user from the cloned repository:   ./install.sh [port]
#
# What it does:
#   1. installs the Debian packages (Python, rtl-sdr, multimon-ng, …)
#   2. blacklists the DVB-T kernel driver so rtl_sdr can use the stick
#   3. creates the local config files and data directories
#   4. optionally builds ft8_lib (FT8/FT4 decoding on shortwave)
#   5. installs and starts two systemd services: hamscanpi-web and hamscanpi-scheduler
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${1:-8080}"
USER_NAME="$(id -un)"
bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

[ "$(id -u)" = 0 ] && { echo "Please run as your normal user, not as root (sudo is used where needed)."; exit 1; }
command -v apt-get >/dev/null || { echo "This installer needs a Debian-based system (Raspberry Pi OS)."; exit 1; }

bold "1/5 Packages"
sudo apt-get update -q
sudo apt-get install -y -q python3 python3-numpy python3-scipy python3-matplotlib python3-pil \
    python3-soundfile python3-sgp4 rtl-sdr multimon-ng sqlite3 alsa-utils util-linux curl git
ok "packages installed"

bold "2/5 RTL-SDR driver"
if ! grep -rqs "dvb_usb_rtl28xxu" /etc/modprobe.d/; then
    echo "blacklist dvb_usb_rtl28xxu" | sudo tee /etc/modprobe.d/blacklist-rtl-sdr.conf >/dev/null
    sudo modprobe -r dvb_usb_rtl28xxu 2>/dev/null || true
    ok "DVB-T driver blacklisted (re-plug the stick)"
else
    ok "DVB-T driver already blacklisted"
fi

bold "3/5 Configuration"
mkdir -p "$DIR/data" "$DIR/log" "$DIR/recordings" "$DIR/reports"
[ -f "$DIR/config/scanner.json" ] || { cp "$DIR/config/scanner.default.json" "$DIR/config/scanner.json"; ok "config/scanner.json created (band plan, receiver settings)"; }
[ -f "$DIR/config/web_settings.json" ] || { cp "$DIR/config/web_settings.example.json" "$DIR/config/web_settings.json"; ok "config/web_settings.json created"; }
chmod 600 "$DIR/config/web_settings.json"      # may hold the SMTP password later
chmod +x "$DIR"/bin/*.py "$DIR"/bin/*.sh

bold "4/5 FT8/FT4 decoder (optional, shortwave only)"
if [ -x "$HOME/ft8_lib/decode_ft8" ]; then
    ok "ft8_lib already built"
else
    read -r -p "  Build ft8_lib for FT8/FT4 decoding? Only needed with a shortwave receiver. [y/N] " a
    if [[ "$a" =~ ^[yYjJ]$ ]]; then
        sudo apt-get install -y -q build-essential
        git clone --depth 1 https://github.com/kgoba/ft8_lib "$HOME/ft8_lib"
        make -C "$HOME/ft8_lib" >/dev/null && ok "ft8_lib built: $HOME/ft8_lib/decode_ft8"
    else
        warn "skipped – FT8/FT4 segments will not be decoded"
    fi
fi

bold "PMSDR (optional)"
if [ -x /usr/local/bin/pmsdr ] && ! systemctl cat pmsdr-control.service >/dev/null 2>&1; then
    read -r -p "  /usr/local/bin/pmsdr found. Set up the pmsdr-control service with hotplug? [y/N] " a
    if [[ "$a" =~ ^[yYjJ]$ ]]; then
        sudo tee /etc/systemd/system/pmsdr-control.service >/dev/null <<UNIT
[Unit]
Description=PMSDR Control FIFO
After=network.target

[Service]
Type=simple
User=$USER_NAME
ExecStart=/usr/local/bin/pmsdr -u /tmp/PMSDRcommands
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT
        sudo mkdir -p /etc/systemd/system/pmsdr-control.service.d
        printf '[Unit]\nBindsTo=sys-subsystem-usb-pmsdr.device\nAfter=sys-subsystem-usb-pmsdr.device\n\n[Service]\nRestartSec=5\n' \
            | sudo tee /etc/systemd/system/pmsdr-control.service.d/hotplug.conf >/dev/null
        echo 'SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="04d8", ATTR{idProduct}=="000c", MODE="0666", TAG+="systemd", ENV{SYSTEMD_ALIAS}="/sys/subsystem/usb/pmsdr", ENV{SYSTEMD_WANTS}+="pmsdr-control.service"' \
            | sudo tee /etc/udev/rules.d/90-hamscanpi-pmsdr.rules >/dev/null
        sudo systemctl daemon-reload
        sudo udevadm control --reload-rules
        sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=04d8 --action=add 2>/dev/null || true
        ok "pmsdr-control set up – starts when the PMSDR is plugged in"
    fi
elif systemctl cat pmsdr-control.service >/dev/null 2>&1; then
    ok "pmsdr-control service already present"
else
    ok "no PMSDR control program found (/usr/local/bin/pmsdr) – shortwave needs it, see docs/pmsdr.md"
fi

bold "5/5 Services"
sudo tee /etc/systemd/system/hamscanpi-web.service >/dev/null <<UNIT
[Unit]
Description=HamScanPi web dashboard
After=network.target

[Service]
User=$USER_NAME
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/bin/webapp.py $PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
sudo tee /etc/systemd/system/hamscanpi-scheduler.service >/dev/null <<UNIT
[Unit]
Description=HamScanPi scheduler (scans and satellite passes)
After=network.target

[Service]
User=$USER_NAME
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/bin/scheduler.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now hamscanpi-web hamscanpi-scheduler
sleep 3
for i in 1 2 3 4 5 6 7 8; do
    curl -fs "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1 && break; sleep 1
done
if curl -fs "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1; then
    ok "dashboard: http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT/"
else
    warn "dashboard does not answer yet – check: journalctl -u hamscanpi-web -n 30"
fi

cat <<EOF

Next steps
  1. Open the dashboard, Settings → Station: enter your Maidenhead locator (needed for satellites)
     and choose the language (English / Deutsch).
  2. Scan → choose the receiver and the bands, press "Scan now".
  3. Satellites: switch on the band "Satellites" – passes are recorded automatically.
  PMSDR (shortwave): build the pmsdr control program first – see docs/pmsdr.md.
EOF
