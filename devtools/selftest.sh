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
cp -r "$SRC/bin" "$SRC/config" "$SRC/devtools" "$SRC/share" "$T/"
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
rm -f /tmp/hsp_*.u8 /tmp/hsp_hf.txt /tmp/hsp_lrpt.txt /tmp/hsp_ft.txt
if [ "$FAIL" = 0 ]; then echo "Selbsttest fertig: alles ok."; else echo "Selbsttest: $FAIL FEHLER"; exit 1; fi
