#!/bin/bash
# HamScanPi – Umzug einer bestehenden PMSDR-Scanner-Installation (~/pmsdr-scanner -> ~/hamscanpi)
# und Vorbereitung des GitHub-Repositorys. Aufruf als normaler Benutzer:  bash hamscanpi-migrate.sh
#
#  1. alte Dienste (webapp.py/scheduler.py) anhalten und abmelden – pmsdr-control bleibt unberührt
#  2. ~/pmsdr-scanner nach ~/hamscanpi umbenennen (Daten, Aufnahmen, Einstellungen bleiben),
#     ~/pmsdr-scanner bleibt als Verweis bestehen
#  3. alten Programmstand sichern, neuen Stand einspielen, Altlasten (*.bak, alte Berichte) aufräumen
#  4. neue Dienste hamscanpi-web und hamscanpi-scheduler anlegen und starten
#  5. Git-Repository anlegen und den ersten Stand festschreiben (persönliche Dateien bleiben draußen)
set -e
OLD="$HOME/pmsdr-scanner"
NEW="$HOME/hamscanpi"
STAMP="$(date +%Y%m%d_%H%M%S)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
[ "$(id -u)" = 0 ] && { echo "Bitte nicht als root starten (bash hamscanpi-migrate.sh)."; exit 1; }

if [ -L "$OLD" ] && [ -d "$NEW" ]; then
    echo "Umzug ist schon erledigt ($OLD -> $NEW). Es wird nur der Programmstand erneuert."
    UPDATE_ONLY=1
elif [ -d "$OLD" ] && [ ! -e "$NEW" ]; then
    UPDATE_ONLY=0
elif [ -d "$NEW" ] && [ ! -e "$OLD" ]; then
    UPDATE_ONLY=1
else
    echo "Unklarer Zustand: $OLD und $NEW existieren beide. Bitte prüfen, nichts geändert."; exit 1
fi

sed -n '/^__PAYLOAD__$/,$p' "$0" | tail -n +2 | base64 -d > "$TMP/p.tgz"
mkdir -p "$TMP/repo" && tar xzf "$TMP/p.tgz" -C "$TMP/repo"

bold "1/5 Pakete"
NEED=""
python3 -c "import sgp4" 2>/dev/null || NEED="$NEED python3-sgp4"
command -v multimon-ng >/dev/null || NEED="$NEED multimon-ng"
command -v git >/dev/null || NEED="$NEED git"
if [ -n "$NEED" ]; then sudo apt-get install -y -q $NEED >/dev/null; fi
ok "Pakete vorhanden"

bold "2/5 Dienste anhalten"
OLD_UNITS=""
for f in /etc/systemd/system/*.service /lib/systemd/system/*.service; do
    [ -f "$f" ] || continue
    case "$(basename "$f")" in hamscanpi-*|pmsdr-control*) continue;; esac
    grep -qE "(webapp|scheduler)\.py" "$f" 2>/dev/null && grep -qi "pmsdr" "$f" 2>/dev/null && OLD_UNITS="$OLD_UNITS $(basename "$f")"
done
for u in hamscanpi-web.service hamscanpi-scheduler.service; do
    systemctl cat "$u" >/dev/null 2>&1 && sudo systemctl stop "$u" || true
done
if [ -n "${OLD_UNITS// /}" ]; then
    sudo systemctl stop $OLD_UNITS || true
    sudo systemctl disable $OLD_UNITS >/dev/null 2>&1 || true
    ok "alte Dienste angehalten und abgemeldet:$OLD_UNITS"
fi
ps -eo pid=,args= | awk '$2 ~ /python3?$/ && $3 ~ /(^|\/)bin\/(webapp|scheduler)\.py$/ {print $1}' | xargs -r kill 2>/dev/null || true
sleep 1

bold "3/5 Umzug und Programmstand"
if [ "$UPDATE_ONLY" = 0 ]; then
    mv "$OLD" "$NEW"
    ln -s "$NEW" "$OLD"
    ok "$OLD -> $NEW (alter Pfad bleibt als Verweis)"
    # alte Dienstdateien sichern und entfernen
    mkdir -p "$NEW/backup/units_$STAMP"
    for u in $OLD_UNITS; do
        for d in /etc/systemd/system /lib/systemd/system; do
            [ -f "$d/$u" ] && sudo cp "$d/$u" "$NEW/backup/units_$STAMP/" && sudo rm -f "$d/$u"
        done
    done
fi
cd "$NEW"
mkdir -p backup data log recordings reports
# alten Programmstand sichern (ohne Daten und Aufnahmen)
tar czf "backup/programm_vor_hamscanpi_$STAMP.tgz" --exclude='./data' --exclude='./recordings' --exclude='./reports' \
    --exclude='./log' --exclude='./backup' --exclude='*/__pycache__' . 2>/dev/null || true
ok "alter Stand gesichert: backup/programm_vor_hamscanpi_$STAMP.tgz"
# Altlasten
rm -f bin/*.bak* bin/*.pre_* bin/build_report.py UPDATE.md config/*.bak_* 2>/dev/null || true
rm -rf bin/__pycache__ devtools 2>/dev/null || true
find reports -maxdepth 1 \( -name '*.js' -o -name '*.css' -o -name 'index.html' \) -delete 2>/dev/null || true
# neuen Stand einspielen; persönliche Dateien (scanner.json, web_settings.json) bleiben unangetastet
cp -r "$TMP/repo/." "$NEW/"
chmod +x bin/*.py bin/*.sh install.sh devtools/*.sh devtools/*.py devtools/fakebin/rtl_sdr 2>/dev/null || true
[ -f config/scanner.json ] || cp config/scanner.default.json config/scanner.json
[ -f config/web_settings.json ] && chmod 600 config/web_settings.json
python3 - <<'PY'
import json, sys
from pathlib import Path
base = Path.home() / "hamscanpi"
sys.path.insert(0, str(base / "bin"))
import satellites as SAT
p = base / "config" / "scanner.json"
c = json.loads(p.read_text(encoding="utf-8"))
for r in c.get("ranges", []):
    if r.get("satellite") or r.get("name") == "Satelliten":
        r["segments"] = SAT.segments_for_bandplan()
        fs = [s["center_hz"] for s in SAT.SATELLITES]
        r["start_hz"], r["stop_hz"] = min(fs), max(fs)
for k, v in (("sstv_record_seconds", 120), ("sstv_probe_seconds", 4), ("sstv_threshold_db", 6.0)):
    c.setdefault(k, v)
# bisherige Anzeige beibehalten: Gerätenamen und Antennen standen fest im Programm
c.setdefault("device_labels", {"pmsdr": "PMSDR", "rtlsdr": "NESDR SMArt v5"})
c.setdefault("antennas", {"pmsdr": "HF-360", "rtlsdr": "Discone"})
# Bänder, deren Mithören verboten ist (Flugfunk): ausgeblendet und gesperrt, solange nicht freigeschaltet
c.setdefault("allow_restricted_bands", False)
d = json.loads((base / "config" / "scanner.default.json").read_text(encoding="utf-8"))
c["legal_note"] = d.get("legal_note", c.get("legal_note", ""))
p.write_text(json.dumps(c, indent=2, ensure_ascii=False), encoding="utf-8")
print("  \033[32m✓\033[0m Bandplan übernommen (Gerätenamen und Antennen wie bisher)")
if not c["allow_restricted_bands"]:
    print("  \033[32m✓\033[0m gesperrte Bänder (Flugfunk) ausgeblendet – allow_restricted_bands: false")
PY
python3 bin/migrate_config.py | sed 's/^/  /' || true          # neue Einstellungen aus dem Standard ergänzen
if crontab -l 2>/dev/null | grep -q "pmsdr-scanner"; then
    warn "crontab enthält noch 'pmsdr-scanner' – läuft über den Verweis weiter, bei Gelegenheit auf hamscanpi ändern (crontab -e)"
fi

bold "4/5 Dienste"
PORT=8080
sudo tee /etc/systemd/system/hamscanpi-web.service >/dev/null <<UNIT
[Unit]
Description=HamScanPi web dashboard
After=network.target

[Service]
User=$(id -un)
WorkingDirectory=$NEW
ExecStart=/usr/bin/python3 $NEW/bin/webapp.py $PORT
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
User=$(id -un)
WorkingDirectory=$NEW
ExecStart=/usr/bin/python3 $NEW/bin/scheduler.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now hamscanpi-web hamscanpi-scheduler >/dev/null 2>&1
sleep 3
for u in hamscanpi-web hamscanpi-scheduler; do echo "  $u: $(systemctl is-active $u)"; done
for i in 1 2 3 4 5 6 7 8; do curl -fs -m 3 "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1 && break; sleep 1; done
curl -fs -m 3 "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1 && ok "Dashboard antwortet auf Port $PORT" || warn "Dashboard antwortet nicht – journalctl -u hamscanpi-web -n 30"

bold "5/5 Git"
cd "$NEW"
# eigene GitHub-Adresse statt Platzhalter in README und Kennung des Bahndatenabrufs
if [ -d .git ] && ORIGIN="$(git remote get-url origin 2>/dev/null)"; then
    URL="$(echo "$ORIGIN" | sed -E 's#^git@github\.com:#https://github.com/#; s#\.git$##')"
    case "$URL" in
        https://github.com/*)
            sed -i "s#https://github.com/<your-account>/hamscanpi#$URL#g" README.md
            sed -i "s#+https://github.com/;#+$URL;#" bin/satellites.py
            ok "GitHub-Adresse eingetragen: $URL"
            ;;
    esac
fi
if [ ! -d .git ]; then
    git init -q -b main
    if [ -z "$(git config --get user.name)" ] || [ -z "$(git config --get user.email)" ]; then
        echo "  Git braucht einen Namen und eine Mailadresse für den ersten Stand. Beides wird auf GitHub öffentlich"
        echo "  sichtbar. Tipp: als Mail die noreply-Adresse von GitHub verwenden (GitHub → Settings → Emails)."
        read -r -p "  Name (z. B. dein Rufzeichen): " GN
        read -r -p "  Mailadresse: " GE
        git config user.name "$GN"; git config user.email "$GE"
    fi
fi
git add -A
# Sicherheitsprüfung: persönliche Dateien dürfen nie im Repository landen
BAD="$(git diff --cached --name-only | grep -E '(^|/)auth\.json$|(^|/)web_settings\.json$|^config/scanner\.json$|^(data|recordings|reports|log|backup)/' || true)"
if [ -n "$BAD" ]; then
    git reset -q
    echo "  ABBRUCH: persönliche Dateien wären im Repository gelandet:"; echo "$BAD" | sed 's/^/    /'
    exit 1
fi
if git diff --cached --quiet; then
    ok "nichts Neues festzuschreiben"
else
    git commit -q -m "${HSP_MSG:-HamScanPi $(date +%Y-%m-%d)}"
    ok "Stand festgeschrieben: $(git ls-files | wc -l) Dateien, keine persönlichen Daten"
fi
if git remote get-url origin >/dev/null 2>&1; then
    if PUSH_ERR="$(git push -q 2>&1)"; then
        ok "auf GitHub hochgeladen ($(git remote get-url origin))"
        # Version als Release: Tag v<VERSION> und, falls gh angemeldet ist, ein GitHub-Release
        V="$(cat VERSION 2>/dev/null || true)"
        if [ -n "$V" ] && ! git rev-parse -q --verify "refs/tags/v$V" >/dev/null; then
            git tag -a "v$V" -m "HamScanPi $V"
            if git push -q origin "v$V" 2>/dev/null; then
                ok "Version v$V markiert"
                if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
                    awk -v v="$V" '$0 ~ "^## "v {f=1; next} /^## / && f {exit} f' CHANGELOG.md > "$TMP/notes.md" 2>/dev/null || true
                    if gh release create "v$V" --title "HamScanPi $V" --notes-file "$TMP/notes.md" >/dev/null 2>&1; then
                        ok "GitHub-Release HamScanPi $V angelegt"
                    else
                        warn "Release nicht angelegt – später: gh release create v$V --title \"HamScanPi $V\" --notes-file CHANGELOG.md"
                    fi
                fi
            fi
        fi
    elif echo "$PUSH_ERR" | grep -qi "workflow"; then
        warn "GitHub verlangt für den automatischen Test (.github/workflows) eine Zusatzberechtigung. Einmalig:"
        echo "      gh auth refresh -h github.com -s workflow"
        echo "      cd ~/hamscanpi && git push && bash ~/hamscanpi-update.sh"
    else
        warn "Hochladen nicht geklappt – später von Hand: cd ~/hamscanpi && git push"
        echo "$PUSH_ERR" | tail -3 | sed 's/^/      /'
    fi
    exit 0
fi

cat <<'EOF2'

Fertig. Zum Hochladen auf GitHub (einmalig):

  sudo apt install gh                 # GitHub-Kommandozeile
  gh auth login                       # Anmeldung im Browser, Zugang bleibt auf dem Pi
  cd ~/hamscanpi
  gh repo create hamscanpi --private --source . --push

Danach auf github.com ansehen und unter Settings → General → Danger Zone auf „Public“ stellen,
wenn alles passt. Spätere Stände: cd ~/hamscanpi && git add -A && git commit -m "…" && git push
EOF2
exit 0
__PAYLOAD__
