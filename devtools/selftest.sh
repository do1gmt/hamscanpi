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
cp -r "$SRC/bin" "$SRC/config" "$SRC/devtools" "$SRC/share" "$SRC/bandplans" "$T/"
mkdir -p "$T/data" "$T/log" "$T/recordings" "$T/reports"
cp "$T/config/web_settings.example.json" "$T/config/web_settings.json"
cp "$T/config/scanner.default.json" "$T/config/scanner.json"
python3 - "$T" <<'PY'
import json, sys
p = sys.argv[1] + "/config/web_settings.json"; d = json.load(open(p)); d["my_locator"] = "JO62QM"; json.dump(d, open(p, "w"))
PY
cd "$T"
FAIL=0
ok()  { echo "   $1: ok"; }
bad() { echo "   $1: FEHLER"; FAIL=$((FAIL + 1)); }
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
ls reports/*iss_sstv70*_sstv.png >/dev/null 2>&1 && ok "SSTV-Bild" || bad "SSTV-Bild"
echo "== 3 gemeinsame Aufnahme"
# Welche zwei Satelliten gemeinsam aufgenommen werden, hängt von der Uhrzeit ab – das SSTV-Bild
# kommt deshalb auf den ersten, der Ton auf den zweiten Kanal des tatsächlich gewählten Paars.
read T1 CH TUNE SIG1 SIG2 C1 <<<"$(python3 - <<'PY'
import sys; sys.path.insert(0, "bin")
import satellites as S
r = S.upcoming(["lobachevsky", "qmrkwt2", "hc1px"], "JO62QM", fetch=False, past_s=0)
x = next(x for x in r["recordings"] if len(x["keys"]) > 1)
a, b = (S.BY_ID[c] for c in x["channels"][:2])
print(int((x["start"] + x["end"]) / 2) - 30, ",".join(x["channels"]), S.tuning_for(x["channels"]),
      f"{a['center_hz']}:sstv:0.3:{a['norad']}", f"{b['center_hz']}:tone:0.2:{b['norad']}", a["id"])
PY
)"
python3 devtools/sim_pass.py /tmp/hsp_train.u8 $T1 60 $TUNE $SIG1 $SIG2 >/dev/null
python3 bin/sat_pass.py --sats $CH --test /tmp/hsp_train.u8 --start $T1 --until $((T1+60)) | tail -2
ls reports/*_${C1}_*sstv.png >/dev/null 2>&1 && ok "SSTV-Bild (eigener Doppler, $C1)" || bad "SSTV-Bild (eigener Doppler, $C1)"
if command -v multimon-ng >/dev/null; then
  echo "== 4 APRS"
  python3 devtools/sim_pass.py /tmp/hsp_aprs.u8 $T0 30 145600000 145825000:aprs:0.3 >/dev/null
  python3 bin/sat_pass.py --sats iss,iss_aprs --test /tmp/hsp_aprs.u8 --start $T0 --until $((T0+30)) | grep "APRS: 3 Pakete" >/dev/null && ok "3 Pakete" || bad "APRS"
else
  echo "== 4 APRS übersprungen (multimon-ng fehlt)"
fi
echo "== 5 SSTV Kurzwelle"
python3 devtools/test_hf_sstv.py USB 60 | tee /tmp/hsp_hf.txt | grep -E "Bild|kein"
[ "$(grep -c "SSTV: Bild" /tmp/hsp_hf.txt)" = 2 ] && ok "2 Bilder" || bad "SSTV Kurzwelle"
echo "== 6 langer Überflug (11 min Ton, 5 Bilder): Speicherbedarf der SSTV-Auswertung"
python3 - <<'PY' || FAIL=$((FAIL + 1))
import sys, tracemalloc, numpy as np
sys.path.insert(0, "bin")
import sstv_decode as SD
from pysstv.color import Robot36
from PIL import Image
im = Image.new("RGB", (320, 240), (40, 90, 160))
pic = np.array(list(Robot36(im, 24000, 16).gen_samples()), np.float32) / 32767
a = (np.random.default_rng(1).standard_normal(24000 * 660) * 0.05).astype(np.float32)
for s0 in (10, 166, 322, 478, 619.3):
    i = int(s0 * 24000); a[i:i + len(pic)] += pic[:max(0, min(len(pic), len(a) - i))]
tracemalloc.start()
r = SD.decode_all(a, 24000, headless=True)
peak = tracemalloc.get_traced_memory()[1] // 2**20
print(f"   {len(r)} Bilder, Spitze {peak} MB:", "ok" if len(r) == 5 and peak < 400 else "FEHLER")
sys.exit(0 if len(r) == 5 and peak < 400 else 1)
PY
echo "== 7 Rufzeichen aus Dekodierungen, ADIF"
python3 - <<'PY' && ok "Rufzeichen und ADIF" || bad "Rufzeichen/ADIF"
import sys; sys.path.insert(0, "bin")
import stations as S
f = S.ft8_senders([{"text": "CQ K1ABC FN42", "snr": -14}, {"text": "DL1XYZ OK2ABC -07", "snr": -3}, {"text": "CQ DX 9A1A JN75", "snr": 0}])
assert [x[0] for x in f] == ["K1ABC", "OK2ABC", "9A1A"] and f[0][1] == "FN42", f
v = S.senders_for({"decoders": [{"type": "FT8", "detail": "3 FT8-Nachrichten · Dial 14.074000 MHz USB", "messages": [{"text": "CQ K1ABC FN42", "snr": -14, "audio_hz": 1500}]}]})
assert v[0][5] == 14075500.0, v
a = S.aprs_senders("DL2ABC-9 → APRS über ARISS: !4807.00N/01134.00E>Mobil")
assert a[0][0] == "DL2ABC-9" and a[0][1] == "JN58SC", a
assert [x[0] for x in S.text_senders("CQ CQ DE OE3ABC OE3ABC K")] == ["OE3ABC"]
assert S.text_senders("THE QUICK BROWN FOX 5NN") == []
d, b = S.distance_bearing("FN42", "JO62QM"); assert 6000 < d < 6600 and 280 < b < 310, (d, b)
x = S.adif([{"call": "K1ABC", "time": "2026-10-24T08:00:00+00:00", "freq_hz": 14074500, "mode": "FT8", "grid": "FN42", "snr": -14}], "N0CALL", "JO62QM")
assert "<CALL:5>K1ABC" in x and "<BAND:3>20m" in x and "<RST_RCVD:3>-14" in x and x.count("<EOR>") == 1
r = S.check_rules([{"id": 1, "rf_hz": 439401000, "band": "70cm", "calls": [{"call": "K1ABC", "grid": "FN42", "mode": "FT8"}]}],
                  [{"id": "a", "type": "freq", "value": "439.400"}, {"id": "b", "type": "call", "value": "K1*"},
                   {"id": "c", "type": "dx", "value": "5000"}, {"id": "d", "type": "call", "value": "DL*"}], "JO62QM")
assert [x[0]["id"] for x in r] == ["a", "b", "c"], r
PY
echo "== 8 Zeitpläne, PSK-Reporter-Pakete"
python3 - <<'PY' && ok "Zeitpläne und IPFIX" || bad "Zeitpläne/IPFIX"
import sys, time, json, struct; sys.path.insert(0, "bin")
import scan_plan as SP, pskreporter as P
cfg = json.load(open("config/scanner.default.json"))
st = {"active_device": "pmsdr", "selected_bands": ["20m"], "schedules": [
      {"name": "Nacht", "days": list(range(7)), "from": "22:00", "to": "06:00", "bands": ["80m", "40m"], "enabled": True}]}
t = lambda h: time.mktime((2026, 10, 27, h, 30, 0, 0, 0, -1))
assert SP.effective_settings(st, cfg, t(23))["selected_bands"] == ["80m", "40m"]
assert SP.effective_settings(st, cfg, t(3))["selected_bands"] == ["80m", "40m"]
assert SP.effective_settings(st, cfg, t(12))["selected_bands"] == ["20m"]
m = P.packets([{"call": "K1ABC", "freq_hz": 14075500, "mode": "FT8", "grid": "FN42", "snr": -14, "t": 1792800000}],
              "N0CALL", "JO62QM", "", 1, 42)[0]
v, ln = struct.unpack("!HH", m[:4]); assert v == 10 and ln == len(m) and ln % 4 == 0
i = 16; ids = []
while i < len(m):
    sid, sl = struct.unpack("!HH", m[i:i + 4]); assert sl % 4 == 0; ids.append(sid); i += sl
assert ids == [3, 2, 0x9992, 0x9993] and i == len(m), ids
PY
echo "== 9 RDS (UKW): Sendername, Radiotext, RT+, Uhrzeit aus einem verrauschten FM-Signal"
python3 - <<'PY' && ok "RDS" || bad "RDS"
import sys; sys.path.insert(0, "bin"); sys.path.insert(0, "devtools")
import rds as R, rds_gen as G
i = R.decode_mpx(G.mpx(freq_err_hz=5, clock_ppm=80, noise=0.03), 240000)
assert i["ps"] == "TEST FM " and i["pi"] == "D3C2" and i["pty_name"] == "Pop", i
assert i["rt"].startswith("Hallo Welt") and "äöü ß" in i["rt"], i["rt"]
assert i["rtplus"].get("artist") == "Welt" and i["ct"]["local"] == "24.10.2026 11:30" and i["af"] == [90.1, 95.0], i
I, Q = G.iq(snr_db=15); j = R.decode_iq(I, Q, 240000); assert j and j["ps"] == "TEST FM ", j
print("   " + R.summary(i))
PY
echo "== 10 TMC-Verkehrsmeldungen (RDS 8A) mit deutscher Ortstabelle"
python3 - <<'PY' && ok "TMC" || bad "TMC"
import sys; sys.path.insert(0, "bin"); sys.path.insert(0, "devtools")
import rds as R, rds_gen as G
t = R.decode_mpx(G.mpx(), 240000)["tmc"]
assert t["ltn"] == 1 and not t["encrypted"], t
txt = [m["text"] for m in t["messages"]]
assert any("A9" in x and "zwischen AS Hilpoltstein (56) und AS Allersberg (55): Stau" in x and "bis 1 h" in x for x in txt), txt
assert any("2 Fahrstreifen gesperrt auf 3 km" in x for x in txt), txt
print("   " + txt[-1])
PY
echo "== 11 Systemwerte und Verlauf (Reiter System)"
python3 - <<'PY' && ok "Systemwerte" || bad "Systemwerte"
import os, sys, time, subprocess, fcntl; sys.path.insert(0, "bin")
import sysmon as S
v = S.live()
assert v["cpu"]["total"] is not None and v["mem"]["total"] > 0 and v["activity"] == "idle", v
# Zuordnung: eigenes Skript (relativ) ja, Shell mit dem Pfad nur im Text nein
open("bin/zz_selftest.py", "w").write("import time\ntime.sleep(5)\n")
a = subprocess.Popen(["python3", "bin/zz_selftest.py"])
b = subprocess.Popen(["bash", "-c", f"cd {os.getcwd()} && sleep 5; echo bin/zz_selftest.py"])
time.sleep(0.5)
own, _ = S._proc_list()
assert a.pid in own and b.pid not in own, own
a.kill(); b.kill(); os.remove("bin/zz_selftest.py")
# Tätigkeit: Scan-Sperre wird erkannt, ohne sie anzufassen
f = open("data/scan.lock", "w"); fcntl.flock(f, fcntl.LOCK_EX)
assert S.activity() == 1
fcntl.flock(f, fcntl.LOCK_UN); f.close()
assert S.activity() == 0
# Sampler schreibt, Verlauf fasst zusammen
s = S.Sampler(period=1); t0 = time.time()
s.tick(t0); time.sleep(1.1); row = s.tick()
assert row and row[1] is not None, row
assert row[8] and row[9] > 0, row                     # größter Prozess der Minute mit Namen und MB
st = S.storage_info()                                  # Systemlaufwerk, Swap, Schreiblast
assert "kind" in st and "swap" in st and (st["disk"] is None or row[10] is not None), (st, row)
db = S._db(); now = int(time.time() // 60 * 60)
db.executemany("INSERT OR REPLACE INTO samples (t, cpu, cpu_peak, mem_avail, swap, temp, throttled, act) VALUES (?,?,?,?,?,?,?,?)",
               [(now - k * 60, 10.0, 20.0, 500, 0, 50.0, 0, 1 if k % 10 == 0 else 0) for k in range(1, 1500)])
db.commit(); db.close()
h = S.history(24)
assert h["bucket_s"] == 120 and 700 <= len(h["points"]) <= 722, (h["bucket_s"], len(h["points"]))
assert any(p["act"] == 1 for p in h["points"])
print(f"   CPU {v['cpu']['total']} %, {v['mem']['available']} MB frei, Verlauf 24 h: {len(h['points'])} Punkte")
PY
echo "== 12 SDR-Sticks: Erkennung, Rollen, Haupt-Stick über Seriennummer"
mkdir -p fakeusb fakebin2
printf '#!/bin/bash\necho "Found Rafael Micro R820T tuner" >&2\nexit 1\n' > fakebin2/rtl_test
printf '#!/bin/bash\nread a; [ "$a" = y ] || exit 1\necho "Configuration successfully written." >&2\n' > fakebin2/rtl_eeprom
chmod +x fakebin2/*
PATH="$PWD/fakebin2:$PATH" HSP_SYSFS_USB="$PWD/fakeusb" python3 - <<'PY' && ok "Sticks" || bad "Sticks"
import os, sys, shutil; sys.path.insert(0, "bin")
import sticks as S, devices as D
def dev(port, vid, pid, man, prod, sn, num):
    d = f"fakeusb/{port}"; os.makedirs(d, exist_ok=True)
    for k, v in dict(idVendor=vid, idProduct=pid, manufacturer=man, product=prod, serial=sn, busnum="1", devnum=num).items():
        open(f"{d}/{k}", "w").write(v + "\n")
assert S.rtl_params({"device_index": 0, "ppm": 3})["d"] == "0"                 # ohne Stick: alte Vorgabe
dev("1-1.2", "0bda", "2838", "Nooelec", "NESDR SMArt v5", "38291044", "4")
p = S.rtl_params({"ppm": 3, "gain": 20})
assert p["d"] == "38291044" and p["ppm"] == 3 and p["how"] == "serial", p          # erster Stick = Haupt-Stick
dev("1-1.3", "0bda", "2838", "Realtek", "RTL2838UHIDIR", "00000001", "5")
reg, present, new = S.sync(notify=False)
k2 = "sn:0bda:2838:00000001"
assert new == [k2] and reg["sticks"][k2]["role"] is None                          # neuer Stick: nicht benutzt
assert S.rtl_params({})["d"] == "38291044"
D._HW_CACHE["v"] = None; hw = D.detect_hardware()["rtlsdr"]
assert hw["present"] and hw["pending"] == 1, hw
S.update(k2, role="main", ppm=42)
p = S.rtl_params({"ppm": 3})
assert p["d"] == "1" and p["how"] == "order" and p["ppm"] == 42, p              # „00000001“ läse rtl_sdr als Gerät 1
assert any(r["duplicate"] for r in S.overview()["sticks"] if r["serial"] == "00000001")
assert S.load()["sticks"]["sn:0bda:2838:38291044"]["role"] == "spare"
shutil.rmtree("fakeusb/1-1.3")                                                    # Haupt-Stick weg: kein Ersatz
try:
    S.rtl_params({}); raise AssertionError("NoMainStick erwartet")
except S.NoMainStick:
    pass
D._HW_CACHE["v"] = None; assert not D.detect_hardware()["rtlsdr"]["present"]
dev("1-1.4", "1d50", "6089", "Great Scott Gadgets", "HackRF One", "a06063c8", "6")
assert any(r["kind"] == "other" for r in S.overview()["sticks"])
try:
    S.update("sn:1d50:6089:a06063c8", role="main"); raise AssertionError("Fremdgerät als Haupt-Stick?")
except ValueError:
    pass
shutil.rmtree("fakeusb/1-1.2"); shutil.rmtree("fakeusb/1-1.4")
dev("1-1.3", "0bda", "2838", "Realtek", "RTL2838UHIDIR", "00000001", "5")
assert S.probe_tuners() == "Rafael Micro R820T"
assert S.write_serial(k2, "DO1GMT2") == "DO1GMT2"
r = S.load()["sticks"]["sn:0bda:2838:DO1GMT2"]
assert r["role"] == "main" and r["ppm"] == 42, r
shutil.rmtree("fakeusb/1-1.3"); dev("1-1.3", "0bda", "2838", "Realtek", "RTL2838UHIDIR", "DO1GMT2", "5")   # neu angesteckt
assert S.rtl_params({})["d"] == "DO1GMT2" and S.rtl_params({})["how"] == "serial"
print("   Haupt-Stick über Seriennummer, neuer Stick wartet auf Zuordnung, HackRF erkannt, Seriennummer geschrieben")
PY
echo "== 13 Prüfsatellit Meteor: beide LRPT-Frequenzen, breiter Wasserfall, Leistungskurve"
TP="$(python3 - <<'PY'
import sys; sys.path.insert(0, "bin")
import satellites as S
p = max(S.upcoming(["meteor23"], "JO62QM", fetch=False, past_s=0)["passes"], key=lambda p: p["max_el"])
print(int(p["max_t"]) - 6)
PY
)"
python3 devtools/sim_lrpt.py /tmp/hsp_lrpt.u8 12 400000 1.0          # Signal auf 137,900 (Abstimmung 137,500)
python3 bin/sat_pass.py --sats meteor23 --test /tmp/hsp_lrpt.u8 --start $TP --until $((TP+12)) > /tmp/hsp_lrpt.txt
grep "MHz: max" /tmp/hsp_lrpt.txt
# Pegelanzeige: die Aufnahme schreibt data/meter.json und räumt sie am Ende weg
python3 - <<'PY' && ok "Pegelanzeige je Sekunde (meter.json), nach der Aufnahme entfernt" || bad "Pegelanzeige"
import json, os, subprocess, sys, time
p = subprocess.Popen([sys.executable, "-c", "import time"]); p.wait()
assert not os.path.exists("data/meter.json")
sys.path.insert(0, "bin"); import sat_pass as P
P.write_meter("satellite", [{"label": "137.900", "db": 3.2, "hist": [1.0, 3.2]}])
d = json.load(open("data/meter.json")); assert d["items"][0]["db"] == 3.2; os.remove("data/meter.json")
PY
grep -q "137.900 MHz: .*stärkste" /tmp/hsp_lrpt.txt && ls reports/*meteor23*_waterfall.png >/dev/null 2>&1 \
  && ok "Signal auf 137,900 erkannt, Wasserfall" || bad "Prüfsatellit"
echo "== 14 Update wartet auf ein freies Fenster"
python3 - <<'PY' && ok "Fenster zwischen Überflügen" || bad "Update-Fenster"
import sys; sys.path.insert(0, "bin")
import update_window as U
r = [(1000, 1600, "A"), (2300, 2900, "B")]
assert U.free_in(0, r, 900)[0] == 0                 # A beginnt erst in 16 min
assert U.free_in(200, r, 900)[0] == 2820            # A beginnt in 13 min, B 10 min nach A -> nach B
assert U.free_in(1800, r, 900)[0] == 1220           # nach A, aber B in 8 min -> nach B
assert U.free_in(200, r, 180)[0] == 0                # mit 3 min Abstand: A erst in 13 min -> jetzt frei
PY
# laufende Aufnahme nachstellen (Prozess namens sat_pass.py, PID in data/sat_rec.pid)
mkdir -p fake && echo "import time; time.sleep(30)" > fake/sat_pass.py
uw_case() {   # Kanal, Restsekunden -> erstes Wort der Ausgabe
  python3 fake/sat_pass.py --sats "$1" --until $(( $(date +%s) + $2 )) & local P=$!
  echo $P > data/sat_rec.pid; sleep 0.3
  python3 bin/update_window.py | cut -d' ' -f1; kill $P; wait $P 2>/dev/null; rm -f data/sat_rec.pid
}
[ "$(uw_case iss 120)" = STOP ] && [ "$(uw_case iss 900)" = 300 ] && [ "$(uw_case hc1px 900)" = STOP ] \
  && ok "letzte 3 min und niedrige Priorität werden beendet, sonst warten" || bad "Abbruch laufender Aufnahme"
echo "== 15 Stick hängt während der Aufnahme, kurze Störimpulse, FT8-Fehlalarm"
cat > fake/rtl_haenger.py <<'PY'
import os, sys, time
st = sys.argv[1]
n = int(open(st).read()) if os.path.exists(st) else 0
open(st, "w").write(str(n + 1))
for i in range(20 if n == 0 else 10 ** 6):
    sys.stdout.buffer.write(b"\x80" * 192000); sys.stdout.buffer.flush(); time.sleep(0.1)
time.sleep(3600)                      # erster Start: Stick bleibt stehen, rtl_sdr lebt weiter
PY
rm -f fake/rtl_state
(cd bin && timeout 60 python3 - <<'PY') && ok "Stillstand erkannt, rtl_sdr neu gestartet, Aufnahme läuft weiter" || bad "Stillstand"
import sat_pass as SP
s = SP.Source.__new__(SP.Source)
s.test, s.rate, s.nbytes, s.restarts, s.dev, s.node = None, 960000, 192000, 0, "0", "/dev/kein-stick"
s.cmd = ["python3", "../fake/rtl_haenger.py", "../fake/rtl_state"]; s._start()
got = stalls = 0
while got < 40:
    try:
        s.read(timeout=2)
        got += 1
    except SP.Stall:
        stalls += 1; s.restart()
s.close()
assert stalls == 1 and s.restarts == 1, (stalls, s.restarts)
PY
(cd bin && python3 - <<'PY') && ok "„SNR bis“ zählt erst ab 10 s, 2-s-Impuls nicht" || bad "SNR-Haltezeit"
import numpy as np, sat_pass as SP
pw = np.ones(7000); pw[2000:2020] = 10 ** 3.4
st = SP.signal_stats(pw); assert st["max_db"] < 1 and st["peak_db"] > 30, st
pw[3000:3300] = 10 ** 1.2
assert abs(SP.signal_stats(pw)["max_db"] - 12) < 0.5
PY
(cd bin && python3 - <<'PY' 2>/tmp/hsp_ft8.err | tail -1 | grep -q "^OK") && ok "FT8 erkannt, wackelnder Träger und Rauschen nicht" || { tail -3 /tmp/hsp_ft8.err; bad "FT8-Erkennung"; }
import numpy as np, decode_recordings as D
rate = 12000; t = np.arange(int(rate * 12.64)) / rate; rng = np.random.default_rng(3)
n = lambda a: a * (rng.standard_normal(len(t)) + 1j * rng.standard_normal(len(t)))
sym = rng.integers(0, 8, len(t) // 1920 + 1); f = 1000 + 6.25 * sym[np.arange(len(t)) // 1920]
ft8 = D.detect_ft8(np.exp(2j * np.pi * np.cumsum(f) / rate) * 0.5 + n(0.3), rate)
f2 = 1000 + 6.25 * (rng.random(len(t) // 1920 + 1) < 0.3)[np.arange(len(t)) // 1920]
car = D.detect_ft8(np.exp(2j * np.pi * np.cumsum(f2) / rate) + n(0.3), rate)
noi = D.detect_ft8(n(1.0), rate)
print("OK" if ft8 and ft8["confidence"] >= 85 and car is None and noi is None else f"NEIN {ft8} {car} {noi}")
PY
(cd bin && python3 - <<'PY') && ok "LRPT-Stufen: Rauschen nichts, 2–4 dB mit Schwund schwach, 12 dB stark" || bad "LRPT-Bewertung"
import numpy as np, sat_pass as SP
rng = np.random.default_rng(0); n = 925; t = np.arange(n)
noise = 1 + 0.02 * rng.standard_normal(n); bump = np.exp(-((t - 470) / 140) ** 2)
R = SP.ProbeChannel.lrpt_rating
assert R(noise)["level"] is None
assert R(noise + (10 ** 0.4 - 1) * bump * (0.6 + 0.4 * np.abs(np.sin(t / 7))))["level"] == "schwach"
assert R(noise + (10 ** 1.2 - 1) * bump)["level"] == "stark"
PY
python3 bin/funktest.py > /tmp/hsp_ft.txt && python3 bin/funktest.py 1 >> /tmp/hsp_ft.txt \
  && grep -q "MHz" /tmp/hsp_ft.txt && grep -q "einstellen" /tmp/hsp_ft.txt \
  && ok "Handfunken-Test: Überflugliste und Dopplertabelle" || bad "funktest.py"
echo "== 16 Richtantenne: Überflüge durch die Keule, Antenne je Treffer"
python3 - <<'PY' && ok "Strahl je Überflug, Bewertung, falsches Band" || bad "Richtantenne"
import json, os, shutil, sys
sys.path.insert(0, "bin")
bak = None
if os.path.exists("data/sticks.json"):
    bak = open("data/sticks.json").read()
json.dump({"sticks": {"x": {"role": "main", "kind": "rtl", "antenna": "Yagi", "ant_az": 245, "ant_bw": 70,
                            "ant_fmin": 137, "ant_fmax": 150}}}, open("data/sticks.json", "w"))
try:
    import satellites as S
    r = S.upcoming(["ao73", "so50"], "JN59HH", fetch=False, past_s=0, hours=48)
    ps = r["passes"]
    assert ps and all("beam" in p for p in ps)
    so = [p for p in ps if p["id"] == "so50"]
    assert so and all(not p["beam"]["band"] and p["score_parts"].get("Antenne") == -20 for p in so)
    hit = [p for p in ps if p["id"] == "ao73" and p["beam"]["secs"] >= 60]
    miss = [p for p in ps if p["id"] == "ao73" and p["beam"]["secs"] < 60]
    assert hit and all(p["score_parts"]["Antenne"] > 0 for p in hit)
    assert all(p["score_parts"].get("Antenne") == -20 for p in miss)
    # Flugbahn für die Himmelsansicht/Karte: Punkte, höchster Punkt passt zur Planung, Bodenspur plausibel
    p = max(ps, key=lambda x: x["max_el"])
    d = S.track(p["norad"], p["aos"], p["los"], "JN59HH")
    top = max(d["points"], key=lambda x: x["el"])
    assert len(d["points"]) > 60 and abs(top["el"] - p["max_el"]) < 1.5 and 200 < top["alt"] < 2000, (top, p["max_el"])
    assert d["beam"] and d["beam"]["az"] == 245
finally:
    if bak is None:
        os.remove("data/sticks.json")
    else:
        open("data/sticks.json", "w").write(bak)
PY
echo "== 17 Sicherung und Wiederherstellung"
python3 - <<'PY' && ok "sichern (3 Umfänge, ohne Zugangsdaten), einspielen, manipulierte Datei abgelehnt" || bad "Sicherung"
import io, json, os, sqlite3, sys, tarfile
sys.path.insert(0, "bin")
import backup as B
os.makedirs("recordings", exist_ok=True)
db = sqlite3.connect("data/scanner.db"); db.execute("create table if not exists bk_t(x)"); db.execute("insert into bk_t values (7)"); db.commit()
open("recordings/bk_test.wav", "wb").write(b"x" * 100)
ws = json.load(open("config/web_settings.json")); ws.setdefault("notify", {})["tg_token"] = "GEHEIM"; json.dump(ws, open("config/web_settings.json", "w"))
for sc in B.SCOPES:
    with open(f"/tmp/hsp_bk_{sc}.tgz", "wb") as f:
        B.write(f, sc, True)
names = tarfile.open("/tmp/hsp_bk_all.tgz").getnames()
assert "data/scanner.db" in names and "recordings/bk_test.wav" in names and "manifest.json" in names
assert "data/scanner.db" not in tarfile.open("/tmp/hsp_bk_settings.tgz").getnames()
with open("/tmp/hsp_bk_ns.tgz", "wb") as f:
    B.write(f, "settings", False)
w = json.loads(tarfile.open("/tmp/hsp_bk_ns.tgz").extractfile("config/web_settings.json").read())
assert "tg_token" not in w.get("notify", {})
db.execute("delete from bk_t"); db.commit(); db.close(); os.remove("recordings/bk_test.wav")
B.restore("/tmp/hsp_bk_all.tgz", wait=False, log=lambda m: None)
assert sqlite3.connect("data/scanner.db").execute("select x from bk_t").fetchall() == [(7,)]
assert os.path.exists("recordings/bk_test.wav") and os.listdir("restore-previous")
bad = io.BytesIO(); t = tarfile.open(fileobj=bad, mode="w:gz")
m = json.dumps({"app": "HamScanPi", "version": "x", "created": "x", "scope": "settings", "files": 1}).encode()
ti = tarfile.TarInfo("manifest.json"); ti.size = len(m); t.addfile(ti, io.BytesIO(m))
ti = tarfile.TarInfo("config/../../boese.txt"); ti.size = 1; t.addfile(ti, io.BytesIO(b"x")); t.close()
open("/tmp/hsp_bk_bad.tgz", "wb").write(bad.getvalue())
try:
    B.restore("/tmp/hsp_bk_bad.tgz", wait=False, log=lambda m: None); raise SystemExit("manipulierte Datei angenommen")
except ValueError:
    pass
PY
rm -f /tmp/hsp_bk_*.tgz
echo "== 18 Weiter aufnehmen: Datenpakete, CW/RTTY"
cat > fake_rtl_burst.py <<'PY'
import sys, numpy as np
rate = 240000
rng = np.random.default_rng(1)
for sec in range(80):
    n = np.arange(rate) + sec * rate
    z = 0.02 * (rng.standard_normal(rate) + 1j * rng.standard_normal(rate))
    if sec < 25 and sec % 5 == 0:
        z[:rate // 2] += 0.3 * np.exp(2j * np.pi * 50000 * n[:rate // 2] / rate)
    u = np.empty(2 * rate, np.uint8)
    u[0::2] = np.clip(z.real * 127.5 + 127.5, 0, 255); u[1::2] = np.clip(z.imag * 127.5 + 127.5, 0, 255)
    try:
        sys.stdout.buffer.write(u.tobytes()); sys.stdout.buffer.flush()
    except BrokenPipeError:
        break
PY
printf '#!/bin/sh\nexec python3 "%s/fake_rtl_burst.py"\n' "$PWD" > fake_rtl_burst.sh; chmod +x fake_rtl_burst.sh
(cd bin && timeout 200 python3 - <<'PY') && ok "APRS bleibt dran, solange Pakete kommen; CW bis zur Sendepause" || bad "Weiter aufnehmen"
import os, numpy as np, channel_watch as CW
class D: pass
d = D(); d.rate = 240000; d.binary = os.path.abspath("../fake_rtl_burst.sh"); d.index = 0; d.ppm = 0; d.gain = None; d.bias_t = False
out = CW.follow_qso(d, 144.75e6, [{"rf": 144.80e6, "mode": "aprs", "level": 15}],
                    {"data_follow_hang_seconds": 8, "data_follow_max_seconds": 60}, log=lambda m: None)
secs = len(out[144.80e6][0]) / CW.AUDIO_RATE
assert 19 <= secs <= 24, secs                      # 5 Pakete bis 20 s, Ruhe am Ende auf 1 s gekürzt
class H:
    rate = 48000.0
    def __init__(s): s.k = 0
    def capture(s, sec):
        n = int(s.rate * sec); t = np.arange(n) / s.rate; s.k += 1
        z = 0.01 * (np.random.default_rng(s.k).standard_normal(n) + 1j * np.random.default_rng(s.k + 99).standard_normal(n))
        if s.k <= 5:
            z = z + 0.2 * np.exp(2j * np.pi * 7000 * t)
        return z.real.astype(np.float32), z.imag.astype(np.float32)
_, sec = CW.hf_continue(H(), {}, [7000.0], 200.0, max_s=60, hang_s=6, chunk_s=4)
assert sec == 28, sec
_, sec = CW.hf_continue(H(), {}, [7000.0], 200.0, max_s=12, hang_s=6, chunk_s=4)
assert sec == 12, sec                              # Höchstdauer greift
PY
echo "== 19 Hinweise, Melderegeln, Stick-Fähigkeiten, Überflug-Vorschau"
python3 - <<'PY' && ok "Neustart/Stickwechsel erkannt, Regeln greifen, Direktabtastung erkannt, Live unter 24 MHz" || bad "Hinweise/Stick"
import json, os, re, sys, time
sys.path.insert(0, "bin")
import health as H, notices as NT, notify as N, sdrcaps as SC
sent = []
N.send_all = lambda s, t, c, **k: sent.append(s)
N.cfg = lambda: {"push": "telegram", "tg_token": "x", "tg_chat": "1",
                 "notices": {"levels": ["error"], "events": {"reboot": True, "stick": False}}}
for f in ("data/notices.json", "data/health_state.json"):
    if os.path.exists(f):
        os.remove(f)
H.run_all()
st = json.load(open("data/health_state.json"))
st["boot_id"], st["alive"] = "alter-start", time.time() - 600
st["sticks_present"] = ["rtl:weg"]
json.dump(st, open("data/health_state.json", "w"))
H.run_all()
keys = [r["key"] for r in NT.listing()["items"]]
assert "reboot" in keys and "stick" in keys, keys
assert any("neu gestartet" in x for x in sent) and not any("abgezogen" in x for x in sent), sent
n = len(sent); NT.raise_("t_warn", "warn", "scan", "Testwarnung"); assert len(sent) == n      # nur Fehler gewählt
NT.raise_("t_err", "error", "scan", "Testfehler"); assert len(sent) == n + 1
NT.ack(); assert NT.listing()["open"] == 0
# Schreibweise der Direktabtastung aus dem Hilfetext
for txt, want in (("\t[-D enable direct sampling (default: off)]\n", ["-D"]),
                  ("\t[-D direct_sampling_mode, 0 (default/off), 1 (I), 2 (Q)]\n", ["-D", "2"])):
    m = re.search(r"\[-D\s+([^\]]*)\]", txt)
    got = ["-D", "2"] if re.search(r"mode|\(I\)|\(Q\)|0 \(", m.group(1)) else ["-D"]
    assert got == want, (txt, got)
c = SC.caps({"manufacturer": "Nooelec", "product": "NESDR SMArt v5", "tuner": "Rafael Micro R820T"})
assert c["hf"] == "direct_q" and c["min_hz"] == 100e3
assert SC.caps({"product": "RTL2838UHIDIR", "tuner": "Rafael Micro R820T"})["hf"] is None
# Live: unter 24 MHz Direktabtastung, darüber nicht
import live as LV
SC.tool_flags = lambda refresh=False: {"rtl_sdr": ["-D"], "rtl_fm": ["-E", "direct2"]}
import sticks as STK
STK.load = lambda: {"sticks": {"k": {"role": "main", "kind": "rtl", "manufacturer": "Nooelec",
                                     "product": "NESDR SMArt v5", "tuner": "Rafael Micro R820T"}}}
cls = [k for k in vars(LV).values() if isinstance(k, type) and hasattr(k, "_direct")][0]
o = cls.__new__(cls)
assert o._direct(7_100_000, "rtl_sdr") == ["-D"] and o.direct
assert o._direct(145_000_000, "rtl_sdr") == [] and not o.direct
assert o._direct(3_700_000, "rtl_fm") == ["-E", "direct2"] and o.rtl_min() == 100000
PY
echo "== 20 Kurzwelle mit dem Stick (Direktabtastung)"
cat > fake_rtl_hf.py <<'PY'
import signal, sys
import numpy as np
a = sys.argv[1:]
opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
f, rate, n = float(opt("-f")), int(opt("-s")), int(opt("-n", "0")) or None
off = 7_101_000 - f                                  # Ton bei 7,101 MHz
signal.signal(signal.SIGINT, lambda *x: sys.exit(0))
done, blk = 0, rate // 10
while n is None or done < n:
    m = blk if n is None else min(blk, n - done)
    k = np.arange(done, done + m)
    z = 0.5 * np.exp(2j * np.pi * off * k / rate) + 0.01 * (np.random.randn(m) + 1j * np.random.randn(m))
    u = np.empty(2 * m, np.uint8); u[0::2] = np.clip(z.real * 127.5 + 127.5, 0, 255); u[1::2] = np.clip(z.imag * 127.5 + 127.5, 0, 255)
    try:
        sys.stdout.buffer.write(u.tobytes()); sys.stdout.buffer.flush()
    except BrokenPipeError:
        break
    done += m
PY
printf '#!/bin/sh\nexec python3 "%s/fake_rtl_hf.py" "$@"\n' "$PWD" > fake_rtl_hf.sh; chmod +x fake_rtl_hf.sh
(cd bin && timeout 200 python3 - <<'PY') && ok "Fenster wie PMSDR (Lage, Länge, Takt), Kurzwellenbänder im Plan" || bad "Kurzwelle mit dem Stick"
import os, numpy as np
import devices as D, sdrcaps as SC, sticks as STK, scan_plan as SP
SC.tool_flags = lambda refresh=False: {"rtl_sdr": ["-D"], "rtl_fm": ["-E", "direct2"]}
STK.rtl_params = lambda rtl: {"d": "0", "node": None, "gain": None, "ppm": 0}
D.kill_orphan_rtl = lambda: 0
fake = os.path.abspath("../fake_rtl_hf.sh")
for conj in (False, True):
    dev = D.RtlHfDevice({"rtlsdr": {"binary": fake}, "iq_conjugate": conj})
    dev.tune(7_100_000)
    def peak(i, q):
        z = (i - 1j * q) if conj else (i + 1j * q)
        S = np.abs(np.fft.fftshift(np.fft.fft(z[:48000])))
        return round(np.fft.fftshift(np.fft.fftfreq(48000, 1 / 48000))[S.argmax()])
    i, q = dev.capture(2); assert abs(len(i) - 98400) < 200 and peak(i, q) == 1000, (len(i), peak(i, q))
    i, q = dev.capture(14); assert abs(len(i) - 14 * 48000) <= 50 and peak(i, q) == 1000, (len(i), peak(i, q))   # ±1 Wert je Block durch Rundung
i, q, T = dev.capture_slot(15.0, lead_s=0.5); assert len(i) == 15 * 48000 and T % 15 == 0
# Plan: mit Kurzwellen-fähigem Stick kommen die gewählten Kurzwellenbänder als „rtlhf“ dazu
D.hf_via_rtl = lambda: True
D.resolve_device = lambda c, hw=None: c
st = {"active_device": "rtlsdr", "selected_bands": ["40m", "10m", "2m"], "selected_segments": {}}
plan = {p["name"]: p for p in SP.make_plan(None, st)}
assert plan.get("40m", {}).get("via") == "rtlhf" and plan["2m"].get("via") is None, plan.keys()
assert max(plan["10m"]["centers_hz"]) <= 28_800_000, max(plan["10m"]["centers_hz"])
PY
echo "== 21 Bandplan je Land (IARU-Region)"
python3 - <<'PY' && ok "DE unverändert, USA: Bänder, APRS 144.390, Simplex 146.520, Relais ±600 kHz, FRS/GMRS, Plan" || bad "Bandplan je Land"
import json, sys
sys.path.insert(0, "bin")
import bandplan as BP
raw = json.load(open("config/scanner.json"))
assert BP.apply(raw, code="DE") is raw                                   # Deutschland: exakt wie bisher
us = BP.apply(raw, code="US")
B = {b["name"]: b for b in us["ranges"]}
for n in ("1.25m", "33cm", "FRS/GMRS", "MURS", "NOAA WX", "ISM915"):
    assert n in B, n
for n in ("Freenet", "PMR446", "LPD", "ISM868", "4m"):
    assert n not in B, n
assert B["2m"]["stop_hz"] == 148_000_000 and B["70cm"]["start_hz"] == 420_000_000 and B["80m"]["stop_hz"] == 4_000_000
assert B["Flugfunk"]["legal"] == "public"
import scanner as SC
cls = lambda b, f: (SC.classify_segment(B[b], f) or {}).get("id")
assert cls("2m", 144.39e6) == "aprs" and cls("2m", 146.52e6) == "fm"
assert cls("2m", 146.94e6) == "relais" and cls("2m", 147.06e6) == "relais_plus"
assert cls("70cm", 446.0e6) == "fm" and cls("60m", 5.357e6) == "ft8"
assert raw == json.load(open("config/scanner.json"))                     # Datei bleibt unverändert
import devices as D, scan_plan as SP
D.resolve_device = lambda c, hw=None: c
D.hf_via_rtl = lambda: False
SP.BP_TEST = True
st = {"country": "US", "active_device": "rtlsdr", "selected_bands": ["2m", "FRS/GMRS", "NOAA WX"], "selected_segments": {}}
import bandplan
bandplan.country = lambda settings=None: "US"
plan = {p["name"]: p for p in SP.make_plan(None, st)}
assert set(plan) == {"2m", "FRS/GMRS", "NOAA WX"}, set(plan)
PY
echo "== 22 ISS-APRS: 437,825 und Weiterleitung über den Satelliten"
(cd bin && python3 - <<'PY') && ok "Kanal RS0ISS 437,825, „*“ im Pfad = vom Satelliten weitergesendet, Station mit Locator" || bad "ISS-APRS"
import satellites as S, sat_pass as P, stations as ST
ch = S.BY_ID["iss_aprs70"]
assert ch["center_hz"] == 437825000 and ch.get("aprs") and ch["norad"] == 25544
a = P.aprs_tnc2("DL1ABC-7>APRS,RS0ISS*,ARISS:!5030.00N/01135.00E-Test")
b = P.aprs_tnc2("OE3XYZ>APRS,ARISS:=4800.00N/01600.00E-direkt")
assert "⟲ RS0ISS (Satellit)" in a and "⟲" not in b, (a, b)
snd = ST.aprs_senders(a)
assert snd and snd[0][0].startswith("DL1ABC") and snd[0][1], snd
PY
echo "== 23 Live-Dekoder (APRS als Datenstrom, FT8-Takt)"
(cd bin && timeout 120 python3 - <<'PY') && ok "APRS-Pakete aus dem Live-Ton, FT8-Takt auf der UTC-Grenze" || bad "Live-Dekoder"
import ast, shutil, time
from pathlib import Path
import numpy as np
from scipy.signal import resample_poly
import live_decode as LD, ft8_watch as FW
if shutil.which("multimon-ng"):
    src = open("../devtools/sim_pass.py").read()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "ax25_afsk")
    ns = {"np": np, "A_RATE": 48000}
    exec(compile(ast.Module([fn], []), "sim", "exec"), ns)
    LD.auto_kind = lambda f, m: "packet"
    d = LD.LiveDecoders()
    a = ns["ax25_afsk"]([("DL1ABC", "APRS", "!5030.00N/01135.00E-Hallo"), ("OE3XYZ", "APRS", "=4800.00N/01600.00E-x")])
    x = np.concatenate([np.zeros(12000), resample_poly(a.astype(np.float64), 1, 2), np.zeros(24000)])
    pcm = (x * 0.6 * 32767).astype("<i2")
    for i in range(0, len(pcm), 480):
        d.feed(pcm[i:i + 480].tobytes(), 144_390_000, "nfm")
        time.sleep(0.001)
    for _ in range(40):
        if len(d.status()["events"]) >= 2:
            break
        time.sleep(0.2)
    txt = " | ".join(e["text"] for e in d.status()["events"])
    d.stop()
    assert "DL1ABC" in txt and "OE3XYZ" in txt, txt
got = []
FW.decode = lambda audio, proto, dec: (got.append(len(audio)), [{"snr": -12.0, "dt": 0.1, "audio_hz": 1234, "text": "CQ DL1ABC JN59"}])[1]
d = LD.LiveDecoders(); d.running = False
d.active, d._decoder, d.freq_hz = "ft8", Path("/bin/true"), 7_074_000
t = 1_800_000_003.0
for _ in range(2000):
    t += 0.02
    d._ft8_feed(t, np.zeros(480, np.float32))
time.sleep(1)
ev = d.status()["events"]
assert len(got) == 1 and abs(got[0] - 180000) < 50 and ev[0]["slot"].endswith(":15") and ev[0]["rf_hz"] == 7_075_234, (got, ev)
PY
echo "== 24 Live: CW, RTTY, SSTV, Stationsliste"
(cd bin && timeout 300 python3 - <<'PY') && ok "CW und RTTY als laufender Text, SSTV-Bild wächst, FT8/APRS-Absender in der Stationsliste" || bad "Live CW/RTTY/SSTV"
import ast, sqlite3, tempfile, time
from pathlib import Path
import numpy as np
from scipy.signal import resample_poly
import live_decode as LD, decode_recordings as DR
R = 24000
def run(kind, a, freq, mode):
    d = LD.LiveDecoders(); d.set_kind(kind)
    pcm = (a * 32767).astype("<i2")
    for i in range(0, len(pcm), 480):
        d.feed(pcm[i:i + 480].tobytes(), freq, mode)
        time.sleep(0.0003)
    for _ in range(60):
        time.sleep(0.2)
        if not d._busy and d.q.empty() and d.status()["events"]:
            time.sleep(1)
            break
    txt = " ".join(e["text"] for e in reversed(d.status()["events"]))
    d.stop()
    return txt
M = {v: k for k, v in DR.MORSE.items()}
u, out = 1.2 / 18, []
tone = lambda dur: np.sin(2 * np.pi * 700 * np.arange(int(dur * R)) / R) * 0.5
for w in "CQ CQ DE DL1ABC K".split():
    for ch in w:
        for sym in M[ch]:
            out += [tone(u if sym == "." else 3 * u), np.zeros(int(u * R))]
        out.append(np.zeros(int(2 * u * R)))
    out.append(np.zeros(int(4 * u * R)))
a = np.concatenate(out + [np.zeros(R * 9)]); a = a + 0.01 * np.random.default_rng(1).standard_normal(len(a))
t = run("cw", a, 7_020_000, "usb")
assert "DL1ABC" in t and "?????" not in t, t
inv = {v: k for k, v in DR.ITA2_LETTERS.items() if v}
bits = [1] * 40
for ch in "RYRYRY CQ CQ DE DLABC K ":
    c = inv[ch]; bits += [0] + [(c >> b) & 1 for b in range(5)] + [1, 1]
bits += [1] * 20
sp, out, ph = R / 45.45, [], 0.0
for k, b in enumerate(bits):
    n = int(round((k + 1) * sp)) - int(round(k * sp))
    f = 2125 if b else 2295
    w = ph + 2 * np.pi * f / R * np.arange(1, n + 1); ph = w[-1]; out.append(np.sin(w) * 0.4)
a = np.concatenate(out + [np.zeros(R * 9)]); a = a + 0.01 * np.random.default_rng(2).standard_normal(len(a))
t = run("rtty", a, 14_085_000, "usb")
assert "CQ CQ DE DLABC" in t, t
src = open("../devtools/sim_pass.py").read()
fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "robot36_audio")
ns = {"np": np, "A_RATE": 48000}; exec(compile(ast.Module([fn], []), "sim", "exec"), ns)
a = np.concatenate([np.zeros(R * 3), resample_poly(ns["robot36_audio"]().astype(np.float64), 1, 2) * 0.6, np.zeros(R * 5)])
LD.REPORTS = Path(tempfile.mkdtemp())
d = LD.LiveDecoders(); d.running = False; d.active = "sstv"
tt = 1_800_000_000.0
for i in range(0, len(a), 2400):
    tt += 0.1
    d._block_feed(tt, a[i:i + 2400].astype(np.float32))
    while d._busy:
        time.sleep(0.05)
ev = [e for e in d.status()["events"] if e["type"] == "SSTV"]
assert len(ev) == 1 and ev[0].get("img") and list(LD.REPORTS.glob("live_sstv_*.png")), ev
LD.DB = Path(tempfile.mkdtemp()) / "t.db"
d = LD.LiveDecoders(); d.running = False; d.freq_hz = 14_074_000
d._heard([("DL1ABC", "JO31", -10.0, "CQ DL1ABC JO31", "FT8", 14_075_000), ("OE3XYZ", "JN88", None, "x", "APRS", 144_800_000)], time.time())
rows = sqlite3.connect(LD.DB).execute("SELECT call, mode, grid, peak_id < 0, info FROM heard").fetchall()
assert {r[0] for r in rows} == {"DL1ABC", "OE3XYZ"} and all(r[3] for r in rows) and rows[0][4].startswith("Live:"), rows
PY
echo "== 25 RBDS, weitere Länder, automatische Sicherung, englische Hinweise"
(cd bin && python3 - <<'PY') && ok "RBDS (Programmart, Rufzeichen), AT/CH/GB/CA, Sicherung in Zielordner mit Aufräumen, Hinweise englisch" || bad "RBDS/Länder/Sicherung/Hinweise"
import json, os, tempfile, time
from pathlib import Path
import rds as R, bandplan as BP, backup as BK, notices as NT
assert R.pi_call(0x54A8) == "WAAA" and R.pi_call(0x1000) == "KAAA" and R.pi_call(0xC000) is None
BP.country = lambda settings=None: "US"; R._pty_cache["t"] = 0
assert R.pty_name(5) == "Rock" and R.pty_name(29) == "Weather"
BP.country = lambda settings=None: "DE"; R._pty_cache["t"] = 0
assert R.pty_name(1) == "Nachrichten"
raw = json.load(open("../config/scanner.json"))
names = lambda c: {b["name"] for b in BP.apply(raw, code=c)["ranges"]}
assert "Freenet" not in names("AT") and "4m" not in names("AT") and "4m" in names("CH")
gb = {b["name"]: b for b in BP.apply(raw, code="GB")["ranges"]}
assert gb["4m"]["start_hz"] == 70_000_000 and gb["4m"]["stop_hz"] == 70_500_000 and "CB 27/81" in gb
assert [s for s in gb["70cm"]["segments"] if s["id"] == "relais"][0]["ranges"][1] == [438_600_000, 440_000_000]
ca = names("CA"); assert "MURS" not in ca and "FRS/GMRS" in ca and "PMR446" not in ca
assert BP.suggest("IO91WM") == "GB" and BP.suggest("JN47AB") == "CH" and BP.suggest("JN78AA") == "AT"
d = Path(tempfile.mkdtemp())
assert BK.check_dir(str(Path("..").resolve())) is not None              # gleiches Laufwerk: abgelehnt
BK.check_dir = lambda x: None                                            # Testordner liegt auf derselben Platte
BK.AUTO_STATE = d / "state.json"
for i in range(4):
    BK.filename = (lambda i: (lambda scope: f"hamscanpi-backup-2026010{i}-0300-{scope}.tar.gz"))(i)
    st = BK.run_auto({"dir": str(d), "scope": "settings", "secrets": False, "keep": 2}, log=lambda m: None)
left = sorted(p.name for p in d.glob("hamscanpi-backup-*.tar.gz"))
assert left == ["hamscanpi-backup-20260102-0300-settings.tar.gz", "hamscanpi-backup-20260103-0300-settings.tar.gz"], left
assert BK.info(d / left[-1])["scope"] == "settings"
NT.FILE = d / "n.json"; NT.LOCK = d / "n.lock"
r = NT.raise_("t", "warn", "hardware", "Haupt-Stick fehlt", "Noch 5.0 GB frei.", fix="Stick anstecken oder USB-Verbindung prüfen.", notify=False)
assert r["en"] == {"title": "Main stick missing", "detail": "5.0 GB free.", "fix": "Plug in the stick or check the USB connection."}, r["en"]
PY
echo "== 26 Ohne Internet: Nachrichten nachschicken, PSK Reporter wartet"
(cd bin && python3 - <<'PY') && ok "Mail/Push ohne Netz aufgehoben und verspätet nachgeschickt, PSK Reporter warnt einmal und meldet nach" || bad "Ohne Internet"
import json, socket, sqlite3, tempfile, time, urllib.error
from pathlib import Path
import notify as N, pskreporter as PSK
d = Path(tempfile.mkdtemp())
N.STATE = d / "n.json"
N.cfg = lambda: {"push": "telegram", "tg_token": "x", "tg_chat": "1"}
sent, online = [], [False]
def push(subject, text, c, image=None, priority="default"):
    if not online[0]:
        raise urllib.error.URLError(socket.gaierror(-3, "Temporary failure in name resolution"))
    sent.append(subject)
N.send_push = push
N.send_all("Testmeldung", "Text", N.cfg())
assert len(json.load(open(N.STATE))["outbox"]) == 1 and not sent
assert N.flush_outbox() == 0 and len(json.load(open(N.STATE))["outbox"]) == 1      # noch offline: bleibt liegen
online[0] = True
assert N.flush_outbox() == 1 and sent[0].startswith("Testmeldung (verspätet, eigentlich") and not json.load(open(N.STATE))["outbox"]
def push_bad(*a, **k):                                   # falsche Einstellung (kein Netzfehler): nicht aufheben
    raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
N.send_push = push_bad
N.send_all("X", "Y", N.cfg())
assert not json.load(open(N.STATE)).get("outbox")
PSK.STATE = d / "p.json"
db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE heard (id INTEGER PRIMARY KEY, peak_id INTEGER, time TEXT, call TEXT, band TEXT, freq_hz REAL, mode TEXT, grid TEXT, snr REAL, info TEXT)")
logs = []
lg = lambda level, event, **kw: logs.append(event)
now = time.time()
db.execute("INSERT INTO heard (time, call, freq_hz, mode) VALUES ('2026-01-01T00:00:00+00:00', 'OLD1AB', 7074000, 'FT8')")
PSK.report(db, "DO1GMT", "JN59HH", log=lg, now=now)                                  # erster Lauf: Startpunkt
from datetime import datetime, timezone
db.execute("INSERT INTO heard (time, call, freq_hz, mode, grid, snr) VALUES (?,?,?,?,?,?)",
           (datetime.fromtimestamp(now + 400, timezone.utc).isoformat(), "DL1ABC", 14075000, "FT8", "JO31", -10))
real = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: (_ for _ in ()).throw(socket.gaierror(-3, "offline"))
assert PSK.report(db, "DO1GMT", "JN59HH", log=lg, now=now + 400) == 0
assert PSK.report(db, "DO1GMT", "JN59HH", log=lg, now=now + 800) == 0
assert logs.count("pskreporter_offline") == 1, logs
socket.getaddrinfo = lambda *a, **k: [(socket.AF_INET, socket.SOCK_DGRAM, 0, "", ("127.0.0.1", 9))]
assert PSK.report(db, "DO1GMT", "JN59HH", log=lg, now=now + 1200) == 1 and "pskreporter_resumed" in logs, logs
socket.getaddrinfo = real
sp = [{"call": "DL1ABC", "band": "20m", "mode": "FT8", "snr": -15, "grid": "JO31", "t": 1, "freq_hz": 14075000},
      {"call": "DL1ABC", "band": "20m", "mode": "FT8", "snr": -8, "grid": None, "t": 31, "freq_hz": 14075010},
      {"call": "DL1ABC", "band": "40m", "mode": "FT8", "snr": -12, "grid": None, "t": 61, "freq_hz": 7075000}]
dd = PSK.dedupe(sp)
assert len(dd) == 2 and [x for x in dd if x["band"] == "20m"][0]["snr"] == -8 and [x for x in dd if x["band"] == "20m"][0]["grid"] == "JO31", dd
assert PSK.software().startswith("HamScanPi")
import live as LV
cls = [c for c in vars(LV).values() if isinstance(c, type) and hasattr(c, "_agc_step")][0]
o = cls.__new__(cls); o._agc_hold = 0.0
g1 = o._agc_step(29.7, 0.0, 10)                          # leise: ein großer Schritt statt vieler kleiner
assert g1 >= 40 and o._agc_step(g1, 0.0, 10) == g1       # danach 10 s Ruhe (kein Neustart je Sekunde)
PY
echo "== 27 Dauerwache (Live ohne Browser)"
(cd bin && timeout 120 python3 - <<'PY') && ok "Dauerwache: Pause für Überflug und weiter, Neustart des Webservers, Endzeit" || bad "Dauerwache"
import json, time
import webapp as W
class Fake:
    def __init__(s, f, m): s.freq_hz = s.active_hz = f; s.mode = m; s.running = True; s.device = "rtlsdr"; s.keep_alive = False; s.watch_until = 0
    def stop(s): s.running = False
    class dec:
        @staticmethod
        def set_kind(k): pass
starts = []
def fake_start(d):
    W._live = Fake(d["freq_hz"], d["mode"]); starts.append(d["freq_hz"])
W.live_start = fake_start
W.scan_running = lambda: False
W.live_start({"freq_hz": 14074000, "mode": "usb"})
W.watch_set(True, 0)
st = json.load(open("../data/live_state.json"))
assert st["watch"]["on"] and st["watch"]["freq_hz"] == 14074000
W.LIVE_YIELD.write_text(json.dumps({"until": time.time() + 1.5, "sat": "ISS"}))
W.watch_tick(); assert W._live is None                                   # Stick für den Überflug frei
time.sleep(1.7); W.watch_tick()
assert W._live and W._live.freq_hz == 14074000 and W._live.keep_alive and not W.LIVE_YIELD.exists()
W._live = None; W._live_state["data"] = None
W.watch_tick(); assert W._live and W._live.keep_alive                    # nach Neustart des Webservers wieder da
W.watch_set(True, time.time() - 1); W.watch_tick()
assert W._watch_cfg() is None and not W._live                            # Endzeit erreicht: aus
assert starts == [14074000] * 3, starts
PY
rm -f /tmp/hsp_*.u8 /tmp/hsp_hf.txt /tmp/hsp_lrpt.txt /tmp/hsp_ft.txt
if [ "$FAIL" = 0 ]; then echo "Selbsttest fertig: alles ok."; else echo "Selbsttest: $FAIL FEHLER"; exit 1; fi
