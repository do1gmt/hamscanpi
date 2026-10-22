#!/bin/bash
# HamScanPi – Selbsttest ohne Hardware.
# Kopiert das Repository in ein temporäres Verzeichnis (die eigene Installation bleibt unberührt) und prüft:
#   1. Bahnrechnung und Aufnahmeplan mit synthetischen Bahndaten
#   2. ISS-Überflug 70 cm (Robot 36 auf 437,550 + Relais 437,800) mit Doppler
#   3. drei Satelliten in einer gemeinsamen Aufnahme, jeder mit eigenem Doppler
#   4. APRS über die ISS (145,825, braucht multimon-ng)
#   5. SSTV auf Kurzwelle (Scottie 1 ohne Kopf + Martin 1, verstimmte Gegenstation)
# Braucht: python3-numpy, python3-scipy, python3-sgp4, python3-pil, pip-Paket pysstv (nur für die Tests).
set -e
SRC="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
cp -r "$SRC/bin" "$SRC/config" "$SRC/devtools" "$T/"
mkdir -p "$T/data" "$T/log" "$T/recordings" "$T/reports"
cp "$T/config/web_settings.example.json" "$T/config/web_settings.json"
cp "$T/config/scanner.default.json" "$T/config/scanner.json"
python3 - "$T" <<'PY'
import json, sys
p = sys.argv[1] + "/config/web_settings.json"; d = json.load(open(p)); d["my_locator"] = "JO62QM"; json.dump(d, open(p, "w"))
PY
cd "$T"
python3 -c "import pysstv" 2>/dev/null || { echo "pysstv fehlt: pip install pysstv"; exit 1; }
echo "== 1 Bahndaten und Plan"
python3 devtools/make_tles.py data/tle.json
python3 - <<'PY'
import sys, time; sys.path.insert(0, "bin")
import satellites as S
r = S.upcoming([s["id"] for s in S.SATELLITES], "JO62QM", fetch=False, past_s=0)
assert r["passes"], "keine Überflüge"
print(f"   {len(r['passes'])} Überflüge, {len(r['recordings'])} Aufnahmen, "
      f"{sum(1 for x in r['recordings'] if len(x['keys']) > 1)} gemeinsam")
PY
echo "== 2 ISS 70 cm"
read T0 <<<"$(python3 - <<'PY'
import sys; sys.path.insert(0, "bin")
import satellites as S
r = S.upcoming(["iss_sstv70", "iss_rpt"], "JO62QM", fetch=False, past_s=0)
p = max(r["passes"], key=lambda p: p["max_el"]); print(int(p["max_t"]) - 45)
PY
)"
python3 devtools/sim_pass.py /tmp/hsp_iss.u8 $T0 75 437675000 437550000:sstv:0.3 437800000:tone:0.2 >/dev/null
python3 bin/sat_pass.py --sats iss_sstv70,iss_rpt --test /tmp/hsp_iss.u8 --start $T0 --until $((T0+75)) | tail -2
ls reports/*iss_sstv70*_sstv.png >/dev/null && echo "   SSTV-Bild: ok"
echo "== 3 gemeinsame Aufnahme"
read T1 CH TUNE <<<"$(python3 - <<'PY'
import sys; sys.path.insert(0, "bin")
import satellites as S
r = S.upcoming(["lobachevsky", "qmrkwt2", "hc1px"], "JO62QM", fetch=False, past_s=0)
x = next(x for x in r["recordings"] if len(x["keys"]) > 1)
print(int((x["start"] + x["end"]) / 2) - 30, ",".join(x["channels"]), S.tuning_for(x["channels"]))
PY
)"
python3 devtools/sim_pass.py /tmp/hsp_train.u8 $T1 60 $TUNE 436320000:sstv:0.3:67284 436950000:tone:0.2:67291 >/dev/null
python3 bin/sat_pass.py --sats $CH --test /tmp/hsp_train.u8 --start $T1 --until $((T1+60)) | tail -2
ls reports/*lobachevsky*_sstv.png >/dev/null && echo "   SSTV-Bild (eigener Doppler): ok"
if command -v multimon-ng >/dev/null; then
  echo "== 4 APRS"
  python3 devtools/sim_pass.py /tmp/hsp_aprs.u8 $T0 30 145600000 145825000:aprs:0.3 >/dev/null
  python3 bin/sat_pass.py --sats iss,iss_aprs --test /tmp/hsp_aprs.u8 --start $T0 --until $((T0+30)) | grep "APRS: 3 Pakete" >/dev/null && echo "   3 Pakete: ok"
else
  echo "== 4 APRS übersprungen (multimon-ng fehlt)"
fi
echo "== 5 SSTV Kurzwelle"
python3 devtools/test_hf_sstv.py USB 60 | grep -E "Bild|kein"
rm -f /tmp/hsp_*.u8
echo "Selbsttest fertig."
