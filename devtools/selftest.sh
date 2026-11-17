#!/bin/bash
# Hesperos DX – Selbsttest ohne Hardware.
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
# pysstv (MIT, reines Python) erzeugt nur die SSTV-Testsignale – liegt bei, muss nicht installiert werden
export PYTHONPATH="$T/devtools/vendor${PYTHONPATH:+:$PYTHONPATH}"
python3 -c "import pysstv" 2>/dev/null || { echo "pysstv fehlt (devtools/vendor)"; exit 1; }
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
shutil.rmtree("fakeusb/1-1.3")                                                    # Haupt-Stick weg: die Reserve springt ein
assert S.rtl_params({})["d"] == "38291044" and S.main_device()[0].get("failover")
S.update("sn:0bda:2838:38291044", role="ignore")                                  # ohne Reserve: kein Ersatz
try:
    S.rtl_params({}); raise AssertionError("NoMainStick erwartet")
except S.NoMainStick:
    pass
D._HW_CACHE["v"] = None; assert not D.detect_hardware()["rtlsdr"]["present"]
S.update("sn:0bda:2838:38291044", role="spare")
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
# Pegelanzeige: die Aufnahme schreibt meter.json in den RAM-Ordner (nicht auf die Karte) und räumt sie am Ende weg
python3 - <<'PY' && ok "Pegelanzeige je Sekunde (meter.json im Arbeitsspeicher), nach der Aufnahme entfernt" || bad "Pegelanzeige"
import json, os, sys
sys.path.insert(0, "bin"); import sat_pass as P, rundir as RD
m = RD.path("meter.json")
assert str(m).startswith("/dev/shm/"), m
assert not m.exists() and not os.path.exists("data/meter.json")
P.write_meter("satellite", [{"label": "137.900", "db": 3.2, "hist": [1.0, 3.2]}])
d = json.load(open(m)); assert d["items"][0]["db"] == 3.2; os.remove(m)
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
BK.check_dir = lambda x, t=None: None                                         # Testordner liegt auf derselben Platte
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
assert PSK.software().startswith("Hesperos DX")
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
echo "== 28 Speicher bei der Auswertung langer Kurzwellen-Aufnahmen"
(cd bin && timeout 300 python3 - <<'PY') && ok "120-s-Aufnahme: Basisband stückweise, SSB-Ton richtig, Speicher begrenzt" || bad "Speicher Auswertung"
import resource, subprocess, sys, tempfile
import numpy as np, soundfile as sf
R, T = 48000, 120
p = tempfile.mktemp(suffix=".wav")
# Testdatei in eigenem Prozess erzeugen: Linux vererbt den Spitzenwert des Speichers an Kindprozesse – die
# Messung unten zeigte sonst den Speicher dieses Erzeugers mit
subprocess.run([sys.executable, "-c", f"""
import numpy as np, soundfile as sf
R, T = {R}, {T}
n = np.arange(R * T)
z = 0.3 * np.exp(2j * np.pi * 6000 * n / R) + 0.01 * (np.random.default_rng(1).standard_normal(len(n)) + 1j * np.random.default_rng(2).standard_normal(len(n)))
sf.write({p!r}, np.stack([z.real, z.imag], 1).astype(np.float32), R, subtype="PCM_16")
"""], check=True, timeout=120)
# Auswertung wie analyze_recordings.py (Basisband + USB + LSB + Spektrum-Kopf) in eigenem Prozess messen
run = f"""
import resource, sys, numpy as np
sys.path.insert(0, '.')
import iqload as IQ
from scipy.signal import butter, sosfiltfilt
src = open('analyze_recordings.py', encoding='utf-8').read()
i = src.index('def demod_ssb('); j = src.index('def make_spectrum(')
exec(src[i:j])
bb, r = IQ.baseband({p!r}, 5000, 12000)
u, _ = demod_ssb(bb, r, 'USB'); l, _ = demod_ssb(bb, r, 'LSB')
z, rr = IQ.head({p!r}, 5.0)
S = np.abs(np.fft.rfft(u[:r * 10])); f = np.fft.rfftfreq(r * 10, 1 / r)
print(round(f[S.argmax()]), round(float(np.std(l) / np.std(u)), 3), len(z) == 5 * rr,
      round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024))
"""
out = subprocess.run([sys.executable, "-c", run], capture_output=True, text=True, timeout=240)
tone, ratio, headok, mb = out.stdout.split()
assert int(tone) == 1000 and float(ratio) < 0.05 and headok == "True", out.stdout + out.stderr
assert int(mb) < 320, f"Spitzenspeicher {mb} MB"               # vorher über 800 MB
PY
echo "== 29 Stabilität: Wächter-Meldung, Notfall-Seite, Diagnose"
(cd bin && timeout 120 python3 - <<'PY') && ok "sd_notify, Notfall-Seite antwortet, Diagnose ohne Zugangsdaten" || bad "Stabilität"
import io, json, os, socket, tarfile, tempfile, threading, urllib.request
import sdnotify, diag, services as SV
d = tempfile.mkdtemp(); p = d + "/n.sock"
srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM); srv.bind(p)
os.environ["NOTIFY_SOCKET"] = p; os.environ["WATCHDOG_USEC"] = "180000000"
assert sdnotify.watchdog_enabled() and sdnotify.notify("WATCHDOG=1") and srv.recv(100) == b"WATCHDOG=1"
ws = json.load(open("../config/web_settings.json")); ws.setdefault("notify", {})["tg_token"] = "GEHEIM-TOKEN"
json.dump(ws, open("../config/web_settings.json", "w"))
buf = io.BytesIO(); diag.write(buf, stream=False); buf.seek(0)
t = tarfile.open(fileobj=buf)
names = t.getnames()
assert "info.txt" in names and "log/app.jsonl" in names and "config/web_settings.json" in names
assert b"GEHEIM-TOKEN" not in t.extractfile("config/web_settings.json").read()
st = SV.status(); assert {u["unit"] for u in st["units"]} == set(SV.UNITS) and "sudo" in st
import sys; sys.argv = ["rescue.py", "18081"]
import rescue
from http.server import ThreadingHTTPServer
h = ThreadingHTTPServer(("127.0.0.1", 18081), rescue.H)
threading.Thread(target=h.serve_forever, daemon=True).start()
html = urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://127.0.0.1:18081/", timeout=30).read().decode()
assert "Notfall-Seite" in html and "Pi neu starten" in html and "Diagnose herunterladen" in html
h.shutdown()
PY
echo "== 30 Mischbetrieb: Stick sauber freigeben, hängender Scan, Bedenkzeit"
(cd bin && timeout 200 python3 - <<'PY') && ok "Scan gibt den Stick binnen Sekunden frei, Zwangsende greift, Wächter erkennt hängenden Scan, Bedenkzeit" || bad "Mischbetrieb"
import ast, json, os, subprocess, sys, tempfile, time
from pathlib import Path
import devices as DV
# 1) Freigabe nur im Scanner aktiv
DV.YIELD_FILE.parent.mkdir(parents=True, exist_ok=True)
DV.YIELD_FILE.write_text("x"); DV.check_yield()                     # nicht aktiv: kein Abbruch
DV.enable_yield(); assert not DV.YIELD_FILE.exists()
DV.YIELD_FILE.write_text("x")
try:
    DV.check_yield(); raise SystemExit("kein Abbruch")
except DV.ScanYield:
    pass
DV.YIELD_FILE.unlink()
# 2) Funktionen des Schedulers ohne seine Hauptschleife laden
src = open("scheduler.py", encoding="utf-8").read()
fns = [n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name in
       ("scan_running", "cycle_running", "stop_running_scan", "scan_watchdog", "sat_arm_wait")]
logs = []
BASE = Path("..").resolve()
g = {"BASE": BASE, "LOCK": BASE / "data" / "scan.lock", "time": time, "json": json, "subprocess": subprocess,
     "applog": lambda lv, comp, ev, **d: logs.append(ev), "sat_pass_active": lambda: False, "SCAN_HANG_S": 600,
     "_RD": __import__("rundir")}
exec(compile(ast.Module(fns, []), "sched", "exec"), g)
# Scanner-Attrappe: hält Lauf- und Stick-Sperre, gibt bei scan_yield frei (wie der echte Scanner)
dummy = f"""
import fcntl, os, time
a = open({str(BASE / 'data' / 'cycle.lock')!r}, 'a'); fcntl.flock(a, fcntl.LOCK_EX)
b = open({str(BASE / 'data' / 'scan.lock')!r}, 'a'); fcntl.flock(b, fcntl.LOCK_EX)
open({str(BASE / 'data' / 'scan.pid')!r}, 'w').write(str(os.getpid()))
t0 = time.time()
while time.time() - t0 < 60:
    if os.environ.get('POLITE') and os.path.exists({str(DV.YIELD_FILE)!r}):
        break
    time.sleep(0.2)
"""
p = subprocess.Popen([sys.executable, "-c", dummy], env=dict(os.environ, POLITE="1"), start_new_session=True)
time.sleep(1.0)
assert g["scan_running"]() and g["cycle_running"]()
t = time.time(); assert g["stop_running_scan"]("Überflug Test")
assert time.time() - t < 5 and "scan_yielded" in logs and not DV.YIELD_FILE.exists(), (round(time.time() - t, 1), logs)
p.wait(5)
# 3) unhöflicher Scan: Zwangsende über die Prozessgruppe
p = subprocess.Popen([sys.executable, "-c", dummy], start_new_session=True)
time.sleep(1.0)
t = time.time(); assert g["stop_running_scan"]("Überflug Test 2")
assert "scan_stopped" in logs and time.time() - t < 40
p.wait(5)
# 4) Wächter: Lebenszeichen 11 min alt -> Lauf wird beendet, Hinweis
import notices as NT
NT.FILE = Path(tempfile.mkdtemp()) / "n.json"; NT.LOCK = NT.FILE.with_suffix(".lock")
p = subprocess.Popen([sys.executable, "-c", dummy], env=dict(os.environ, POLITE="1"), start_new_session=True)
time.sleep(1.0)
DV.HEARTBEAT.touch(); old = time.time() - 660; os.utime(DV.HEARTBEAT, (old, old))
g["scan_watchdog"]()
assert "scan_hung" in logs and any(r["key"] == "scan_hang" for r in NT.listing()["items"]) and not g["scan_running"]()
p.wait(5)
# 5) Bedenkzeit
(BASE / "data" / "sat_arm.json").write_text(json.dumps({"until": time.time() + 60}))
assert 55 < g["sat_arm_wait"]() <= 60
(BASE / "data" / "sat_arm.json").write_text(json.dumps({"until": 0}))
assert g["sat_arm_wait"]() == 0
PY
echo "== 31 Stationen: Übersicht je Rufzeichen, Karte mit allen"
(cd bin && timeout 200 python3 - <<'PY') && ok "Übersicht stimmt mit der Aufzeichnung überein, Karte zeigt alle mit Locator, nicht nur eine Seite" || bad "Stationen"
import random, sqlite3, time
import webapp as W, stations as ST
db = W.db_connect(); ST.ensure_tables(db)
rnd = random.Random(3)
calls = [f"T{i:03d}AB" for i in range(400)]
rows = []
for k in range(4000):
    c = rnd.choice(calls)
    rows.append((-(10_000_000 + k), time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - rnd.randint(0, 40 * 86400))),
                 c, rnd.choice(["40m", "20m"]), 14074000, "FT8", ("JO31" if int(c[1:4]) % 2 else None), rnd.randint(-20, 5), "t"))
db.executemany("INSERT INTO heard (peak_id, time, call, band, freq_hz, mode, grid, snr, info) VALUES (?,?,?,?,?,?,?,?,?)", rows)
db.execute("DELETE FROM meta WHERE k='station_summary'"); db.commit(); db.close()
d = W.stations(0, "", "", 100, 0)                                  # baut die Übersicht auf
db = W.db_connect()
a = dict(db.execute("SELECT call, COUNT(*) FROM heard WHERE call LIKE 'T%AB' GROUP BY call").fetchall())
b = dict(db.execute("SELECT call, n FROM station WHERE call LIKE 'T%AB'").fetchall())
assert a == b and len(a) == 400, (len(a), len(b))
# neuer Eintrag über den normalen Weg: Übersicht wird mitgeführt
cur = db.execute("INSERT OR IGNORE INTO heard (peak_id, time, call, band, freq_hz, mode, grid, snr, info) VALUES (?,?,?,?,?,?,?,?,?)",
                 (-99999999, "2026-11-03T10:00:00+00:00", "T001AB", "80m", 3573000, "FT4", "JO31AB", 7, "t"))
ST.upsert_station(db, "T001AB", "2026-11-03T10:00:00+00:00", "80m", "FT4", "JO31AB", 7); db.commit()
r = db.execute("SELECT n, bands, modes, grid, best_snr FROM station WHERE call='T001AB'").fetchone()
assert r[0] == a["T001AB"] + 1 and "80m" in r[1] and "FT4" in r[2] and r[3] == "JO31AB" and r[4] >= 7, r
db.close()
m = W.stations_map(0)
assert len([p for p in m["points"] if p[0].startswith("T") and p[0].endswith("AB")]) == 200, len(m["points"])   # alle mit Locator
assert len(d["stations"]) == 100                                    # Liste bleibt seitenweise
assert W._period_days({"hours": "0.5"}, 30) == 0.5 / 24 and W._period_days({"hours": "0"}, 30) == 0
assert W._period_days({"days": "7"}, 30) == 7 and W._period_days({}, 30) == 30
assert ST.pick_grid({"JN47": 5, "JN49": 1}) == "JN47" and ST.pick_grid({"JN47AB": 1, "JN47": 3, "JO31": 2}) == "JN47AB"
db = W.db_connect()
for i in range(6):                                                    # 5× JN47, 1× JN49 (Fehldekodierung/Ausflug)
    g = "JN47" if i < 5 else "JN49"
    db.execute("INSERT INTO heard (peak_id, time, call, band, freq_hz, mode, grid, snr, info) VALUES (?,?,?,?,?,?,?,?,?)",
               (-(20_000_000 + i), f"2026-11-0{1 + i % 3}T1{i}:00:15+00:00", "HB9ZZZ", "40m", 7075000, "FT8", g, -10, f"X HB9ZZZ {g}"))
    ST.upsert_station(db, "HB9ZZZ", f"2026-11-0{1 + i % 3}T1{i}:00:15+00:00", "40m", "FT8", g, -10)
db.commit(); db.close()
sd = W.station_detail("HB9ZZZ")
assert sd["found"] and sd["summary"]["grid"] == "JN47" and sd["total"] == 6 and [g["grid"] for g in sd["grids"]] == ["JN47", "JN49"], sd["summary"]
assert not W.station_detail("XX9XXX")["found"]
recent = W.stations(1 / 24, "", "", 1000, 0)["total"]                 # letzte Stunde: nur der eben eingetragene
assert recent <= W.stations(0, "", "", 1, 0)["total"]
PY
echo "== 32 Antennen-Profile"
(cd bin && python3 - <<'PY') && ok "Profile aus bisherigen Angaben, Auswahl übernimmt Richtung und Bereich, Handänderung löst das Profil" || bad "Antennen-Profile"
import json, tempfile
from pathlib import Path
import sticks as STK, antennas as AN
STK.REG = Path(tempfile.mkdtemp()) / "sticks.json"
json.dump({"sticks": {"a": {"key": "a", "role": "main", "kind": "rtl", "antenna": "Discone"},
                      "b": {"key": "b", "role": "spare", "kind": "rtl", "antenna": "Yagi", "ant_az": 245, "ant_bw": 70,
                            "ant_fmin": 137, "ant_fmax": 150}}}, open(STK.REG, "w"))
AN.FILE = Path(tempfile.mkdtemp()) / "antennas.json"
d = AN.load()
names = {p["name"]: p for p in d["profiles"]}
assert "Discone" in names and names["Yagi"]["az"] == 245 and names["Yagi"]["fmax"] == 150, d
AN.select("rtlsdr", names["Yagi"]["id"])
m = STK.load()["sticks"]["a"]
assert m["antenna"] == "Yagi" and m["ant_az"] == 245 and m["ant_fmin"] == 137 and m["ant_profile"] == names["Yagi"]["id"], m
assert AN.current()["rtlsdr"] == names["Yagi"]["id"]
hf = AN.set_profiles(AN.load()["profiles"] + [{"name": "HF-360", "fmin": 0.1, "fmax": 30}])["profiles"][-1]
AN.select("rtlsdr", hf["id"])                              # Kurzwellenantenne am Stick (ab 100 kHz) – war ein Fehler
assert STK.load()["sticks"]["a"]["ant_fmin"] == 0.1
STK.update("a", antenna="Selbstbau")                      # von Hand geändert: kein Profil mehr
assert STK.load()["sticks"]["a"].get("ant_profile") is None
try:
    AN.set_profiles([{"name": "X", "fmin": 150, "fmax": 137}]); raise SystemExit("Bereich falsch herum angenommen")
except ValueError:
    pass
PY
echo "== 33 Scanlauf: Rückgabewert (erfolgreich / für Überflug unterbrochen)"
RCT="$(mktemp -d)"; cp -r bin config data "$RCT/" 2>/dev/null; mkdir -p "$RCT/log" "$RCT/data"
RCOK=1
for code in 0 3; do
    printf '#!/usr/bin/env python3\nimport sys\nprint("Peaks : 0"); print("Scan beendet"); sys.exit(%s)\n' "$code" > "$RCT/bin/scanner.py"
    chmod +x "$RCT/bin/scanner.py"
    rc=0; (cd "$RCT" && timeout 120 bash bin/run_cycle.sh >/dev/null 2>&1) || rc=$?
    [ "$rc" -eq "$code" ] || { echo "   Scanner $code -> run_cycle $rc"; RCOK=0; }
done
rm -rf "$RCT"
[ "$RCOK" -eq 1 ] && ok "erfolgreicher Lauf 0, unterbrochener Lauf 3" || bad "Rückgabewert run_cycle"
echo "== 34 Netzwerk-Ausgabe im WSJT-X-Protokoll (OpenHamClock)"
(cd bin && timeout 60 python3 - <<'PY') && ok "Pakete nach WSJT-X-Spezifikation, Empfang über UDP, ohne Netz aufgehoben und nachgeschickt" || bad "WSJT-X-Ausgabe"
import json, socket, sys, time
sys.path.insert(0, "../devtools")
import wsjtx_read as RD, wsjtx_udp as WX
d = RD.read(WX.decode("Hesperos DX", 1793710815.0, -12, 0.3, 1997, "FT8", "CQ DL1ABC JO31"))
assert d["typ"] == 2 and d["ms"] == 46815000 and d["snr"] == -12 and d["df"] == 1997 and d["mode"] == "~" and d["msg"] == "CQ DL1ABC JO31" and d["rest"] == 0, d
s = RD.read(WX.status("Hesperos DX", 7074000, "FT8", "DO1GMT", "JN59HH"))
assert s["dial"] == 7074000 and s["decall"] == "DO1GMT" and s["rest"] == 0, s
assert WX.parse_targets("192.168.178.50, 224.0.0.1:2237; ohc.local:2238") == [("192.168.178.50", 2237), ("224.0.0.1", 2237), ("ohc.local", 2238)]
try:
    WX.parse_targets("rechner:99999"); raise SystemExit("falscher Port angenommen")
except ValueError:
    pass
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); rx.bind(("127.0.0.1", 0)); rx.settimeout(5)
port = rx.getsockname()[1]
WX.cfg = lambda: {"enabled": True, "targets": f"127.0.0.1:{port}", "ident": "Hesperos DX", "my_call": "DO1GMT", "my_locator": "JN59HH"}
WX.STATE = __import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "st.json"
# Netzausfall nachstellen: die ersten Sendeversuche scheitern
real = WX.Sender._send
fail = {"n": 3}
def flaky(self, tg, pkt):
    if fail["n"] > 0:
        fail["n"] -= 1
        raise OSError(101, "Network is unreachable")
    return real(self, tg, pkt)
WX.Sender._send = flaky
msgs = [{"text": "CQ DL1ABC JO31", "snr": -10, "audio_hz": 1200}, {"text": "DL1ABC OE3XYZ R-12", "snr": -3, "audio_hz": 800}]
assert WX.publish(14074000, "FT8", time.time() // 15 * 15, msgs)
time.sleep(1.5)
assert WX._get().pending, "nichts aufgehoben"
WX.publish(14074000, "FT8", time.time() // 15 * 15, [{"text": "CQ K1ABC FN42", "snr": -15, "audio_hz": 500}])   # Netz wieder da
got = []
t0 = time.time()
while time.time() - t0 < 6 and len([g for g in got if g["typ"] == 2]) < 3:
    try:
        got.append(RD.read(rx.recv(4096)))
    except socket.timeout:
        break
texts = [g["msg"] for g in got if g["typ"] == 2]
assert sorted(texts) == sorted(["CQ DL1ABC JO31", "DL1ABC OE3XYZ R-12", "CQ K1ABC FN42"]), texts
assert any(g["typ"] == 1 and g["dial"] == 14074000 for g in got)
assert not WX._get().pending
PY
echo "== 35 EiBi-Sendeplan (Kurzwellen-Rundfunk)"
(cd bin && python3 - <<'PY') && ok "Saison, Sendezeiten und Tage, Senderstandorte, Sortierung, Hochladen, ohne Internet bleibt die Liste" || bad "EiBi"
import json, tempfile
from datetime import datetime, date, timezone
from pathlib import Path
import eibi as E
assert E.season(date(2026, 11, 3)) == "b26" and E.season(date(2026, 7, 1)) == "a26" and E.season(date(2027, 1, 10)) == "b26"
assert E.season(date(2027, 3, 28)) == "a27" and E.season(date(2027, 3, 27)) == "b26" and E.previous_season("a27") == "b26"
E.DIR = Path(tempfile.mkdtemp()); E.CSV = E.DIR / "sked.csv"; E.README = E.DIR / "README.TXT"; E.STATE = E.DIR / "state.json"
E._home = lambda: (49.31, 10.625)                                   # JN59HH
csv = open("../devtools/fixtures/eibi_sked_sample.csv", "rb").read()
rd = open("../devtools/fixtures/eibi_readme_sample.txt", "rb").read()
try:
    E.upload(b"irgendwas"); raise SystemExit("falsche Datei angenommen")
except ValueError:
    pass
assert E.upload(csv) == 8 and E.upload(rd, readme=True) == 8
names = lambda t: [d["station"] for d in E.now_list(all_freqs=True, now=t)["items"]]
assert names(datetime(2026, 11, 4, 0, 30, tzinfo=timezone.utc)) == ["Radio Channel 292", "China Radio Int."]   # Mi 00:30, über Mitternacht
assert "China Radio Int." not in names(datetime(2026, 11, 8, 0, 30, tzinfo=timezone.utc))                    # So (Beginn Sa): nicht Mo-Fr
assert "Radio Gloria" in names(datetime(2026, 11, 7, 13, 30, tzinfo=timezone.utc))                           # nur samstags
assert "Aqui Andorra" in names(datetime(2026, 12, 7, 17, 10, tzinfo=timezone.utc))                           # nur am 7. Dezember
assert "Aqui Andorra" not in names(datetime(2026, 12, 8, 17, 10, tzinfo=timezone.utc))
oa = E.on_air(6070, 5, now=datetime(2026, 11, 7, 13, 30, tzinfo=timezone.utc))
assert [d["station"] for d in oa][:2] == ["Radio Gloria", "Radio Channel 292"] or [d["station"] for d in oa][:2] == ["Radio Channel 292", "Radio Gloria"]
top = oa[0]
assert top["site_name"] == "Rohrbach" and 90 < top["distance_km"] < 130 and top["lang_name"] == "German", top
k = [d for d in E.on_air(6005, 1, now=datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)) if "Kall" in d["station"]][0]
assert k["site_name"] == "Kall-Krekel" and k["target_name"] == "Central Europe"
t = datetime(2026, 11, 7, 13, 30, tzinfo=timezone.utc)
assert [d["station"] for d in E.now_list(q="6070", now=t)["items"]] == ["Radio Channel 292", "Radio Gloria"]   # Frequenz
assert {d["station"] for d in E.now_list(q="german", now=t)["items"]} >= {"Radio Channel 292", "Radio Gloria"}  # Sprache
assert [d["station"] for d in E.now_list(q="rohrbach gloria", now=t)["items"]] == ["Radio Gloria"]             # Standort + Name
assert [d["station"] for d in E.now_list(lang="D", band="49m", now=t)["items"]] == ["Radio Channel 292", "Radio Gloria"]
# ohne Internet: Fehler wird vermerkt, die vorhandene Liste bleibt
E._get = lambda url, timeout=25: (_ for _ in ()).throw(OSError("Network is unreachable"))
st = E.refresh(force=True)
assert st.get("error") and E.load()["count"] == 8 and E.status()["count"] == 8
PY
echo "== 36 Betriebsfunk: NAVTEX, Wetterfax, Wetter-RTTY (450 Hz)"
(cd bin && timeout 400 python3 - <<'PY') && ok "NAVTEX mit Fehlerschutz, Wetterfax mit Phasing, DWD-RTTY 50 Bd/450 Hz, automatische Wahl nach Frequenz" || bad "Betriebsfunk"
import tempfile, time
from pathlib import Path
import numpy as np
from scipy.signal import resample_poly, hilbert
import live_decode as LD, navtex as NX, wefax as WF, decode_recordings as DR
R = 24000
def fsk(bits, baud, f0, f1, rate=R):
    sps = rate / baud; ph = 0.0; out = []
    for k, b in enumerate(bits):
        n = int(round((k + 1) * sps)) - int(round(k * sps)); f = f1 if b else f0
        w = ph + 2 * np.pi * f / rate * np.arange(1, n + 1); ph = w[-1]; out.append(np.sin(w))
    return np.concatenate(out) * 0.4
def live(kind, a, freq):
    LD.REPORTS = Path(tempfile.mkdtemp())
    d = LD.LiveDecoders(); d.running = False; d.freq_hz = freq; d.mode = "usb"; d._switch(kind)
    tt = 1_800_000_000.0
    for i in range(0, len(a), 2400):
        tt += 0.1; d._block_feed(tt, a[i:i + 2400].astype(np.float32))
        while d._busy:
            time.sleep(0.02)
    return d
assert LD.auto_kind(518000, "usb") == "navtex" and LD.auto_kind(7878100, "usb") == "wefax" and LD.auto_kind(10099300, "usb") == "rtty"
msg = "ZCZC SE42\nGERMAN BIGHT WIND SW 5 TO 6, LATER 7.\nNNNN\n"
codes = NX.fec_stream(NX.encode(msg))
bad = list(codes)
for i in range(1, len(bad), 26):
    bad[i] = 0x00                                                    # jede 13. Erstsendung zerstört
assert "GERMAN BIGHT WIND SW 5 TO 6, LATER 7." in NX.to_text(NX.defec(bad))
a = fsk(NX.bits_of(codes), 100, 1000, 1170)
a = np.concatenate([np.zeros(R * 2), a, np.zeros(R * 40)]) + 0.02 * np.random.default_rng(1).standard_normal(len(a) + R * 42)
d = live("navtex", a, 518000)
ev = [e for e in d.status()["events"] if e["type"] == "NAVTEX" and "laufend" not in (e.get("detail") or "")]
assert ev and "GERMAN BIGHT" in ev[0]["text"] and "Wettervorhersage" in ev[0]["detail"], d.status()["events"]
assert NX.decode((0.02 * np.random.default_rng(5).standard_normal(8000 * 30)).astype(complex), 8000) is None
H, W = 120, 600
y, x = np.mgrid[0:H, 0:W]
img = ((x // 40 + y // 20) % 2).astype(float) * 0.8 + 0.1
for off in (0.137, 0.41):
    f = WF.synth(img)
    f = np.concatenate([np.zeros(int(8000 * off), np.float32), f]) + 0.05 * np.random.default_rng(2).standard_normal(len(f) + int(8000 * off)).astype(np.float32)
    dd = WF.Decoder(); evs = []
    for i in range(0, len(f), 800):
        evs += [e for e in dd.feed(f[i:i + 800]) if e != "line"]
    im = dd.image(); ref = np.stack([np.interp(np.linspace(0, W - 1, WF.WIDTH), np.arange(W), r) * 255 for r in img])
    n = min(len(im), len(ref))
    assert evs == ["start", "stop"] and abs(len(im) - H) <= 4 and np.corrcoef(im[:n].astype(float).ravel(), ref[:n].ravel())[0, 1] > 0.85, (evs, im.shape)
d = live("wefax", resample_poly(WF.synth(np.tile(np.linspace(0, 1, 300), (60, 1))).astype(np.float64), 3, 1), 7880000)
assert any(e["type"] == "WEFAX" and e.get("img") for e in d.status()["events"]) and list(LD.REPORTS.glob("live_wefax_*.png"))
inv = {v: k for k, v in DR.ITA2_LETTERS.items() if v}
bits = [1] * 40
for ch in "RYRYRY DWD SEEWETTERBERICHT DEUTSCHE BUCHT ":
    c = inv[ch]; bits += [0] + [(c >> b) & 1 for b in range(5)] + [1, 1]
bits += [1] * 20
a = np.concatenate([fsk(bits, 50, 1725, 1275), np.zeros(R * 9)]) + 0.01 * np.random.default_rng(3).standard_normal(len(bits) * 480 + R * 9 + 100)[:len(bits) * 480 + R * 9]
d = LD.LiveDecoders(); d.set_kind("rtty")
pcm = (a * 32767).astype("<i2")
for i in range(0, len(pcm), 480):
    d.feed(pcm[i:i + 480].tobytes(), 10_099_300, "usb"); time.sleep(0.0003)
for _ in range(60):
    time.sleep(0.2)
    if d.status()["events"] and not d._busy:
        break
txt = " ".join(e["text"] for e in d.status()["events"]); d.stop()
assert "SEEWETTERBERICHT" in txt, txt
PY
echo "== 37 „Neu im letzten Lauf“ = wirklich der letzte Lauf"
(cd bin && python3 - <<'PY') && ok "neu zählt nur den letzten abgeschlossenen Lauf (auch ohne Treffer, unterbrochen oder fehlgeschlagen)" || bad "neu im letzten Lauf"
import webapp as W
db = W.db_connect()
db.execute("CREATE TABLE IF NOT EXISTS run (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, duration_s INTEGER, status TEXT, peaks INTEGER DEFAULT 0, rejected INTEGER DEFAULT 0, error TEXT)")
def run(st, a, b, status):
    db.execute("INSERT INTO run (started_at, finished_at, status, peaks) VALUES (?,?,?,0)", (a, b, status))
def hit(t, band="40m"):
    cur = db.execute("INSERT INTO scan (time, band, center_hz, noise_db, rms_i, rms_q) VALUES (?,?,?,?,?,?)", (t, band, 7e6, -100, 0, 0))
    db.execute("INSERT INTO peak (scan_id, rf_hz, offset_hz, level_db, snr_db) VALUES (?,?,?,?,?)", (cur.lastrowid, 7.07e6, 0, -60, 10))
run(1, "2030-01-01T10:00:00+00:00", "2030-01-01T10:05:00+00:00", "success"); hit("2030-01-01T10:02:00+00:00"); hit("2030-01-01T10:03:00+00:00")
run(2, "2030-01-01T10:30:00+00:00", "2030-01-01T10:33:00+00:00", "failed"); hit("2030-01-01T10:31:00+00:00")
hit("2030-01-01T10:40:00+00:00", "Satelliten")                     # Überflug zwischen den Läufen: kein Scan-Treffer
db.commit()
assert W.stats()["hits_new"] == 1, W.stats()["hits_new"]           # nur der letzte (fehlgeschlagene) Lauf
run(3, "2030-01-01T11:00:00+00:00", "2030-01-01T11:04:00+00:00", "cancelled"); db.commit()
assert W.stats()["hits_new"] == 0                                    # letzter Lauf ohne Treffer: 0, nicht der vorige
assert W.stats()["last_run"]["status"] == "cancelled"
run(4, "2030-01-01T11:30:00+00:00", None, "running"); db.commit()
assert W.stats()["hits_new"] == 0                                    # laufender Lauf zählt noch nicht
h = [x for x in W.list_hits({"limit": "50", "interference": "1"}) if x["time"].startswith("2030-01-01T10:31")]
assert h and h[0]["is_new"] is False
run(5, "2030-01-01T12:00:00+00:00", "2030-01-01T12:05:00+00:00", "success"); hit("2030-01-01T12:02:00+00:00"); db.commit()
only = W.list_hits({"limit": "50", "last": "1"})
assert [x["time"] for x in only] == ["2030-01-01T12:02:00+00:00"], [x["time"] for x in only]   # Filter „nur letzter Lauf“
db.execute("DELETE FROM run WHERE started_at LIKE '2030-%'"); db.execute("DELETE FROM peak WHERE scan_id IN (SELECT id FROM scan WHERE time LIKE '2030-%')")
db.execute("DELETE FROM scan WHERE time LIKE '2030-%'"); db.commit(); db.close()
PY
echo "== 38 EiBi im Scan (Chip wie Satelliten)"
(cd bin && timeout 120 python3 - <<'PY') && ok "EiBi-Chip in der Bandliste, Fenster in der Planung, Trägerpegel gemessen und protokolliert" || bad "EiBi im Scan"
import json, tempfile, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import eibi as E
E.DIR = Path(tempfile.mkdtemp()); E.CSV = E.DIR / "sked.csv"; E.README = E.DIR / "README.TXT"; E.STATE = E.DIR / "state.json"
h = datetime.now(timezone.utc).hour
lines = open("../devtools/fixtures/eibi_sked_sample.csv").read().splitlines()[:1] + [
    f"6070;{h:02d}00-{(h + 1) % 24:02d}00;;D;Radio Jetzt A;D;Eu;r;1;;", f"6085;{h:02d}00-{(h + 1) % 24:02d}00;;D;Radio Jetzt B;D;Eu;n;1;;",
    f"9420;{h:02d}00-{(h + 2) % 24:02d}00;;ROU;Radio Jetzt C;E;Eu;t;1;;"]
E.upload(("\n".join(lines) + "\n").encode()); E.upload(open("../devtools/fixtures/eibi_readme_sample.txt", "rb").read(), readme=True)
import webapp as W, scan_plan as SP, scanner as S
assert any(b["name"] == "EiBi" and len(b["segments"]) == 14 for b in W.bandplan())
st = json.load(open("../config/web_settings.json")); st.update(selected_bands=["EiBi"], active_device="pmsdr")
plan = SP.make_plan(None, st)
e = [p for p in plan if p["name"] == "EiBi"][0]
assert len(e["centers_hz"]) == 2 and [len(t) for t in e["targets"]] == [2, 1], e
assert all(abs(x["khz"] * 1000 - c) >= 1000 for c, t in zip(e["centers_hz"], e["targets"]) for x in t)   # nicht auf DC
class Dev:
    def tune(self, c): self.c = c
    def capture(self, sec):
        n = int(48000 * sec); t = np.arange(n) / 48000
        z = 0.01 * (np.random.default_rng(1).standard_normal(n) + 1j * np.random.default_rng(2).standard_normal(n))
        if abs(self.c - 6077500) < 30000:
            z = z + 0.003 * np.exp(2j * np.pi * (6070000 - self.c) * t)
        return z.real, z.imag
db = S.open_database()
assert S.eibi_scan(Dev(), {"device": "pmsdr", "sample_rate": 48000}, e, db) == 2
rows = {r[0]: (r[1], r[2]) for r in db.execute("SELECT station, snr_db, heard FROM eibi_log WHERE station LIKE 'Radio Jetzt%'")}
assert rows["Radio Jetzt A"][1] == 1 and rows["Radio Jetzt A"][0] > 20 and rows["Radio Jetzt B"][1] == 0 and rows["Radio Jetzt C"][1] == 0, rows
db.close()
PY
echo "== 39 Neues Netzwerk / neue IP-Adresse"
(cd bin && python3 - <<'PY') && ok "neue IP und anderes Netz werden gemeldet (mit Adresse, Locator-Hinweis), kein Fehlalarm" || bad "Netzwechsel"
import json, tempfile
from pathlib import Path
import health as H, notices as NT, notify as N
NT.FILE = Path(tempfile.mkdtemp()) / "n.json"; NT.LOCK = NT.FILE.with_suffix(".lock")
sent = []
N.cfg = lambda: {"push": "telegram", "tg_token": "x", "tg_chat": "1"}
N.send_push = lambda subject, text, c, image=None, priority="default": sent.append(subject)
seq = iter([
    {"iface": "eth0", "ip": "192.168.178.92", "gw": "192.168.178.1", "gwmac": "aa:aa"},   # erster Lauf: merken
    {"iface": "eth0", "ip": "192.168.178.92", "gw": "192.168.178.1", "gwmac": "aa:aa"},
    {"iface": "eth0", "ip": "192.168.178.95", "gw": "192.168.178.1", "gwmac": "aa:aa"},   # neue IP: erst vormerken
    {"iface": "eth0", "ip": "192.168.178.95", "gw": "192.168.178.1", "gwmac": "aa:aa"},   # bestätigt -> Meldung
    {"iface": "eth0", "ip": "10.0.5.23", "gw": "10.0.5.1", "gwmac": "bb:bb"},
    {"iface": "eth0", "ip": "10.0.5.23", "gw": "10.0.5.1", "gwmac": "bb:bb"},             # anderes Netz
])
H.net_state = lambda: next(seq)
st = {}
for _ in range(6):
    H.check_network(st)
items = [r for r in NT.listing()["items"] if r["key"] == "network"]
titles = sorted(r["title"] for r in items)
assert titles == ["Neue IP-Adresse: 192.168.178.95", "Neues Netzwerk: 10.0.5.23"], titles
newnet = [r for r in items if r["title"].startswith("Neues Netzwerk")][0]
assert "http://10.0.5.23:" in newnet["detail"] and "Locator" in newnet["fix"]
assert not [r for r in items if r["title"].startswith("Neue IP")][0]["fix"]                  # gleiches Netz: kein Locator-Hinweis
assert len(sent) == 2 and "network" in N.EVENT_KEYS, sent
import subprocess as _sp
class _R: stdout = "Power save: on\n"
_orig = _sp.run
_sp.run = lambda *a, **k: _R()
H.check_wifi_powersave({"net": {"iface": "wlan0"}})
assert any(r["key"] == "wifi_powersave" and r["active"] for r in NT.listing()["items"])     # WLAN schläft: Hinweis
_R.stdout = "Power save: off\n"
H.check_wifi_powersave({"net": {"iface": "wlan0"}})
assert not any(r["key"] == "wifi_powersave" and r["active"] for r in NT.listing()["items"])
_sp.run = _orig
PY
echo "== 40 Scan startet nicht (Sperre belegt): Meldung statt „angefordert 0 %“"
(cd bin && timeout 60 python3 - <<'PY') && ok "Sperrenhalter erkannt, abgelehnter Start gemeldet und Anzeige zurückgesetzt" || bad "abgelehnter Scanstart"
import ast, json, subprocess, sys, time, tempfile
from datetime import datetime, timezone
from pathlib import Path
import lockinfo as LI
lock = Path(tempfile.mkdtemp()) / "cycle.lock"
p = subprocess.Popen(["flock", str(lock), "sleep", "20"])
time.sleep(0.5)
h = LI.holders(lock)
assert any(pid in (p.pid,) or "sleep" in cmd or "flock" in cmd for pid, cmd in h), h
# run_scan / _status_back aus scheduler.py (das Modul selbst läuft beim Import als Endlosschleife)
src = open("scheduler.py", encoding="utf-8").read()
tree = ast.parse(src)
code = "\n".join(ast.get_source_segment(src, n) for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("_status_back", "run_scan"))
logs = []
g = {"json": json, "datetime": datetime, "timezone": timezone, "BASE": Path(tempfile.mkdtemp()), "subprocess": subprocess,
     "cycle_running": lambda: True, "scan_running": lambda: False, "log": lambda m: None,
     "applog": lambda *a, **k: logs.append((a, k))}
(g["BASE"] / "data").mkdir()
(g["BASE"] / "data" / "runtime_status.json").write_text(json.dumps({"state": "queued", "message": "Scan wurde angefordert"}))
LI.LOCKS["cycle"] = lock
exec(code, g)
g["run_scan"]()
st = json.loads((g["BASE"] / "data" / "runtime_status.json").read_text())
assert st["state"] == "error" and "nicht gestartet" in st["message"] and "PID" in st["message"], st
assert logs and logs[0][0][2] == "scan_start_rejected" and "PID" in logs[0][1]["holder"]
p.kill()
PY
echo "== 41 Aussetzer des Dashboards: Abbild aller Fäden"
(cd bin && timeout 60 python3 - <<'PY') && ok "hängt der Webserver, steht in log/webapp_stall.txt, was jeder Faden tut" || bad "Aussetzer-Wache"
import tempfile, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import webapp as W
W.STALL_FILE = Path(tempfile.mkdtemp()) / "webapp_stall.txt"
logs = []
W.APPLOG.log = lambda *a, **k: logs.append((a, k))
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        def blockiert_hier():
            time.sleep(4)
        blockiert_hier()
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass
srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
W.stall_loop(srv.server_address[1], first_wait=0, limit=2, every=0, rounds=1)
txt = W.STALL_FILE.read_text()
assert "blockiert_hier" in txt and "do_GET" in txt, txt[-800:]
assert logs and logs[0][0][2] == "webapp_stall" and logs[0][1]["dumped"]
PY
echo "== 42 Spektrumbild einer schnellen Stick-Aufnahme (1 MS/s): Speicher"
(cd bin && timeout 120 python3 - <<'PY') && ok "Spektrum aus dem 12-kHz-Basisband, Speicher begrenzt" || bad "Spektrum-Speicher"
import resource, tempfile
import numpy as np, soundfile as sf
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import iqload as IQ
r, n = 1024000, 1024000 * 8
t = np.arange(n, dtype=np.float32) / r
noise = 0.01 * np.random.default_rng(1).standard_normal(n).astype(np.float32)
d = tempfile.mkdtemp()
sf.write(f"{d}/rec.wav", np.stack([noise + 0.2 * np.cos(2 * np.pi * 150000 * t), noise + 0.2 * np.sin(2 * np.pi * 150000 * t)], 1).astype(np.float32), r, subtype="FLOAT")
del t, noise
base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
src = open("analyze_recordings.py", encoding="utf-8").read()
g = {}
exec("import numpy as np\nimport matplotlib.pyplot as plt\n" + src[src.index("def make_spectrum"):src.index("db = sqlite3.connect(DB)")], g)
bb, br = IQ.baseband(f"{d}/rec.wav", 150000, 12000, False)
assert abs(np.fft.fftfreq(len(bb), 1 / br)[np.argmax(np.abs(np.fft.fft(bb)))]) < 1         # Treffer bei 0 Hz
g["make_spectrum"](bb, br, 145e6, 145.15e6, f"{d}/spec.png")
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
assert peak - base < 120, (base, peak)
PY
echo "== 43 Satelliten: Horizont rundherum und Mindesthöhe"
(cd bin && timeout 120 python3 - <<'PY') && ok "aufgenommen wird nur über dem Horizont, zu niedrige Überflüge stehen ungeplant in der Liste" || bad "Horizont/Mindesthöhe"
import satellites as S
ids = [s["id"] for s in S.SATELLITES][:12]
a = S.upcoming(ids, "JN59HH", fetch=False, past_s=0, settings={})
b = S.upcoming(ids, "JN59HH", fetch=False, past_s=0, settings={"sat_horizon": 20, "sat_min_el": 30})
assert a["horizon"] == 0 and a["min_max_el"] == 10 and b["horizon"] == 20 and b["min_max_el"] == 30
A = {p["key"]: p for p in a["passes"]}
assert b["passes"] and all(p["key"] in A for p in b["passes"])
assert not [p for p in b["passes"] if p["max_el"] < 20]                       # nie über dem Horizont: entfällt
for p in b["passes"]:
    q = A[p["key"]]
    assert q["aos"] < p["aos"] < p["los"] < q["los"]                          # nur der Teil über 20°
    if p["max_el"] < 30:
        assert p.get("below") and p["status"] == "skipped" and not p.get("rec")
for r in b["recordings"]:
    assert all(A[k]["max_el"] >= 30 for k in r["keys"])
assert len(b["recordings"]) < len(a["recordings"])
PY

echo "== 44 Stick wieder angesteckt, Standortfrage nach Netzwechsel, Name im Netz"
(cd bin && timeout 120 python3 - <<'PY') && ok "„abgezogen“ erledigt sich, Standortfrage bis zur Antwort, hamscanpi.local angemeldet bzw. belegt erkannt" || bad "Stick/Standort/Name"
import json, os, stat, sys, tempfile, time
from pathlib import Path
import health as H, notices as NT
NT.FILE = Path(tempfile.mkdtemp()) / "n.json"; NT.LOCK = NT.FILE.with_suffix(".lock")
import sticks as STK
S = {"key": "rtl:27244444", "label": "NESDR SMArt v5", "serial": "27244444", "role": "main", "kind": "rtl", "port": "1-1.4"}
st = {}
for present in (True, False, True):
    STK.overview = lambda p=present: {"sticks": [dict(S, present=p)]}
    H.check_sticks(st)
items = NT.listing()["items"]
gone = [r for r in items if r["title"].startswith("Stick abgezogen")]
assert gone and not gone[0]["active"], gone                                   # beim Wiederkommen quittiert
assert any(r["title"].startswith("Stick wieder angesteckt") and r["active"] for r in items)
# Standortfrage: anderes Netz -> data/location_check.json; Webserver beantwortet sie
H.BASE = Path(tempfile.mkdtemp()); (H.BASE / "data").mkdir(); (H.BASE / "config").mkdir()
(H.BASE / "config" / "web_settings.json").write_text(json.dumps({"my_locator": "JN59HH"}))
seq = iter([{"iface": "eth0", "ip": "192.168.178.92", "gw": "192.168.178.1", "gwmac": "aa"}] * 2
           + [{"iface": "eth0", "ip": "10.0.5.23", "gw": "10.0.5.1", "gwmac": "bb"}] * 2)
H.net_state = lambda: next(seq)
st = {}
for _ in range(4):
    H.check_network(st)
lc = json.loads((H.BASE / "data" / "location_check.json").read_text())
assert lc["ip"] == "10.0.5.23" and lc["old_ip"] == "192.168.178.92"
# Name im Netz: avahi-publish nachgestellt (läuft = angemeldet, bricht mit „collision“ ab = belegt)
import mdns_alias as MD
MD.STATE = Path(tempfile.mkdtemp()) / "m.json"
MD._ip = lambda: "192.168.178.92"
d = tempfile.mkdtemp()
for name, body in (("ok", "sleep 30"), ("busy", "echo 'Failed to add address: Local name collision' >&2; exit 1")):
    os.makedirs(f"{d}/{name}", exist_ok=True)
    f = f"{d}/{name}/avahi-publish"
    open(f, "w").write("#!/bin/sh\n" + body + "\n"); os.chmod(f, 0o755)
os.environ["PATH"] = f"{d}/ok:" + os.environ["PATH"]
MD.run(8080, rounds=1, every=0, host="raspberrypi")
assert MD.state()["mode"] == "alias" and MD.state()["url"] == "http://hamscanpi.local:8080", MD.state()
os.environ["PATH"] = f"{d}/busy:" + os.environ["PATH"]
MD.run(8080, rounds=1, every=0, host="raspberrypi")
assert MD.state()["mode"] == "conflict" and MD.state()["name"] == "raspberrypi.local", MD.state()
MD.run(8080, rounds=1, every=0, host="HamScanPi")
assert MD.state()["mode"] == "hostname"
PY
echo "== 46 Kurzwelle je Stick per Hand"
(cd bin && python3 - <<'PY') && ok "„nein“ nimmt einem als v5 gemeldeten Stick die Kurzwelle, „ja“ gibt sie einem namenlosen" || bad "Kurzwelle per Hand"
import sdrcaps as SC
v5 = {"manufacturer": "Nooelec", "product": "NESDR SMArt v5", "tuner": "Rafael Micro R820T"}
assert SC.caps(v5)["hf"] == "direct_q" and SC.caps(dict(v5, hf_mode="auto"))["hf"] == "direct_q"
off = SC.caps(dict(v5, hf_mode="off"))
assert off["hf"] is None and off["min_hz"] == 24e6 and not SC.hf_ok(dict(v5, hf_mode="off"))[0]
gen = {"manufacturer": "Realtek", "product": "RTL2838UHIDIR", "tuner": "Rafael Micro R820T"}
assert SC.caps(gen)["hf"] is None and SC.caps(dict(gen, hf_mode="on"))["hf"] == "direct_q"
PY
echo "== 47 Satelliten: Kanalwahl nach Aussicht auf ein Signal"
(cd bin && python3 - <<'PY') && ok "ISS: 145,825 APRS statt drei meist stiller 70-cm-Kanäle; gehörte SSTV-Aktion dreht die Wahl um" || bad "Kanalwahl"
import tempfile, time
from pathlib import Path
import satellites as S
S.STATS_FILE = Path(tempfile.mkdtemp()) / "sat_stats.json"
ids = ["iss_sstv70", "iss", "iss_aprs", "iss_aprs70", "iss_rpt"]
ch, dropped = S.channel_group(ids, {})
assert set(ch) == {"iss", "iss_aprs"} and "iss_sstv70" in dropped, (ch, dropped)
for _ in range(5):
    S.record_channel("iss_sstv70", time.time() - 3600, True)
assert len(S.read_stats()["channels"]["iss_sstv70"]) == 5
ch, _ = S.channel_group(ids, {})
assert "iss_sstv70" in ch, ch
assert abs(S.channel_chance("meteor23", {}) - 0.95) < 1e-9 and S.channel_chance("so50", {}) == 0.35
PY
echo "== 48 Automatische Prüfung auf eine neue Version"
(cd bin && python3 - <<'PY') && ok "neuere Version gibt einen Hinweis (einmal je Version), aktuelle Version keinen; Prüfabstand aus der Einstellung" || bad "Versionsprüfung"
import json, tempfile, time
from pathlib import Path
import health as H, notices as NT, housekeeping as HK
NT.FILE = Path(tempfile.mkdtemp()) / "n.json"; NT.LOCK = NT.FILE.with_suffix(".lock")
H.BASE = Path(tempfile.mkdtemp()); (H.BASE / "VERSION").write_text("1.0.0\n"); cur = "1.0.0"
calls = []
def fake(force=False, max_age=86400, cached_only=False):
    calls.append((max_age, cached_only)); return {"latest": latest[0], "checked": time.time()}
HK.update_check = fake
H.update_days = lambda settings=None: 7
latest = ["99.0.0"]; st = {}
H.check_update(st); H.check_update(st)
act = [r for r in NT.listing()["items"] if r["key"] == "update_available" and r["active"]]
assert len(act) == 1 and "99.0.0" in act[0]["title"] and calls[0] == (7 * 86400, False), (act, calls)
latest = [cur]; H.check_update(st)
assert not [r for r in NT.listing()["items"] if r["key"] == "update_available" and r["active"]]
H.update_days = lambda settings=None: 0
H.check_update(st); assert calls[-1][1] is True                  # „nie“: nichts abfragen
PY
echo "== 49 Satelliten ohne NORAD-Nummer (Schlüssel mit Minus)"
(cd bin && python3 - <<'PY') && ok "sat_pass.py nimmt Schlüssel wie „-2_29898…“ an (Aufruf wie im Zeitplaner)" || bad "Schlüssel mit Minus"
import argparse, re
src = open("scheduler.py", encoding="utf-8").read()
assert '"--keys=" + ' in src
ap = argparse.ArgumentParser()                    # dieselben Optionen wie sat_pass.py
for line in open("sat_pass.py", encoding="utf-8"):
    if line.strip().startswith("ap.add_argument("):
        exec(line.strip())
a = ap.parse_args(["--sats", "hc1px", "--until", "1", "--keys=" + ",".join(["-2_29898123", "57166_2989"])])
assert a.keys == "-2_29898123,57166_2989" and a.sats == "hc1px", a
PY
echo "== 50 Reserve-Stick springt ein, Sicherung von einem anderen Pi"
(cd bin && python3 - <<'PY') && ok "fehlt der Haupt-Stick, arbeitet die Reserve mit ihren Einstellungen; nach dem Einspielen wird der angesteckte Stick Haupt-Stick" || bad "Reserve/Einspielen"
import json, tempfile
from pathlib import Path
import sticks as STK
d = Path(tempfile.mkdtemp()); STK.REG = d / "sticks.json"; STK.REG_LOCK = d / "sticks.lock"
big = {"port": "1-1.3", "vid": "0bda", "pid": "2838", "kind": "rtl", "manufacturer": "Nooelec", "product": "NESDR SMArt v5", "serial": "27244444", "node": None}
small = dict(big, port="1-1.4", serial="HSP24686")
cur = [[big, small]]
STK.usb_scan = lambda: list(cur[0])
reg, present, _ = STK.sync(notify=False)
kb, ks = STK.key_of(big), STK.key_of(small)
STK.update(kb, role="main"); STK.update(ks, role="spare", gain=33)
assert STK.main_device()[0]["serial"] == "27244444"
cur[0] = [small]                                             # Haupt-Stick abgezogen: Reserve springt ein
r = STK.main_device()[0]
assert r["serial"] == "HSP24686" and r["failover"] and r["replaces_serial"] == "27244444" and r["gain"] == 33
assert STK.effective_main()["serial"] == "HSP24686" and STK.overview()["failover"]["serial"] == "HSP24686"
assert STK.rtl_params({})["gain"] in (33, 33.0)
STK.update(ks, role=None)                                    # neuer, nicht festgelegter Stick springt nicht ein
reg = STK.load(); reg["sticks"][ks]["role"] = None; STK._save(reg)
assert STK.main_device() is None
cur[0] = [big]; assert STK.main_device()[0]["serial"] == "27244444"
# Sicherung eines anderen Pi: Haupt-Stick fehlt, genau ein anderer steckt -> übernommen
cur[0] = [small]
new, old = STK.adopt_after_restore()
reg = STK.load()
assert new["serial"] == "HSP24686" and reg["sticks"][ks]["role"] == "main" and reg["sticks"][kb]["role"] == "spare"
assert STK.adopt_after_restore() is None                      # schon passend: nichts zu tun
# Hinweis im Betrieb: Reserve springt ein (statt „Haupt-Stick fehlt“)
import health as H, notices as NT
NT.FILE = d / "n.json"; NT.LOCK = d / "n.lock"
STK.update(kb, role="main"); STK.update(ks, role="spare")
cur[0] = [small]
H.check_sticks({})
act = {r["key"] if r["kind"] == "event" else r["id"]: r for r in NT.listing()["items"] if r["active"]}
assert "stick_failover" in act and "stick_main_missing" not in act, list(act)
PY
echo "== 45 Update per git: Veröffentlichen (PC) und bin/update.sh (Pi)"
upd45() {
    # „set -e“ wirkt in einer &&-Kette nicht – jeder Schritt prüft selbst (|| return 1)
    local G; G="$(mktemp -d)"
    export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@x GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@x
    git config --global user.name >/dev/null || git config --global user.name t
    git config --global user.email >/dev/null || git config --global user.email t@x
    git init -q --bare -b main "$G/remote.git" || return 1
    mkdir -p "$G/src" "$G/pub" "$G/fake" && cp -r "$SRC/." "$G/src/" && rm -rf "$G/src/.git" "$G/src/data" "$G/src/log" || return 1
    echo 9.9.1 > "$G/src/VERSION"; chmod 644 "$G/src/bin/eibi.py"
    (cd "$G/src" && OUT="$G/pub/hesperos-publish.sh" bash devtools/build_publish.sh "Test 9.9.1" >/dev/null) || return 1
    (cd "$G/pub" && HSP_REPO="$G/remote.git" bash hesperos-publish.sh >/dev/null) || return 1
    git clone -q "$G/remote.git" "$G/pi" 2>/dev/null || return 1
    git -C "$G/pi" config core.fileMode true && chmod +x "$G/pi/bin/eibi.py"   # nur Rechte geändert (im Repo 644)
    echo 9.9.2 > "$G/src/VERSION"
    (cd "$G/src" && OUT="$G/pub/hesperos-publish.sh" bash devtools/build_publish.sh "Test 9.9.2" >/dev/null) || return 1
    (cd "$G/pub" && HSP_REPO="$G/remote.git" bash hesperos-publish.sh >/dev/null) || return 1
    git -C "$G/remote.git" tag | grep -q v9.9.2 || return 1
    printf '#!/bin/sh\necho "[sudo] $*"\n' > "$G/fake/sudo" && chmod +x "$G/fake/sudo"
    cp "$G/pi/config/web_settings.example.json" "$G/pi/config/web_settings.json"
    local OUTU; OUTU="$(cd "$G/pi" && HSP_SETUP_CMD="echo [setup]" HSP_USER="$(id -un)" bin/update.sh --now 2>&1)" || { echo "$OUTU" | tail -5; return 1; }
    [ "$(cat "$G/pi/VERSION")" = 9.9.2 ] || return 1
    echo "$OUTU" | grep -q "setup_system.sh --restart" || return 1
    grep -q "9.9.1 → 9.9.2" "$G/pi/log/update.log" || return 1
    rm -rf "$G"
}
upd45 && ok "Veröffentlicher legt Commit und Tag an, der Pi holt die Version und richtet das System ein" || bad "Update per git"
echo "== 51 Dashboard: jede Abfrage einmal (kein Programmfehler), Status-Karte, Prüfabstand"
api51() {
    local W; W="$(mktemp -d)"
    cp -r "$SRC/bin" "$SRC/web" "$SRC/config" "$SRC/share" "$SRC/bandplans" "$SRC/VERSION" "$W/" && mkdir -p "$W/data" "$W/log"
    cp "$W/config/web_settings.example.json" "$W/config/web_settings.json"; cp "$W/config/scanner.default.json" "$W/config/scanner.json"
    python3 - "$W" <<'PY' || return 1
import json, sys
p = sys.argv[1] + "/config/web_settings.json"; d = json.load(open(p)); d["update_check_days"] = 7; json.dump(d, open(p, "w"))
PY
    local PORT=$((20000 + RANDOM % 20000))
    (cd "$W" && exec python3 bin/webapp.py $PORT >"$W/web.log" 2>&1) & local WP=$!
    for i in $(seq 1 40); do curl -fs -m 2 "http://127.0.0.1:$PORT/api/ping" >/dev/null 2>&1 && break; sleep 0.5; done
    python3 - "$PORT" "$W/bin/webapp.py" <<'PY'; local rc=$?
import json, re, sys, urllib.request, urllib.error
port, src = sys.argv[1], open(sys.argv[2], encoding="utf-8").read()
paths = sorted(set(re.findall(r'if path == "(/api/[^"]+)"', src)))
bad = []
for p in paths:
    if p in ("/api/update",) or "diagnose" in p or "backup/download" in p or p.startswith("/api/live/"):
        continue
    try:
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}{p}", timeout=30).read()
    except urllib.error.HTTPError as e:
        body = e.read()
    except Exception as e:
        continue
    t = body[:400].decode("utf-8", "replace")
    if re.search(r"local variable|not associated with a value|is not defined|NameError|has no attribute", t):
        bad.append(f"{p}: {t[:120]}")
assert not bad, bad
s = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/system", timeout=30).read())
assert "disk" in s and "version" in s, s
u = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/update", timeout=30).read())
assert u["check_days"] == 7, u
PY
    kill $WP 2>/dev/null; wait $WP 2>/dev/null; rm -rf "$W"
    return $rc
}
api51 && ok "keine Abfrage bricht mit einem Programmfehler ab, Status-Karte liefert Werte, Prüfabstand 7 Tage" || bad "Dashboard-Abfragen"

echo "== 52 WLAN repariert sich selbst, Ausweich-WLAN, schwacher Empfang"
(cd bin && python3 - <<'PY') && ok "Stufen nach 2/5/9/14 min, Meldung beim Wiederkommen, Ausweich-WLAN unter dem Haupt-WLAN, Kabel = nichts tun" || bad "WLAN-Selbstheilung"
import tempfile
from pathlib import Path
import netheal as N, notices as NT
d = Path(tempfile.mkdtemp())
NT.FILE = d / "n.json"; NT.LOCK = d / "n.lock"; N.LOG = d / "wifi_log.json"
calls = []
state = {"up": True, "cable": False, "dbm": -60}
N.wifi_iface = lambda: "wlan0"
N.cable_ok = lambda: state["cable"]
N.link = lambda i: {"connected": state["up"], "ssid": "Haus" if state["up"] else None, "dbm": state["dbm"] if state["up"] else None}
N.gateway_ok = lambda i: (state["up"], "192.168.1.1")
nets = [{"name": "preconfigured", "ssid": "Haus", "prio": 0, "active": True, "fallback": False}]
def helper(*a, input=None, timeout=60):
    calls.append(a)
    if a[0] == "list":
        return True, "\n".join(f"{n['name']}\t{n['ssid']}\t{n['prio']}\t{'yes' if n['active'] else 'no'}" for n in nets)
    if a[0] == "add":
        nets.append({"name": "hsp-" + a[1], "ssid": a[1], "prio": int(a[2]), "active": False, "fallback": True})
    return True, ""
N.helper = helper
st, t = {}, 1_000_000.0
N.check(st, now=t)                                    # alles gut
state["up"] = False
for m in range(0, 16):                                # 15 min weg
    N.check(st, now=t + 60 + m * 60)
steps = [c[0] for c in calls if c[0] not in ("list", "scan")]
assert steps == ["reconnect", "radio", "restart-nm", "reload-driver"], steps
assert any(r["id"] == "wifi_down" and r["active"] for r in NT.listing()["items"])
state["up"] = True
N.check(st, now=t + 60 * 18)
items = NT.listing()["items"]
assert not any(r["id"] == "wifi_down" and r["active"] for r in items)
ev = [r for r in items if r["title"].startswith("WLAN war")]
assert ev and "WLAN-Treiber neu geladen" in ev[0]["detail"], ev
assert N.read_log()[-1]["minutes"] == 17, N.read_log()
# Ausweich-WLAN: Priorität unter dem Haupt-WLAN, jedes weitere darunter
p1 = N.add_fallback("Handy", "geheim123"); p2 = N.add_fallback("Nachbar", "")
assert p1 < 0 and p2 < p1, (p1, p2)
try:
    N.remove_fallback("preconfigured"); raise AssertionError("Haupt-WLAN gelöscht?")
except ValueError:
    pass
# Netzwerkkabel: nichts tun
calls.clear(); state.update(up=False, cable=True); st2 = {}
for m in range(20):
    N.check(st2, now=t + m * 60)
assert not [c for c in calls if c[0] != "list"], calls
# schwacher Empfang: Median der letzten 15 Messungen unter -75 dBm
state.update(up=True, cable=False, dbm=-82); st3 = {}
for m in range(15):
    N.check(st3, now=t + m * 60)
assert any(r["id"] == "wifi_weak" and r["active"] for r in NT.listing()["items"])
PY
bash -n "$SRC/share/root/hesperosdx-net" && ! bash "$SRC/share/root/hesperosdx-net" hostname "Böser Name" 2>/dev/null \
  && ! bash "$SRC/share/root/hesperosdx-net" remove preconfigured 2>/dev/null \
  && ok "Root-Helfer prüft seine Eingaben (falscher Rechnername, fremde Verbindung)" || bad "Root-Helfer"

echo "== 53 Name dieser Station: Rechnername, Mail, Namensdienst"
(cd bin && python3 - <<'PY') && ok "„Hesperos-AFGN“ → hesperos-afgn, Vorgabe bleibt hamscanpi, Betreff [Hesperos-AFGN], kein zusätzliches hamscanpi.local" || bad "Stationsname"
import json, tempfile
from pathlib import Path
import station as STN
assert STN.hostname_for("Hesperos-AFGN") == "hesperos-afgn" and STN.hostname_for("HamScanPi-AFGN") == "hamscanpi-afgn"
assert STN.hostname_for("Hesperos DX") == STN.hostname_for("HamScanPi") == STN.hostname_for("") == "hamscanpi"   # Vorgabe: Rechnername bleibt
assert STN.validate("HamScanPi") == "Hesperos DX" and not STN.custom({"station_name": "HamScanPi"})                  # alte Vorgabe
assert STN.hostname_for("Funkbude Müller") == "funkbude-mueller"
assert STN.validate("") == "Hesperos DX" and STN.name({}) == "Hesperos DX" and not STN.custom({})
for bad in ("x" * 33, "<script>", "-abc"):
    try:
        STN.validate(bad); raise AssertionError(bad)
    except ValueError:
        pass
assert STN.custom({"station_name": "Hesperos-AFGN"})
d = Path(tempfile.mkdtemp()); (d / "config").mkdir()
(d / "config" / "web_settings.json").write_text(json.dumps({"station_name": "Hesperos-AFGN"}))
STN.BASE = d
import notify as NF
sent = {}
class FakeSMTP:
    def __init__(self, *a, **k): pass
    def starttls(self): pass
    def login(self, *a): pass
    def send_message(self, m): sent["subj"], sent["from"] = m["Subject"], m["From"]
    def quit(self): pass
NF.smtplib.SMTP = FakeSMTP
NF.send_mail("Test", "x", {"smtp_host": "h", "mail_to": "a@b", "mail_from": "pi@example.org", "smtp_port": 587})
assert sent["subj"] == "[Hesperos-AFGN] Test" and sent["from"].startswith("Hesperos-AFGN"), sent
import mdns_alias as MD
MD.STATE = d / "m.json"; MD._ip = lambda: "192.168.1.5"
MD.run(8080, rounds=1, every=0, host="hamscanpi-afgn", custom=True)
assert MD.state()["mode"] == "hostname" and MD.state()["name"] == "hamscanpi-afgn.local", MD.state()
PY

echo "== 54 PMSDR-Steuerprogramm: Quellen, Korrektur, Meldungen"
pm54() {
    local P; P="$(mktemp -d)"
    echo "7131124daf136e0b16a10efc50200da484b8078c0faac53d01a96e75a5fca947  $SRC/share/pmsdr/pmsdr-0.26.tar.gz" | sha256sum -c --quiet || return 1
    tar xzf "$SRC/share/pmsdr/pmsdr-0.26.tar.gz" -C "$P" || return 1
    grep -q "unsigned int nbytes = sizeof szLine - 1;" "$P/pmsdr-0.26/pmsdr.c" || return 1
    sed -n 's/unsigned int nbytes = sizeof szLine - 1;/size_t nbytes = sizeof szLine - 1;/p' "$SRC/bin/setup_system.sh" | grep -q "sed -i" || return 1
    sed -i 's/unsigned int nbytes = sizeof szLine - 1;/size_t nbytes = sizeof szLine - 1;/' "$P/pmsdr-0.26/pmsdr.c"
    [ "$(grep -c 'size_t nbytes = sizeof szLine - 1;' "$P/pmsdr-0.26/pmsdr.c")" = 1 ] || return 1
    # nicht über ./configure bauen: dessen config.guess (2009) kennt aarch64 nicht – auf dem 64-Bit-Pi scheiterte 1.7.93
    grep -q "pmsdr-0.26\" && asuser ./configure\|asuser ./configure" "$SRC/bin/setup_system.sh" && return 1
    grep -q "asuser gcc -D__USE_GNU -std=gnu99 -fgnu89-inline -O2 -o pmsdr" "$SRC/bin/setup_system.sh" || return 1
    # mit libusb-Entwicklerpaket: genau den Befehl des Installers ausführen, mit den strengen Regeln von GCC 14
    if [ -f /usr/include/libusb-1.0/libusb.h ] && [ -f /usr/include/readline/readline.h ]; then
        (cd "$P/pmsdr-0.26" && gcc -Werror=incompatible-pointer-types -Werror=implicit-function-declaration -Werror=int-conversion \
            -D__USE_GNU -std=gnu99 -fgnu89-inline -O2 -o pmsdr cmdparser.c pmsdr.c pmsdr_ctrl.c pmsdr_err.c udp_cmd.c \
            -lusb-1.0 -lreadline -lpthread -lm >/dev/null 2>&1 && ./pmsdr -h 2>&1 | grep -q "PMSDR") || return 1
        echo "   gebaut und gestartet (strenge Compiler-Regeln)"
        if command -v aarch64-linux-gnu-gcc >/dev/null; then
            (cd "$P/pmsdr-0.26" && aarch64-linux-gnu-gcc -Werror=incompatible-pointer-types -Werror=implicit-function-declaration \
                -Werror=int-conversion -D__USE_GNU -std=gnu99 -fgnu89-inline -O2 -c cmdparser.c pmsdr.c pmsdr_ctrl.c pmsdr_err.c udp_cmd.c \
                >/dev/null 2>&1) || return 1
            echo "   übersetzt auch für aarch64 (64-Bit-Pi)"
        fi
    fi
    rm -rf "$P"
    (cd bin && python3 - <<'PY') || return 1
import devices as D
D.Path = type("P", (), {"__init__": lambda s, p: None, "exists": lambda s: False})
m = D.pmsdr_control_problem()
assert "Steuerprogramm fehlt" in m and "sudo systemctl start" not in m, m
PY
}
pm54 && ok "Archiv geprüft, Korrektur greift genau einmal, Meldung nennt den richtigen Schritt" || bad "PMSDR-Steuerprogramm"
echo "== 55 Einheitliches Speichern, Designs, Kurzwellen-Block entfernt"
(python3 - "$SRC/web" <<'PY') && ok "keine Speichern-Knöpfe mehr sichtbar, Rückmeldung als Einblendung, alle Designs vollständig" || bad "Speichern/Designs"
import re, sys
from pathlib import Path
w = Path(sys.argv[1])
html, js, css = (w / "index.html").read_text(), (w / "app.js").read_text(), (w / "style.css").read_text()
# Speichern-Knöpfe: nur noch versteckt (Ausnahme: Standort-Abfrage im Dialog, Passwort setzen)
for m in re.finditer(r'<button[^>]*>(Speichern|Netze speichern)</button>', html):
    tag = m.group(0)
    assert " hidden" in tag or 'id="locSave"' in tag or re.search(r'<div class="actions" hidden>\s*$', html[max(0, m.start() - 40):m.start()]), tag
assert 'data-a="save" hidden>' in js
for wire in ("autoSave($('antRows')", "autoSave($('panel-backup')", "autoSave($('wxBox')", "autoSave($('notifyForm')", "[data-follow]"):
    assert wire in js, wire
assert "toast(msg, 2800, !!err)" in js
# Kurzwellen-Block ist weg
assert "hfDevice" not in html and "$('hf" not in js and "renderHfDevice" not in js
# Designs: jedes Design setzt alle Farbwerte
root = re.search(r":root \{(.*?)\n\}", css, re.S).group(1)
toks = {t for t in re.findall(r"--([a-z0-9-]+):", root) if t not in ("radius", "tap", "font", "mono")}
for th in ("light", "contrast", "night", "ocean", "forest", "tube", "aubergine"):
    blk = re.search(r':root\[data-theme="%s"\] \{(.*?)\}' % th, css, re.S).group(1)
    miss = toks - set(re.findall(r"--([a-z0-9-]+):", blk))
    assert not miss, (th, miss)
auto = re.search(r':root\[data-theme="auto"\] \{(.*?)\}', css, re.S).group(1)
assert not toks - set(re.findall(r"--([a-z0-9-]+):", auto))
# keine fest eingebaute dunkle Fläche mehr außerhalb der Variablen (Kartenbeschriftung und Wasserfall bleiben dunkel)
rest = re.sub(r":root[^{]*\{.*?\}", "", css, flags=re.S)
for bad in ("rgba(15,22,32,.9", "#0a0f16", "#2b1d00", "#a78bfa"):
    assert bad not in rest, bad
assert '<script src="/static/theme.js"></script>' in html and '<script src="/static/theme.js"></script>' in (w / "login.html").read_text()
PY
(node - "$SRC/web/theme.js" <<'JS') && ok "Design je Browser: Wahl bleibt, Unsinn wird „Automatisch“, gesperrter Speicher stört nicht" || bad "Design-Umschalter"
const src = require('fs').readFileSync(process.argv[2], 'utf8');
function run(store, throwing) {
  const attrs = {}, metas = { 'color-scheme': { c: '' }, 'theme-color': { c: '' } };
  const document = { documentElement: { setAttribute: (k, v) => attrs[k] = v },
    querySelector: q => { const n = (q.match(/name="([^"]+)"/) || [])[1]; return metas[n] ? { setAttribute: (k, v) => metas[n].c = v } : null; } };
  const localStorage = throwing ? { getItem() { throw new Error('x'); }, setItem() { throw new Error('x'); } }
    : { getItem: k => store[k] ?? null, setItem: (k, v) => store[k] = v };
  const window = {};
  new Function('window', 'document', 'localStorage', 'matchMedia', src)(window, document, localStorage, () => ({ matches: false, addEventListener() {} }));
  return { T: window.HSPTheme, attrs, metas };
}
const st = {};
let r = run(st); if (r.attrs['data-theme'] !== 'auto') throw 'default';
r.T.set('night'); if (st.theme !== 'night' || r.attrs['data-theme'] !== 'night' || r.metas['theme-color'].c !== '#080000') throw 'set';
r = run(st); if (r.attrs['data-theme'] !== 'night') throw 'remembered';
r.T.set('quatsch'); if (r.T.get() !== 'auto') throw 'invalid';
r = run({}, true); r.T.set('light'); if (r.attrs['data-theme'] !== 'light' || r.metas['color-scheme'].c !== 'light') throw 'blocked storage';
if (r.T.next() !== 'ocean') throw 'next ' + r.T.next();
for (const th of ['ocean', 'forest', 'tube', 'aubergine']) { r.T.set(th); if (r.attrs['data-theme'] !== th) throw 'theme ' + th; }
JS

(python3 - "$SRC/web" <<'PY') && ok "jede Karte hat eine feste Kennung (Anordnung je Browser), Griff und Zurücksetzen vorhanden" || bad "Karten verschieben"
import re, sys
from pathlib import Path
w = Path(sys.argv[1]); html = (w / "index.html").read_text(); js = (w / "layout.js").read_text()
tags = re.findall(r'<(?:section|details) class="panel[^"]*"[^>]*>', html)
ids = [re.search(r'id="([^"]+)"', t) for t in tags]
assert all(ids), [t for t, i in zip(tags, ids) if not i]
ids = [i.group(1) for i in ids]
assert len(ids) == len(set(ids)), "doppelte Kennung"
assert '<script src="/static/layout.js"></script>' in html and 'id="layoutReset"' in html
for k in ("pointerdown", "window.addEventListener('pointermove'", "localStorage", "ArrowUp", "layoutchange"):
    assert k in js, k
PY

echo "== 56 Sicherung auf USB-Stick und Netzwerkfreigabe"
(cd bin && python3 - <<'PY') && ok "Stick erkannt (Systemkarte nicht), Ziel gemerkt, nach Wiederanstecken eingehängt, Auswerfen respektiert, Freigabe alle 10 min, Sicherung vom Ziel einspielbar" || bad "USB/NAS-Sicherung"
import json, os, tempfile, socket
from pathlib import Path
import storage as STO, backup as BK
tmp = Path(tempfile.mkdtemp())
STO.STATE = tmp / "storage_state.json"
STO.USB_MP, STO.NAS_MP = tmp / "usb", tmp / "nas"
for p in (STO.USB_MP, STO.NAS_MP):
    p.mkdir()
cfgf = Path("../config/web_settings.json")
lsblk = {"blockdevices": [
  {"name": "mmcblk0", "type": "disk", "tran": None, "rm": False, "hotplug": False, "children": [
     {"name": "mmcblk0p2", "path": "/dev/mmcblk0p2", "type": "part", "fstype": "ext4", "uuid": "root-uuid", "size": 3e10, "mountpoint": "/"}]},
  {"name": "sda", "type": "disk", "tran": "usb", "rm": True, "hotplug": True, "model": "SanDisk ", "children": [
     {"name": "sda1", "path": "/dev/sda1", "type": "part", "fstype": "vfat", "uuid": "1234-ABCD", "label": "STICK", "size": 32e9, "mountpoint": None}]},
  {"name": "sdb", "type": "disk", "tran": "usb", "rm": False, "hotplug": True, "model": "Ext", "children": [
     {"name": "sdb1", "path": "/dev/sdb1", "type": "part", "fstype": "ntfs", "uuid": "AAAA-1", "size": 1e12},
     {"name": "sdb2", "path": "/dev/sdb2", "type": "part", "fstype": "hfsplus", "uuid": "BBBB-2", "size": 1e11}]},
]}
def run(cmd, timeout=30, input=None):
    if cmd[0] == "lsblk" and "-J" in cmd:
        return 0, json.dumps(lsblk), ""
    if cmd[0] == "findmnt":
        return 0, "/dev/mmcblk0p2\n", ""
    if cmd[0] == "lsblk":
        return 0, "mmcblk0\n", ""
    return 1, "", "?"
STO._run = run
dr = STO.drives()
assert [d["uuid"] for d in dr] == ["1234-ABCD", "AAAA-1", "BBBB-2"], dr
assert dr[0]["fs_name"] == "FAT32" and dr[1]["supported"] and not dr[2]["supported"]
mounts, calls, present = set(), [], {"1234-ABCD": True}
STO.mounted = lambda mp: str(mp) in mounts
STO._present = lambda u: present.get(u, False)
def helper(*a, input=None, timeout=60):
    calls.append(a)
    if a[0] == "mount-usb":
        if not present.get(a[1]): return False, "Datenträger nicht gefunden – eingesteckt?"
        mounts.add(str(STO.USB_MP))
    if a[0] == "umount-usb": mounts.discard(str(STO.USB_MP))
    if a[0] == "mount-nas":
        if a[1] == "//nas/zu": return False, "Zugang verweigert – Benutzer oder Passwort falsch"
        if present.get("nas_down"): return False, "Rechner nicht erreichbar – Adresse prüfen"
        mounts.add(str(STO.NAS_MP))
    if a[0] in ("umount-nas", "nas-forget"): mounts.discard(str(STO.NAS_MP))
    return True, ""
STO.helper = helper
d = STO.use_usb("1234-ABCD")
assert d == str(STO.USB_MP / STO.SUB / socket.gethostname()) and Path(d).is_dir()
st = json.loads(cfgf.read_text()); st["auto_backup"] = {"enabled": True, "target": {"kind": "usb", "uuid": "1234-ABCD", "label": "STICK"}}
cfgf.write_text(json.dumps(st))
c = BK.auto_cfg()
assert c["dir"] == d, c
assert BK.check_dir(c["dir"], c["target"]) is None
# Sicherung landet im Ordner dieses Pi
res = BK.run_auto(c, log=lambda m: None)
assert Path(res["file"]).parent == Path(d)
# auswerfen: bleibt draußen, bis abgezogen; wieder angesteckt: eingehängt
STO.eject_usb()
assert not STO.mounted(STO.USB_MP)
STO.ensure(now=1e6); assert not STO.mounted(STO.USB_MP), "ausgeworfen, aber wieder eingehängt"
assert "ausgeworfen" in BK.check_dir(c["dir"], c["target"])
present["1234-ABCD"] = False; STO.ensure(now=1e6 + 60)
present["1234-ABCD"] = True; STO.ensure(now=1e6 + 120)
assert STO.mounted(STO.USB_MP), "nach dem Wiederanstecken nicht eingehängt"
present["1234-ABCD"] = False; mounts.clear()
assert "steckt nicht" in BK.check_dir(c["dir"], c["target"])
# Sicherungen auf dem Ziel: auch die eines anderen Pi; nur Dateien darunter
mounts.add(str(STO.USB_MP))
other = STO.USB_MP / STO.SUB / "anderer-pi"; other.mkdir(parents=True)
(other / "hamscanpi-backup-20260101-0300-data.tar.gz").write_bytes(b"x")
lst = STO.list_backups()
assert len(lst) == 2 and lst[0]["name"] > lst[1]["name"] or lst[0]["host"] == socket.gethostname(), lst
assert {x["host"] for x in lst} == {socket.gethostname(), "anderer-pi"}, lst
STO.checked_path(other / "hamscanpi-backup-20260101-0300-data.tar.gz")
for badp in ("/etc/passwd", str(other / ".." / ".." / ".." / "x.tar.gz"), str(tmp / "hamscanpi-backup-20260101-0300-data.tar.gz")):
    try:
        STO.checked_path(badp); raise AssertionError(badp)
    except ValueError:
        pass
# NAS: Adresse prüfen, Passwort nur an den Helfer (stdin), Ausfall: höchstens alle 10 min neu versuchen
assert STO.norm_share("smb://nas.local/Backup/") == "//nas.local/Backup"
assert STO.norm_share("\\\\192.168.1.5\\Daten") == "//192.168.1.5/Daten"
for b in ("//nas/a,b", "//nas/../etc", "nas", "//a b/x"):
    try:
        STO.norm_share(b); raise AssertionError(b)
    except ValueError:
        pass
calls.clear()
share, nd = STO.use_nas("//nas/backup", "pi", "geheim", "")
assert ("nas-cred",) in calls and all("geheim" not in " ".join(map(str, c)) for c in calls), calls
try:
    STO.use_nas("//nas/zu", "pi", "falsch"); raise AssertionError("falsches Passwort angenommen")
except ValueError as e:
    assert "verweigert" in str(e)
st["auto_backup"]["target"] = {"kind": "nas", "share": "//nas/backup"}; cfgf.write_text(json.dumps(st))
mounts.discard(str(STO.NAS_MP)); calls.clear(); present["nas_down"] = True
for m in range(10):
    STO.ensure(now=2e6 + m * 60)
assert [c[0] for c in calls].count("mount-nas") == 1, calls
STO.ensure(now=2e6 + 601); assert [c[0] for c in calls].count("mount-nas") == 2
assert "nicht erreichbar" in STO.status()["nas"]["error"]
present["nas_down"] = False; STO.ensure(now=2e6 + 1202); assert STO.mounted(STO.NAS_MP)
assert "geheim" not in cfgf.read_text()
PY
H="$SRC/share/root/hesperosdx-store"
bash -n "$H" && ! bash "$H" mount-usb "../../etc" 2>/dev/null && ! bash "$H" mount-nas "//nas/a,uid=0" 2>/dev/null \
  && ! printf 'pi\n\001x\n\n' | bash "$H" nas-cred 2>/dev/null && ! bash "$H" format 2>/dev/null \
  && grep -q "hesperosdx-store" "$SRC/bin/setup_system.sh" && grep -q "cifs-utils" "$SRC/bin/setup_system.sh" \
  && ok "Root-Helfer prüft UUID, Adresse und Zugangsdaten, formatiert nie; Installer richtet ihn ein" || bad "Root-Helfer Sicherungsziel"

echo "== 57 Schreiblast, Scan nach Neustart, Hinweise aufräumen, Netz-Angaben"
(cd bin && python3 - <<'PY') && ok "Überflug-Zwischendateien int16 (halbe Schreiblast, alte float32 lesbar), Pegel/Lebenszeichen im RAM, Diagnose misst Schreiblast" || bad "Schreiblast"
import numpy as np, tempfile, os
from pathlib import Path
import sat_pass as SP, rundir as RD, diag
d = Path(tempfile.mkdtemp())
z = (np.random.randn(48000) + 1j * np.random.randn(48000)).astype(np.complex64) * 0.3
(d / "a.r16").write_bytes(SP.to_i16(np.stack([z.real, z.imag], 1), SP.IQ_SCALE).tobytes())
np.stack([z.real, z.imag], 1).astype(np.float32).tofile(d / "a.raw")
for f in ("a.r16", "a.raw"):
    m, k = SP.open_iq(d / f)
    zz = (m[:, 0].astype(np.float32) + 1j * m[:, 1].astype(np.float32)) * k
    assert np.max(np.abs(zz - z)) < 2e-4, f
assert (d / "a.r16").stat().st_size * 2 == (d / "a.raw").stat().st_size
a = (np.random.randn(24000) * 0.8).astype(np.float32)
(d / "t.a16").write_bytes(SP.to_i16(a, SP.AUD_SCALE).tobytes())
A = SP.open_audio(d / "t.a16")
assert len(A) == 24000 and np.max(np.abs(A[100:200] - a[100:200])) < 2e-4 and np.allclose(SP.read_audio(d / "t.a16"), a, atol=2e-4)
assert str(SP.METER).startswith("/dev/shm/") and str(RD.path("scheduler.heartbeat")).startswith("/dev/shm/")
t = diag.io_sample(1)
assert "geschrieben" in t and "/tmp:" in t, t
PY
(cd bin && python3 - <<'PY') && ok "„aus“ bleibt aus, auch wenn die Einstellungen kurz nicht lesbar sind; liegengebliebene Scan-Anforderung verfällt" || bad "Scan nach Neustart"
import ast, json, tempfile
from pathlib import Path
src = Path("scheduler.py").read_text()
fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "interval_minutes")
d = Path(tempfile.mkdtemp()); f = d / "s.json"
logs = []
ns = {"json": json, "SETTINGS": f, "DEFAULT_INTERVAL": 30, "_sat_note": {}, "applog": lambda *a, **k: logs.append(a)}
exec(compile(ast.Module([fn], []), "x", "exec"), ns)
assert ns["interval_minutes"]() == 30                      # frische Installation
f.write_text('{"scan_interval_minutes": 0}'); assert ns["interval_minutes"]() == 0
f.write_text('{"scan_interval_mi'); assert ns["interval_minutes"]() == 0 and logs, "kaputte Datei darf nicht scannen"
f.write_text('{"scan_interval_minutes": 10}'); assert ns["interval_minutes"]() == 10
assert "_age > 600" in src and "trigger_expired" in src
PY
(cd bin && python3 - "$SRC" <<'PY') && ok "Sicherung aus/anderes Ziel: alter Fehler und Hinweis verschwinden; quittierte Hinweise ausgeblendet" || bad "Hinweise aufräumen"
import json, sys, tempfile
from pathlib import Path
import backup as BK, notices as NT
d = Path(tempfile.mkdtemp()); NT.FILE = d / "n.json"; NT.LOCK = d / "n.lock"; BK.AUTO_STATE = d / "bs.json"
BK.AUTO_STATE.write_text(json.dumps({"error": "Zielordner /mnt/usb gibt es nicht", "tried_day": "2026-11-14"}))
NT.raise_("auto_backup", "warn", "storage", "Automatische Sicherung fehlgeschlagen", "x", notify=False)
BK.auto_cfg = lambda settings=None: {"enabled": False}
BK.auto_tick()
assert not BK.auto_state().get("error") and not NT.is_active("auto_backup")
js = (Path(sys.argv[1]) / "web" / "app.js").read_text()
assert "showOld || (r.active && !r.acked)" in js
PY
(cd bin && python3 - <<'PY') && ok "System → Raspberry Pi: Schnittstellen mit MAC, Router, Namensserver (zwischengespeichert)" || bad "Netz-Angaben"
import sysmon as M
n = M.network()
assert {"ifaces", "gateway", "dns", "hostname"} <= set(n), n
assert all({"name", "mac", "wifi"} <= set(f) for f in n["ifaces"])
assert M.net_details() is M.net_details()                  # 30 s zwischengespeichert
PY
grep -q '"/favicon.ico"' "$SRC/bin/webapp.py" && [ -s "$SRC/web/favicon.ico" ] && ok "favicon.ico vorhanden" || bad "favicon.ico"

echo "== 58 SSTV-Bilder auf die Webseite (WordPress-Plugin)"
(cd bin && python3 - <<'PY') && ok "Warteschlange: Anmeldung, feste Kennung, Entwurf, Mindest-Vollständigkeit, wachsendes Live-Bild, ohne Netz später, Passwort nie sichtbar" || bad "Webseite"
import base64, json, tempfile, threading, time, http.server
from pathlib import Path
import publish as P, backup as BK
d = Path(tempfile.mkdtemp()); P.QUEUE = d / "q.json"; P.LOG = d / "l.json"
got, state = [], {"down": False}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _ok(self):
        return self.headers.get("Authorization") == "Basic " + base64.b64encode(b"tom:abcd efgh").decode()
    def do_GET(self):
        if not self._ok(): self.send_response(401); self.end_headers(); return
        b = json.dumps({"ok": True, "version": "1.0.0", "user": "tom", "can_publish": True}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        n = int(self.headers["Content-Length"]); body = self.rfile.read(n)
        if not self._ok(): self.send_response(401); self.end_headers(); return
        assert self.path == "/wp-json/hsp-sstv/v1/images" and b"\x89PNG" in body and b'name="uid"' in body
        meta = json.loads(body.split(b'name="meta"\r\n\r\n')[1].split(b"\r\n--")[0])
        st = body.split(b'name="status"\r\n\r\n')[1].split(b"\r\n")[0].decode()
        got.append((meta, st))
        b = json.dumps({"ok": True, "id": len(got), "status": st, "edit": "http://x/edit"}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(b)
srv = http.server.HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}"
c = {"enabled": True, "url": url, "user": "tom", "app_password": "abcd efgh", "status": "draft", "min_complete": 60}
P.cfg = lambda settings=None: dict(c)
assert P.ping()["user"] == "tom"
png = d / "a_sstv.png"; P.test_image(png)
good = P.build("ISS", time.time(), {"mode": "PD120", "complete": 0.93}, [{"title": "Satellit und Überflug", "rows": [["Satellit", "ISS"]]}], {"kind": "satellite"})
bad_ = P.build("ISS", time.time(), {"mode": "PD120", "complete": 0.41}, [], {})
assert [s["title"] for s in good["sections"]][:2] == ["Bild", "Satellit und Überflug"] and good["sections"][-1]["title"].startswith("Raspberry Pi")
assert "UTC" in good["sections"][0]["rows"][2][1]
u1 = P.enqueue(png, good); P.enqueue(png, bad_, key="kaputt")
u1b = P.enqueue(png, good)                                   # gleiche Kennung: kein zweiter Eintrag
assert u1 == u1b and len(P.queue()) == 2
P.enqueue(png, good, key="live-1", settle_s=90)              # wächst noch
assert P.tick() == 1 and got[-1][1] == "draft" and got[-1][0]["summary"]["source"] == "ISS"
assert [e["key"] for e in P.queue()] == ["live-1"]
assert any(e["state"] == "zu unvollständig" for e in P.read_log())
assert P.tick(now=time.time() + 100) == 1                    # Live-Bild nach 90 s unverändert
# ohne Netz: liegen lassen, später nachschicken
srv.shutdown(); srv.server_close()
P.enqueue(png, good, key="spaeter")
assert P.tick() == 0 and P.queue()[0]["tries"] == 1 and "nicht erreichbar" in P.queue()[0]["error"]
# Passwort: nie im Dashboard, nicht in Sicherungen ohne Zugangsdaten
assert "app_password" not in P.public_cfg({"publish": c}) and P.public_cfg({"publish": c})["has_password"]
try:
    P.validate(dict(c, url="http://do1gmt.de")); raise AssertionError("http ins Internet erlaubt")
except ValueError:
    pass
assert P.validate(dict(c, url="do1gmt.de"))["url"] == "https://do1gmt.de"
cf = Path("../config/web_settings.json"); s = json.loads(cf.read_text()); s["publish"] = c; cf.write_text(json.dumps(s))
arc = dict(BK._config_files(False))["config/web_settings.json"]
assert b"abcd efgh" not in arc and b"app_password" not in arc
PY
phpok() { command -v php >/dev/null || return 0; php -l "$SRC/share/wordpress/hesperosdx-sstv-galerie/hesperosdx-sstv-galerie.php" >/dev/null 2>&1 && php -l "$SRC/share/wordpress/hesperosdx-sstv-galerie/includes/galerie-plugin.php" >/dev/null 2>&1; }
phpok && node -e "new Function(require('fs').readFileSync('$SRC/share/wordpress/hesperosdx-sstv-galerie/assets/galerie.js','utf8'))" \
  && grep -q "Plugin Name: *Hesperos DX SSTV-Galerie" "$SRC/share/wordpress/hesperosdx-sstv-galerie/hesperosdx-sstv-galerie.php" \
  && ok "WordPress-Plugin: PHP und Skript fehlerfrei" || bad "WordPress-Plugin"

echo "== 59 Zeitangaben: UTC oder Ortszeit, überall mit Kennung"
OUT59="$(node "$SRC/devtools/tz_harness.js" "$SRC/web/app.js" 2>&1)"
[ "$OUT59" = "$(printf '%s\n' '18:05 UTC' 'Sa 14.11. 18:05:09 UTC' 'Sa 14.11.2026 18:05 UTC' '19:05 MEZ' 'MESZ' 'Sa' '{"days":[4],"from":"18:00","to":"18:30"}' '{"days":[6],"from":"23:30","to":"01:00"}' '13:00' '{"days":[0],"from":"00:30","to":"02:00"}' '14:00')" ] \
  && ok "Dashboard: UTC und Ortszeit mit Kennung (MEZ/MESZ), Zeitpläne über Mitternacht und Tagesgrenze umgerechnet, EiBi-Zeiten" \
  || { echo "$OUT59"; bad "Zeitangaben im Dashboard"; }
(python3 - "$SRC" <<'PY') && ok "keine Zeitangabe mehr am Einstellungs-Weg vorbei; Tabellenköpfe tragen die Kennung" || bad "Zeitangaben vollständig"
import re, sys
from pathlib import Path
w = Path(sys.argv[1]) / "web"
js, html = (w / "app.js").read_text(), (w / "index.html").read_text()
bad = [l for l in js.splitlines() if re.search(r"toLocaleTimeString|toLocaleDateString|toLocaleString\([^)]*hour|\.getHours\(\)", l) and "clockLocal" not in l]
assert not bad, bad
assert "(UTC)" not in html.replace('<span class="tzl">UTC</span>', "X"), "fester UTC-Tabellenkopf"
assert html.count('class="tzl"') >= 8 and 'id="uiTime"' in html
PY
(cd bin && TZ=Europe/Berlin python3 - <<'PY') && ok "Pi: Hinweise und Meldungen in der eingestellten Zeit, Zeitpläne je eigener Zeitzone, Dauerwache" || bad "Zeitangaben auf dem Pi"
import json, time, calendar
from pathlib import Path
import timefmt as T, scan_plan as SP
t = calendar.timegm((2026, 11, 14, 18, 5, 0, 0, 0, 0))
assert T.fmt(t, m="utc") == "14.11. 18:05 UTC" and T.fmt(t, m="local", settings={}) == "14.11. 19:05 MEZ", (T.fmt(t, m="local", settings={}))
assert T.fmt(calendar.timegm((2026, 7, 1, 12, 0, 0, 0, 0, 0)), m="local", settings={"ui_lang": "en"}).endswith("CEST")
cfg = {"ranges": [{"name": "70cm", "device": "rtlsdr", "start_hz": 430e6}]}
base = {"active_device": "rtlsdr"}
loc = {**base, "schedules": [{"name": "Babbelrunde", "days": [5], "from": "19:00", "to": "19:30", "bands": ["70cm"]}]}
utc = {**base, "schedules": [{"name": "UTC-Runde", "days": [5], "from": "18:00", "to": "18:30", "bands": ["70cm"], "tz": "utc"}]}
assert SP.active_schedule(loc, cfg, now=t + 600)["name"] == "Babbelrunde"           # 19:15 MEZ
assert SP.active_schedule(utc, cfg, now=t + 600)["name"] == "UTC-Runde"             # 18:15 UTC
assert SP.active_schedule({**utc, "schedules": [dict(utc["schedules"][0], **{"from": "19:00", "to": "19:30"})]}, cfg, now=t + 600) is None
s = Path("../config/web_settings.json"); d = json.loads(s.read_text())
d["time_mode"] = "local"; s.write_text(json.dumps(d)); assert T.mode() == "local" and T.hm(t).endswith("MEZ")
d["time_mode"] = "utc"; s.write_text(json.dumps(d)); assert T.mode() == "utc" and T.hm(t) == "18:05 UTC"
src = Path("webapp.py").read_text()
assert 'datetime(s.time, \'localtime\')' in src and '"time_mode": __import__("timefmt").mode(data)' in src
for f in ("health.py", "netheal.py", "notify.py"):
    assert "time.localtime(" not in "".join(l for l in Path(f).read_text().splitlines() if "strftime" in l and "%H:%M" in l), f
PY

echo "== 60 SSTV-Bild von Hand auf die Webseite (Knopf in der Trefferkarte)"
(cd bin && python3 - "$SRC" <<'PY') && ok "Nachreichen: auch bei ausgeschaltetem Hochladen und unter der Mindest-Vollständigkeit, gleiche Kennung, ohne Pi-Werte von jetzt, Zustand in der Trefferliste" || bad "Nachreichen"
import base64, json, sqlite3, tempfile, threading, time, http.server
from pathlib import Path
import publish as P, satellites as SAT
d = Path(tempfile.mkdtemp()); P.QUEUE = d / "q.json"; P.LOG = d / "l.json"
got = []
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        meta = json.loads(body.split(b'name="meta"\r\n\r\n')[1].split(b"\r\n--")[0])
        got.append((meta, b'name="manual"' in body))
        b = json.dumps({"ok": True, "id": len(got), "status": "publish", "link": f"http://x/#sstv-{len(got)}", "edit": "http://x/e"}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(b)
srv = http.server.HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
c = {"enabled": False, "url": "", "user": "tom", "app_password": "pw", "status": "publish", "min_complete": 60}
P.cfg = lambda settings=None: dict(c)
rep = Path("../reports"); rep.mkdir(exist_ok=True)
png = rep / "20261114_171500_Satelliten_iss_0_sstv.png"; P.test_image(png)
row = {"band": "Satelliten", "segment_id": "iss", "rf_hz": 145800000, "snr_db": 18.2, "note": "SSTV PD120 (ISS), 45 % der Zeilen",
       "antenna": "X-50", "time": "2026-11-14T17:30:00", "sideband": None, "recording_path": "x/20261114_171500_Satelliten_iss_0.wav"}
try:
    P.manual(row, png); raise AssertionError("ohne Webseite vorgemerkt")
except ValueError as e:
    assert "Einstellungen" in str(e)
c["url"] = f"http://127.0.0.1:{srv.server_port}"
assert P.enqueue(png, {"image": {"complete": 1}}, key="auto-1") is None          # automatisch aus: nichts vorgemerkt
P.QUEUE.write_text(json.dumps([{"key": "auto-0", "uid": "a0", "added": time.time(), "tries": 0, "next": 0, "ready": 0, "png": str(png), "meta": {}}]))
uid = P.manual(row, png)
assert P.states()[P.key_for(png)]["state"] == "wartet"
assert P.tick() == 1 and [e["key"] for e in P.queue()] == ["auto-0"]            # automatischer bleibt liegen
meta, man = got[-1]
assert man and meta["summary"]["manual"] is True and meta["summary"]["source"] == "ISS" and meta["summary"]["mode"] == "PD120"
assert meta["summary"]["complete"] == 0.45 and meta["summary"]["time_utc"] == "2026-11-14T17:15:00Z"
titles = [s["title"] for s in meta["sections"]]
assert not any(t.startswith("Raspberry Pi") for t in titles) and "Empfang" in titles, titles
st = P.states()[P.key_for(png)]; assert st["state"] == "veröffentlicht" and st["link"].endswith("#sstv-1")
assert P.key_for(png) == f"{__import__('socket').gethostname()}-{png.name}"    # wie automatisch: Seite legt es nur einmal an
# Kurzwelle
png2 = rep / "20261114_160000_20m_14230000Hz_sstv0_sstv.png"; P.test_image(png2)
P.manual({"band": "20m", "segment_id": "s", "rf_hz": 14231900, "snr_db": 9.1, "note": "SSTV Martin 1", "antenna": "Dipol",
          "time": "2026-11-14T16:03:00", "sideband": "USB", "recording_path": "y/20261114_160000_20m_14230000Hz_sstv0.wav"}, png2)
P.tick(); m2 = got[-1][0]
assert m2["summary"]["source"] == "Kurzwelle 20m" and m2["summary"]["kind"] == "hf" and m2["summary"]["mode"] == "Martin 1"
# Satellit mit Bahndaten: Überflug wie beim automatischen Hochladen
tle = ["ISS (ZARYA)", "1 25544U 98067A   20316.41516162  .00001589  00000+0  36499-4 0  9995",
       "2 25544  51.6454 339.9628 0001882  94.8340 265.2864 15.49409479254842"]
tr = SAT.Tracker(tle, 49.3, 10.6)
rows, geo = P.sat_rows(tr, next(s for s in SAT.SATELLITES if s["id"] == "iss"), 145.8e6, 1605128580)
lab = [r[0] for r in rows]
assert {"Aufgang", "Höchster Punkt", "Untergang", "Doppler beim Bild", "Alter der Bahndaten"} <= set(lab) and isinstance(geo["max_el"], float)
assert rows[0] == ["Satellit", "ISS"], rows[0]
srv.shutdown()
# Trefferliste trägt den Zustand
import webapp as W
W.ensure_schema(); db = W.db_connect()
sid = db.execute("INSERT INTO scan (time, band, center_hz, noise_db, rms_i, rms_q) VALUES (?,?,?,?,?,?)",
                 ("2026-11-14T17:30:00+00:00", "Satelliten", 145800000, 0, 0, 0)).lastrowid
db.execute("INSERT INTO peak (scan_id, rf_hz, offset_hz, level_db, snr_db, recording_path, mode, segment_id) VALUES (?,?,?,?,?,?,?,?)",
           (sid, 145800000, 0, 18, 18, "x/20261114_171500_Satelliten_iss_0.wav", "sstv", "iss"))
db.commit(); db.close()
h = [x for x in W.list_hits({"mode": "sstv", "limit": 50}) if x["files"].get("sstv", "").endswith("iss_0_sstv.png")]
assert h and h[0]["pub"]["state"] == "veröffentlicht", h[:1]
import sys
src = Path("webapp.py").read_text(); js = (Path(sys.argv[1]) / "web" / "app.js").read_text()
assert '"/api/publish/hit"' in src and "sstv_manual_queued" in src and "data-act=\"pub\"" in js and "pubCell(h.pub)" in js
PY
grep -q "'manual_status'" "$SRC/share/wordpress/hesperosdx-sstv-galerie/includes/galerie-plugin.php" \
  && grep -q "min(\$t, time())" "$SRC/share/wordpress/hesperosdx-sstv-galerie/includes/galerie-plugin.php" \
  && grep -q '.hsp-galerie \[hidden\]' "$SRC/share/wordpress/hesperosdx-sstv-galerie/assets/galerie.css" \
  && grep -q "IntersectionObserver" "$SRC/share/wordpress/hesperosdx-sstv-galerie/assets/galerie.js" \
  && ok "Plugin 1.3: Anordnung einstellbar, Nachladeknopf nur bei weiteren Bildern, nie geplante Beiträge" || bad "Plugin 1.3"

echo "== 61 Update-Prüfung, Neustart-Meldung, Satellit in der Trefferkarte"
(cd bin && python3 - "$SRC" <<'PY') && ok "Update-Prüfung: Ausweg über git, letzter Stand bleibt, stündlich neu, Uhr vor, Hinweis nach 3 Tagen; Neustart: geplant/sauber/Strom weg ohne „Was tun“; Satellit beim Aufklappen" || bad "Update/Neustart/Satellit"
import json, sys, tempfile, time
from pathlib import Path
import housekeeping as HK, health as H, notices as NT
d = Path(tempfile.mkdtemp()); HK.UPDATE_CACHE = d / "u.json"
calls = []
class R:
    def __init__(self, v): self.v = v
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self, n): return self.v
def ok_url(req, timeout=0): calls.append("raw"); return R(b"9.9.9\n")
def bad_url(req, timeout=0): calls.append("raw"); raise OSError("blockiert")
HK.urllib.request.urlopen = ok_url
assert HK.update_check(force=True)["latest"] == "9.9.9" and HK.update_check()["via"] == "raw"
HK.urllib.request.urlopen = bad_url
HK._latest_via_git = lambda: "9.9.10"                         # Rohdatei gesperrt, git geht
c = HK.update_check(force=True); assert c["latest"] == "9.9.10" and c["via"] == "git" and "error" not in c
def nogit(): raise RuntimeError("Could not resolve host")
HK._latest_via_git = nogit
c = HK.update_check(force=True)
assert c["latest"] == "9.9.10" and "blockiert" in c["error"] and "resolve" in c["error"] and c["fail_since"] and c["ok_at"]
n = len(calls); HK.update_check(max_age=86400); assert len(calls) == n          # innerhalb der Stunde: nicht neu
c["checked"] = time.time() - 3700; HK.UPDATE_CACHE.write_text(json.dumps(c))
HK.update_check(max_age=86400); assert len(calls) == n + 1                     # nach einer Stunde Fehler: neu
c = json.loads(HK.UPDATE_CACHE.read_text()); c["checked"] = time.time() + 5 * 86400; c.pop("error"); HK.UPDATE_CACHE.write_text(json.dumps(c))
HK.update_check(max_age=86400); assert len(calls) == n + 2                     # Prüfzeit in der Zukunft zählt nicht
# Hinweis nach 3 Tagen ohne Erfolg
NT.STATE = d / "n.json" if hasattr(NT, "STATE") else None
raised = {}
H.NT = type("N", (), {"raise_": staticmethod(lambda nid, *a, **k: raised.__setitem__(nid, (a, k))),
                      "clear": staticmethod(lambda nid: raised.pop(nid, None))})
H.update_days = lambda settings=None: 1
c = json.loads(HK.UPDATE_CACHE.read_text()); c.update(error="URLError: x", fail_since=time.time() - 4 * 86400, checked=time.time(), latest="9.9.10")
HK.UPDATE_CACHE.write_text(json.dumps(c))
H._upd["th"] = type("T", (), {"is_alive": lambda self: True})()                 # keine echte Abfrage im Test
if not Path("../VERSION").exists():
    Path("../VERSION").write_text("1.0.0\n")
H.check_update({})
assert "update_check_failed" in raised and "update_available" in raised and "bin/update.sh" in raised["update_check_failed"][1]["cmd"]
c["fail_since"] = time.time() - 3600; HK.UPDATE_CACHE.write_text(json.dumps(c)); H.check_update({})
assert "update_check_failed" not in raised
# Neustart: Ursache
dd = Path(tempfile.mkdtemp()); now = time.time()
assert H.reboot_cause("b1", now - 60, dd) == (None, None)
(dd / "planned_reboot.json").write_text(json.dumps({"t": now - 70, "via": "Dashboard"}))
assert H.reboot_cause("b1", now - 60, dd) == ("planned", "Dashboard") and not (dd / "planned_reboot.json").exists()
(dd / "clean_stop.json").write_text(json.dumps({"t": now - 50, "boot": "b1"}))
assert H.reboot_cause("b1", now - 60, dd) == ("clean", None)
assert H.reboot_cause("b1", now + 600, dd) == ("unclean", None)               # später noch gelaufen: Strom weg
assert H.reboot_cause("b0", now - 60, dd) == ("unclean", None)
for cause in ((None, None), ("planned", "Dashboard"), ("clean", None), ("unclean", None)):    # Meldung je Ursache
    ev = []
    H.reboot_cause = lambda p, l, d=None, c=cause: c
    H.NT = type("N", (), {"event": staticmethod(lambda *a, **k: ev.append((a, k)))})
    H.check_reboot({"boot_id": "alt", "alive": time.time() - 300, "boot_time": time.time() - 7200})
    assert ev and "fix" not in ev[0][1] and ("geplant" in ev[0][0][3]) == (cause[0] == "planned"), (cause, ev)
    assert ("Protokoll" in ev[0][0][4]) == (cause[0] != "planned"), ev
src = Path("health.py").read_text()
i = src.index("def check_reboot"); assert "fix=" not in src[i:src.index("# ----", i)], "Neustart-Meldung ohne „Was tun“"
assert "planned_reboot.json" in Path("services.py").read_text() and "clean_stop.json" in Path("scheduler.py").read_text()
assert NT.english("Der Pi wurde neu gestartet (vor 2 min), zuletzt erreichbar 14.11. 17:02 UTC. Er wurde nicht heruntergefahren – Strom weg oder abgestürzt. Mögliche Ursachen (Stromschwankungen, Kartenfehler) stehen im Protokoll.").endswith("are in the log.")
# Satellit in der Trefferkarte
import webapp as W, satellites as SAT
W.ensure_schema(); db = W.db_connect()
sid = db.execute("INSERT INTO scan (time, band, center_hz, noise_db, rms_i, rms_q) VALUES (?,?,?,?,?,?)",
                 ("2020-11-11T12:00:00+00:00", "Satelliten", 145800000, 0, 0, 0)).lastrowid
pid = db.execute("INSERT INTO peak (scan_id, rf_hz, offset_hz, level_db, snr_db, recording_path, mode, segment_id) VALUES (?,?,?,?,?,?,?,?)",
                 (sid, 145800000, 0, 10, 10, "x/20201111_120000_Satelliten_iss_fm.wav", "voice", "iss")).lastrowid
db.commit(); db.close()
tle = {"25544": ["ISS (ZARYA)", "1 25544U 98067A   20316.41516162  .00001589  00000+0  36499-4 0  9995",
                 "2 25544  51.6454 339.9628 0001882  94.8340 265.2864 15.49409479254842"]}
SAT.load_tles = lambda fetch=True, force=False: ({int(k): v for k, v in tle.items()}, {})
W.load_settings = lambda: {"my_locator": "JN59UU"}
r = W.hit_sat_info(pid)
t = [s["title"] for s in r["sections"]]; rows = {k: v for s in r["sections"] for k, v in s["rows"]}
assert t == ["Satellit", "Überflug"] and rows["Satellit"] == "ISS" and "Doppler beim Treffer" in rows and "Beim Treffer: Elevation" in rows, r
assert W.hit_sat_info(999999) == {"sections": []}
js = (Path(sys.argv[1]) / "web" / "app.js").read_text()
assert "hits-day" in js and "/api/hit/sat" in js and "data-kind" in (Path(sys.argv[1]) / "web" / "style.css").read_text().replace('[data-kind', 'data-kind')
i = js.index("async function loadUpdate"); j = js.index("\n  }\n", i)
assert js[i:j].count("return d;") == 1 and js[i:j].rstrip().endswith("return d;"), "Update-Karte: Protokoll und Fortschritt nach dem return"
PY

echo "== 62 RAM-Ordner verschwindet (Abmelden), Diagnose doppelt abgerufen, Pi startet gerade neu"
(cd bin && python3 - "$SRC" <<'PY') && ok "RAM-Ordner wird neu angelegt (Zeitplaner läuft weiter), RemoveIPC aus, Diagnose einmal gebaut und abbruchfest, Einstellungen laden nach" || bad "RAM-Ordner/Diagnose"
import io, shutil, sys, threading, time
from pathlib import Path
import rundir as R, diag
p = R.path("scheduler.heartbeat"); R.touch(p)
shutil.rmtree(R.directory()); R.touch(p); assert p.exists()                       # logind hat /dev/shm geleert
shutil.rmtree(R.directory()); R.ensure(); assert R.directory().is_dir()
src = Path("scheduler.py").read_text(); i = src.index('_RD.touch(_RD.path("scheduler.heartbeat"))')
assert "try:" in src[i - 40:i] and "except OSError" in src[i:i + 200]
assert "RemoveIPC=no" in Path("setup_system.sh").read_text()
assert "_RD.ensure()" in Path("scanner.py").read_text() and "_RD.ensure()" in Path("sat_pass.py").read_text()
n = {"build": 0}
real = diag.write
def fake(f, stream=True):
    n["build"] += 1; time.sleep(0.3); f.write(b"x" * 200000)
diag.write = fake
class Hd:
    def __init__(self, broken=False):
        self.h, self.wfile, self.broken = {}, io.BytesIO(), broken
        if broken:
            def w(b): raise BrokenPipeError()
            self.wfile.write = w
    def send_response(self, c): self.code = c
    def send_header(self, k, v): self.h[k] = v
    def end_headers(self): pass
hs = [Hd(broken=True), Hd(), Hd()]
th = [threading.Thread(target=lambda h=h: h.__setattr__("ok", diag.send(h))) for h in hs]
[t.start() for t in th]; [t.join() for t in th]
assert n["build"] == 1, n                                                    # gleichzeitig: nur einmal gebaut
assert hs[0].ok is False and hs[1].ok and hs[2].ok and hs[1].h["Content-Length"] == "200000"
assert hs[1].wfile.getvalue() == hs[2].wfile.getvalue()
diag.write = real
js = (Path(sys.argv[1]) / "web" / "app.js").read_text()
assert "e instanceof TypeError" in js and "Pi gerade nicht erreichbar" in js and "svcDiag" in js
PY

echo "== 63 Rohdaten laufender Überflüge, wer schreibt auf die Karte"
(cd bin && python3 - <<'PY') && ok "Rohdatei eines laufenden Überflugs ist kein „abgebrochen“ (Warnung und Rettung), Schreiblast je Programm im Verlauf" || bad "Rohdaten/Schreiblast"
import os, subprocess, sys, tempfile, time
from pathlib import Path
import housekeeping as HK, sysmon as SM
d = Path(tempfile.mkdtemp())
live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "sat_pass.py"])
old = d / f".sat_iss_{live.pid}.r16"; old.write_bytes(b"x" * 100)                 # läuft gerade
dead = d / ".sat_iss_999999.r16"; dead.write_bytes(b"x" * 100)                   # Überflug abgebrochen
fresh = d / ".sat_ao7_999998.r16"; fresh.write_bytes(b"x")                       # wird noch geschrieben
for p in (old, dead): os.utime(p, (time.time() - 600, time.time() - 600))
assert HK.sat_raw_orphans(d) == [dead], HK.sat_raw_orphans(d)
live.kill()
assert "HK.sat_raw_orphans(SP.RECDIR)" in Path("sat_recover.py").read_text()
SM.DB = d / "sysmon.db"
seq = iter([{"1": ("scanner.py", 0), "2": ("webapp.py", 0)},
            {"1": ("scanner.py", 30_000_000), "2": ("webapp.py", 2_000_000)},
            {"1": ("scanner.py", 45_000_000), "2": ("webapp.py", 2_000_000), "3": ("rtl_sdr", 10)}])
SM.own_writes = lambda: next(seq)
sm = SM.Sampler(); t = time.time()
sm.tick(t); sm.tick(t + 10); row = sm.tick(t + 70)
assert row[-1] == "scanner.py 45.0, webapp.py 2.0", row
st = SM.storage_info(now=t + 80)
assert st["writers_24h"][0] == ["scanner.py", 45] and st["writers_24h"][1] == ["webapp.py", 2], st
assert SM._short(b"/usr/bin/python3\0/home/g/hamscanpi/bin/scanner.py\0--x") == "scanner.py" and SM._short(b"rtl_sdr\0-f\0x") == "rtl_sdr"
PY

echo "== 64 PMSDR im Diagnosepaket, Galerie in der Breite des Themes"
(cd bin && python3 - "$SRC" <<'PY') && ok "Diagnose: PMSDR-Empfangstest mit Pegel, I/Q und stärksten Signalen, nur wenn frei; Galerie begrenzt ihre Breite" || bad "PMSDR-Diagnose/Galerie-Breite"
import fcntl, json, sys
from pathlib import Path
import numpy as np, diag, devices as DEV
Path("../config/scanner.json").write_text(json.dumps({**json.loads(Path("../config/scanner.json").read_text()), "alsa_name": "Aureon", "iq_conjugate": True}))
class F:
    rate = 48000; fifo_fd = -1
    def __init__(self, cfg): pass
    @staticmethod
    def _find_alsa(n, d): return 2, 0
    def tune(self, c): self.c = c
    def capture(self, s):
        t = np.arange(int(48000 * s)) / 48000
        z = 0.01 * np.exp(2j * np.pi * 11500 * t) + 0.001 * (np.random.randn(len(t)) + 1j * np.random.randn(len(t)))
        return z.real, -z.imag
DEV.PmsdrDevice = F
diag._run = lambda cmd, timeout=20: f"[{cmd[0]}]"
r = diag.pmsdr_info()
assert "7.074 MHz: Pegel" in r and "7.0755 MHz +" in r and "ÜBERSTEUERT" not in r, r
lk = open("../data/scan.lock", "a"); fcntl.flock(lk, fcntl.LOCK_EX)          # Scan läuft
assert "übersprungen – Scan" in diag.pmsdr_info(); fcntl.flock(lk, fcntl.LOCK_UN)
css = (Path(sys.argv[1]) / "share/wordpress/hesperosdx-sstv-galerie/assets/galerie.css").read_text()
assert "var(--hsp-max, var(--max" in css and "margin: 1rem auto 2rem" in css
assert "'max_w'" in (Path(sys.argv[1]) / "share/wordpress/hesperosdx-sstv-galerie/includes/galerie-plugin.php").read_text()
PY

echo "== 65 PMSDR-Eingang, Gerätewechsel beim Abstimmen, unterbrochener Überflug, Karten und schnelle Klicks"
(cd bin && python3 - "$SRC" <<'PY') && ok "Soundkarten-Eingang auf Line, keine UHF an die PMSDR, Neustart = unterbrochen (kein Fehler), PMSDR unter Dienste, Klicks werden nicht zurückgesetzt" || bad "PMSDR/Live/Überflug/Dashboard"
import json, os, subprocess, sys, time
from pathlib import Path
import devices as DEV
calls = []
def fake_run(cmd, **k):
    calls.append(cmd)
    out = "numid=16,iface=MIXER,name='PCM Capture Source'\n  ; Item #0 'Mic'\n  ; Item #1 'Line'\n  : values=0\n" if "cget" in cmd else ""
    return subprocess.CompletedProcess(cmd, 0, out, "")
real = DEV.subprocess.run; DEV.subprocess.run = fake_run
assert DEV.capture_input(7) == "Mic" and any(c[-1] == "Line" and "cset" in c for c in calls)
n = len(calls); assert DEV.capture_input(7) is None and len(calls) == n          # einmal je Karte
assert DEV.capture_input(8, "") is None
DEV.subprocess.run = real
pm = DEV.PmsdrDevice.__new__(DEV.PmsdrDevice); pm.fifo_fd = -1; pm.cfg = {"fifo": "/x"}
try:
    pm.tune(437_560_000); raise AssertionError("UHF an die PMSDR")
except ValueError as e:
    assert "außerhalb der PMSDR" in str(e)
src = Path("webapp.py").read_text()
assert "def live_device_for" in src and "want != _live.device" in src
import services as SV
assert SV.UNITS.get("pmsdr-control") and "restart pmsdr-control" in Path("setup_system.sh").read_text()
# Überflug durch Neustart unterbrochen: sofort „abgebrochen“, Warnung statt Fehler
import satellites as SAT, housekeeping as HK
logs = []
SAT.write_result("t_1", state="running", channels=["iss"], started=time.time() - 300, pid=999999, boot="alter-start")
HK.run({}, lambda d: (0, 0), lambda *a, **k: logs.append((a, k)))
r = SAT.read_results()["t_1"]
assert r["state"] == "cancelled" and "neu gestartet" in r["error"], r
assert any(a[1] == "pass_interrupted" for a, k in logs) and not any(a[1] == "pass_aborted" for a, k in logs), logs
assert "pid=os.getpid(), boot=_boot" in Path("sat_pass.py").read_text()
js = (Path(sys.argv[1]) / "web" / "app.js").read_text(); lj = (Path(sys.argv[1]) / "web" / "layout.js").read_text()
assert "saveChain" in js and "if (!pending.length && !saveTimer)" in js and "Mindestens einer bleibt gewählt" in js
assert "if (u === 'pmsdr-control') continue" in js
assert "createComment('slot')" in lj and "c.appendChild(p); moved.push(p)" not in lj
PY

echo "== 66 Lesbare Fehlermeldungen, Band bei Live-Dekodierungen, SSTV-Melder je Pi"
(cd bin && python3 - "$SRC" <<'PY') && ok "Fehler als Klartext mit „Was tun“ nur wenn nötig, Live-Dekodierung mit Band, Plugin: Rolle SSTV-Melder und Station je Bild" || bad "Fehlertexte/Live-Band/Melder"
import sys
from pathlib import Path
import errtext as ET, notify as N, live_decode as LD
t, b = ET.text("scheduler", "loop_exception", {"error": "FileNotFoundError: x", "where": ["lang"]})
assert t == "Zeitplaner: eine Runde fehlgeschlagen" and "Was tun:" in b and "lang" not in b and "(scheduler / loop_exception)" in b
t, b = ET.text("scanner", "device_open_failed", {"device": "pmsdr", "error": "Soundkarte fehlt"})
assert "pmsdr" in t and "Was tun" not in b
t, b = ET.text("webapp", "neu_unbekannt", {"x": 1, "output_tail": "z" * 50})
assert t == "Dashboard: Fehler" and "x: 1" in b and "zzz" not in b
t, b = ET.text("scheduler", "loop_exception", {"error": "e"}, en=True); assert t.startswith("Scheduler")
sent = []
N.cfg = lambda: {"enabled": True, "on_error": True, "throttle_min": 0}
N._throttled = lambda k, m: False
N._is_en = lambda: False
N.send_all = lambda title, body, c, **k: sent.append((title, body))
N.notify_error("housekeeping", "pass_aborted", {"stage": "Aufnahme", "hint": "Rohdaten …"})
assert sent and sent[0][0] == "⚠ Überflug nicht beendet" and "{" not in sent[0][1], sent
LD.band_of = lambda f: "40m" if 7e6 <= f <= 7.2e6 else "Live"
cls = next(v for v in vars(LD).values() if isinstance(v, type) and hasattr(v, "_add") and hasattr(v, "status"))
o = cls.__new__(cls); import threading, collections
o.lock, o.seq, o.events, o.freq_hz = threading.Lock(), 0, collections.deque(maxlen=100), 7_074_000
o._add("FT8", "CQ DL1ABC JO31")
o.freq_hz = 145_500_000; o._add("APRS", "x")
ev = list(o.events)
assert ev[1]["band"] == "40m" and ev[0]["band"] is None, ev
js = (Path(sys.argv[1]) / "web" / "app.js").read_text()
assert "e.band || fmtMHz(e.freq_hz)" in js
php = (Path(sys.argv[1]) / "share/wordpress/hesperosdx-sstv-galerie/hesperosdx-sstv-galerie.php").read_text() + (Path(sys.argv[1]) / "share/wordpress/hesperosdx-sstv-galerie/includes/galerie-plugin.php").read_text()
for k in ("add_role('hsp_sstv_melder'", "'_hsp_station'", "'hsp_from'", "hsp_sstv_pi_user", "create_new_application_password",
          "'permission_callback' => 'hsp_sstv_can_send'", "admin_url('post.php?post='"):
    assert k in php, k
PY

echo "== 67 Neuer Repository-Name hesperos-dx: Veröffentlichen und Pi stellen um"
ren67() {
    local G; G="$(mktemp -d)"
    export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@x GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@x
    git init -q --bare -b main "$G/hesperos-dx.git" || return 1
    mkdir -p "$G/src" "$G/pub" && cp -r "$SRC/." "$G/src/" && rm -rf "$G/src/.git" "$G/src/data" "$G/src/log" || return 1
    echo 9.9.0 > "$G/src/VERSION"                                 # erster Stand auf „GitHub“
    (cd "$G/src" && OUT="$G/pub/hesperos-publish.sh" bash devtools/build_publish.sh "Test 9.9.0" >/dev/null) || return 1
    (cd "$G/pub" && HSP_WORK="$G/seed" HSP_REPO="$G/hesperos-dx.git" bash hesperos-publish.sh >/dev/null 2>&1) || return 1
    echo 9.9.1 > "$G/src/VERSION"
    (cd "$G/src" && OUT="$G/pub/hesperos-publish.sh" bash devtools/build_publish.sh "Test 9.9.1" >/dev/null) || return 1
    # alte Arbeitskopie „hamscanpi“ neben der Datei, Adresse mit dem alten Namen (zeigt hier – wie die
    # GitHub-Umleitung – auf dasselbe Repository)
    ln -s "$G/hesperos-dx.git" "$G/hamscanpi.git"
    git clone -q "$G/hamscanpi.git" "$G/pub/hamscanpi" 2>/dev/null || return 1
    local O; O="$(cd "$G/pub" && HSP_REPO="$G/hesperos-dx.git" bash hesperos-publish.sh 2>&1)" || { echo "$O" | tail -25; git -C "$G/hesperos-dx.git" log --oneline --all | head; return 1; }
    [ ! -e "$G/pub/hamscanpi" ] && [ -d "$G/pub/hesperos-dx/.git" ] || return 1
    [ "$(git -C "$G/pub/hesperos-dx" remote get-url origin)" = "$G/hesperos-dx.git" ] || return 1
    git -C "$G/hesperos-dx.git" tag | grep -q v9.9.1 || return 1
    # eine Pi-Installation mit Daten neben der Datei wird nie verschoben
    rm -rf "$G/pub/hesperos-dx" && git clone -q "$G/hamscanpi.git" "$G/pub/hamscanpi" 2>/dev/null && mkdir "$G/pub/hamscanpi/data" || return 1
    echo 9.9.2 > "$G/src/VERSION"
    (cd "$G/src" && OUT="$G/pub/hesperos-publish.sh" bash devtools/build_publish.sh "Test 9.9.2" >/dev/null) || return 1
    (cd "$G/pub" && HSP_REPO="$G/hesperos-dx.git" bash hesperos-publish.sh >/dev/null 2>&1) || return 1
    [ -d "$G/pub/hamscanpi/data" ] && [ -d "$G/pub/hesperos-dx/.git" ] || return 1
    # Pi: Umstellung der Adresse in setup_system.sh (dieselbe sed-Regel wie dort)
    local R; R="$(grep -o "s#^((https://|ssh://git@|git@)github.*#p" "$SRC/bin/setup_system.sh" | head -1)"
    R="${R%\"*}"; R="${R%\'}"
    [ -n "$R" ] || return 1
    m() { printf '%s' "$1" | sed -nE "$R"; }
    [ "$(m https://github.com/do1gmt/hamscanpi)" = https://github.com/do1gmt/hesperos-dx ] || return 1
    [ "$(m https://github.com/do1gmt/hamscanpi.git)" = https://github.com/do1gmt/hesperos-dx.git ] || return 1
    [ "$(m git@github.com:do1gmt/hamscanpi.git)" = git@github.com:do1gmt/hesperos-dx.git ] || return 1
    [ -z "$(m https://tok@github.com/do1gmt/hamscanpi.git)" ] && [ -z "$(m https://github.com/do1gmt/hesperos-dx.git)" ] \
      && [ -z "$(m https://github.com/x/hamscanpi-fork)" ] || return 1
    grep -q 'ls-remote -q --exit-code "$NU" HEAD' "$SRC/bin/setup_system.sh" || return 1
    grep -q 'do1gmt/hesperos-dx/main/VERSION' "$SRC/bin/housekeeping.py" || return 1
    ! grep -rq "do1gmt/hamscanpi" "$SRC/bin" "$SRC/web" "$SRC/README.md" "$SRC/devtools/publish_head.sh" || return 1
    rm -rf "$G"
}
ren67 && ok "alte Arbeitskopie übernommen und umgestellt, Pi-Installation bleibt, Pi stellt nur echte hamscanpi-Adressen um" || bad "Umstellung hesperos-dx"

echo "== 68 Testpaket ohne eigenes Rufzeichen, Karten zwischen Spalten, Live-Hinweis, Quittieren, SSTV-Export"
(cd bin && timeout 30 python3 - "$SRC" <<'PY') && ok "Testpaket Freitext, Spaltenwechsel bleibt, Live-Hinweis überall, „Alle quittieren“ nur bei Offenem, Karte SSTV-Export" || bad "1.8.09-Oberfläche"
import socket, sys
from pathlib import Path
sys.path.insert(0, "../devtools")
import wsjtx_read as RD, wsjtx_udp as WX
W = Path(sys.argv[1]) / "web"
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); rx.bind(("127.0.0.1", 0)); rx.settimeout(3)
WX.cfg = lambda: {"enabled": True, "targets": "", "ident": "X", "my_call": "DO1GMT", "my_locator": "JN59HH"}
r = WX.test(f"127.0.0.1:{rx.getsockname()[1]}", "X")
assert r and r[0]["ok"], r
got = [RD.read(rx.recv(4096)) for _ in range(3)]
dec = [g for g in got if g["typ"] == 2]
assert dec and dec[0]["msg"] == "HESPEROS TEST" and "DO1GMT" not in dec[0]["msg"] and len(dec[0]["msg"]) <= 13, dec
html, js, lj = (W / "index.html").read_text(), (W / "app.js").read_text(), (W / "layout.js").read_text()
assert '<h2>SSTV-Export <small' in html and 'id="wxBox" open' in html and "Ins Netzwerk senden" in html
assert 'id="liveBanner"' in html and "function liveBanner(s, busy)" in js and "liveBanner(s, busy);" in js
assert "$('noticeAckAll').disabled = !ackable" in js
assert "<th>Bedeutung</th>" not in js and 'id="liveLevelVal"' in html
pl = Path(sys.argv[1]) / "share/wordpress/hesperosdx-sstv-galerie"
php, tpl = (pl / "hesperosdx-sstv-galerie.php").read_text() + (pl / "includes/galerie-plugin.php").read_text(), (pl / "templates/galerie.php").read_text()
assert "Plugin URI:        https://github.com/do1gmt/hesperos-dx" in php and "HSP_SSTV_HOME" in php and "esc_url(HSP_SSTV_HOME)" in php
assert "HSP_SSTV_HOME" in tpl and 'rel="noopener"' in tpl
# Spaltenwechsel: aus der anderen Spalte gezogene Karten bekommen beim Wiederherstellen einen Platz
assert "for (var i = marks.length; i < want.length; i++)" in lj and "tgt.parentElement !== p.parentElement" in lj
PY
if command -v node >/dev/null && node -e "require('jsdom')" 2>/dev/null; then
    node - "$SRC/web/layout.js" <<'JS' && ok "Anordnung: Karte in die andere Spalte bleibt nach dem Neuladen dort (jsdom)" || bad "Anordnung Spaltenwechsel"
const { JSDOM } = require('jsdom'); const js = require('fs').readFileSync(process.argv[2], 'utf8');
const html = '<section class="view" data-view="scan"><div class="two-col"><div class="col"><section class="panel" id="A"><h2>A</h2></section><section class="panel" id="B"><h2>B</h2></section></div><div class="col"><section class="panel" id="C"><h2>C</h2></section><section class="panel" id="X"><h2>X</h2></section></div></div></section>';
function run(order) {
  const d = new JSDOM('<body>' + html + '</body>', { runScripts: 'outside-only', url: 'http://x/' });
  d.window.localStorage.setItem('layout.v1', JSON.stringify({ order, folded: [] }));
  d.window.eval(js); d.window.document.dispatchEvent(new d.window.Event('DOMContentLoaded'));
  return [...d.window.document.querySelectorAll('.col')].map(c => [...c.querySelectorAll('.panel')].map(p => p.id).join('')).join('|');
}
const a = run({ 'scan-col0': ['B'], 'scan-col1': ['C', 'X', 'A'] }), b = run({ 'scan-col0': ['A', 'B', 'C', 'X'], 'scan-col1': [] });
if (a !== 'B|CXA' || b !== 'ABCX|') { console.log(a, b); process.exit(1); }
JS
fi

echo "== 69 USB-Stick abgezogen und wieder angesteckt, Kernel-Fehler am Stick, Diagnose-Dateiname, Logo"
(cd bin && timeout 60 python3 - "$SRC" <<'PY') && ok "tote Einhängung erkannt und neu eingehängt, Sicherung versucht es danach noch einmal, Stick-Fehler nicht der Speicherkarte zugeschrieben, Diagnose mit Namen im Netz" || bad "USB-Stick/Diagnose"
import errno, sys, tempfile
from pathlib import Path
import storage as STO, backup as BK, health as HE, diag as DG, notices as NT
src = Path(sys.argv[1])
# 1) tote Einhängung: /dev/sda1 gibt es nicht mehr, der Stick heißt jetzt sdb1
tmp = Path(tempfile.mkdtemp())
byuuid = tmp / "uuid"; byuuid.write_text("")
STO._mount_source = lambda mp: "/dev/sda1-gibt-es-nicht"
real_path = STO.Path
class P(type(Path())):
    pass
orig = STO.Path
STO.Path = lambda *a: byuuid if a[:1] == ("/dev/disk/by-uuid",) else orig(*a)
assert STO.usb_stale("ABCD-1234")
STO._mount_source = lambda mp: str(byuuid)                     # gleiche Gerätedatei: in Ordnung
assert not STO.usb_stale("ABCD-1234")
STO.Path = orig
# 2) Sicherung: Ein-/Ausgabefehler → einmal neu einhängen, dann klappt es
calls = []
STO.mounted = lambda mp: True
state = {"n": 0}
def probe(p):
    state["n"] += 1
    return "Ein-/Ausgabefehler" if state["n"] == 1 else None
BK._probe = probe
STO.remount_usb = lambda uuid, log=None, why="": (calls.append(uuid) or (True, ""))
assert BK.check_dir(str(tmp / "z"), {"kind": "usb", "uuid": "ABCD-1234", "label": "HAMSCANPI"}) is None and calls == ["ABCD-1234"]
state["n"] = -5                                                # bleibt kaputt: verständliche Meldung
BK._probe = lambda p: "Ein-/Ausgabefehler"
e = BK.check_dir(str(tmp / "z"), {"kind": "usb", "uuid": "ABCD-1234", "label": "HAMSCANPI"})
assert e and "Abziehen und wieder anstecken" in e and "aktiven USB-Hub" in e, e
# echter Probelauf auf einem beschreibbaren Ordner
import importlib; importlib.reload(BK)
assert BK._probe(tmp / "ok") is None and not (tmp / "ok" / ".hesperos-probe").exists()
# 3) Kernel: FAT-Fehler an sdb → eigener Hinweis, nicht „Karte tauschen“
assert HE._disk_of("FAT-fs (sdb1): Directory bread(block 30464) failed") == "sdb"
assert HE._disk_of("mmcblk0: error -110 transferring data") == "mmcblk0"
assert any(rx.search("FAT-fs (sda1): Directory bread(block 30464) failed") for rx, *_ in HE.KERNEL_PATTERNS)
ev = []
NT.event = lambda key, level, area, title, detail="", **k: ev.append((key, title, k.get("fix", "")))
HE._root_disk = lambda: "mmcblk0"
HE._run = lambda cmd, timeout=15: (0, "1700000000.0 pi kernel: FAT-fs (sdb1): Directory bread(block 30464) failed\n"
                                        "1700000001.0 pi kernel: FAT-fs (sdb1): FAT read failed (blocknr 59)\n")
NT.clear = lambda *a, **k: None
HE.check_kernel({})
assert [e[0] for e in ev] == ["usb_disk_io"] and "Speicherkarte ist nicht betroffen" in ev[0][2], ev
# 4) Diagnose-Dateiname mit dem Namen im Netz
import socket
socket.gethostname = lambda: "do1gmt-devpi"
assert DG.filename().startswith("hesperos-diagnose-do1gmt-devpi-") and DG.filename().endswith(".tar.gz")
# 5) Logo: Stern mit Wellen, PNG-Größen und favicon vorhanden
svg = (src / "web/icon.svg").read_text()
assert "Abendstern" in svg and all((src / "web" / f).stat().st_size > 500 for f in ("icon-180.png", "icon-192.png", "icon-512.png", "favicon.ico"))
PY
bash -n "$SRC/share/root/hesperosdx-store" && grep -q "  remount-usb)" "$SRC/share/root/hesperosdx-store" && grep -q ';&' "$SRC/share/root/hesperosdx-store" \
  && ok "Helfer kennt remount-usb und hängt abgezogene Sticks lose aus" || bad "Helfer remount-usb"

echo "== 70 WLAN-Suche: sichtbare Liste, Fehler mit Grund"
(cd bin && timeout 30 python3 - "$SRC" <<'PY') && ok "Netze als Liste zum Antippen, offene Netze erkannt, fehlgeschlagene Suche mit Grund statt „keine Netze“" || bad "WLAN-Suche"
import sys
from pathlib import Path
import netheal as NH
NH.wifi_iface = lambda: "wlan0"
NH.helper = lambda *a, **k: (True, "cumulus\t72\tWPA2\nCafe Gast\t40\t--\nNachbar\t81\tWPA1 WPA2\n")
vis, err = NH.scan_detail()
assert err is None and [v["ssid"] for v in vis] == ["Nachbar", "cumulus", "Cafe Gast"] and vis[2]["security"] == "", vis
NH.helper = lambda *a, **k: (False, "Error: Wi-Fi scan not allowed\nsudo: …")
vis, err = NH.scan_detail()
assert vis == [] and err and "sudo" in err, err
NH.wifi_iface = lambda: None
assert NH.scan_detail() == ([], "kein WLAN-Adapter gefunden")
W = Path(sys.argv[1]) / "web"
html, js = (W / "index.html").read_text(), (W / "app.js").read_text()
assert 'id="wifiSeenList"' in html and "$('wifiSeenList').addEventListener('click'" in js and "Suche fehlgeschlagen: " in js
PY

echo "== 71 Station umbenannt: Sicherungsordner zieht mit"
(cd bin && timeout 30 python3 - <<'PY') && ok "Ordner umbenannt, mit bestehendem zusammengeführt ohne Überschreiben, bei fehlendem Stick nachgeholt, auch mehrfach und hin und zurück" || bad "Sicherungsordner bei Umbenennung"
import tempfile
from pathlib import Path
import storage as STO
tmp = Path(tempfile.mkdtemp())
STO.USB_MP, STO.NAS_MP, STO.STATE = tmp / "usb", tmp / "nas", tmp / "state.json"
base = STO.USB_MP / STO.SUB
plugged = {"on": True}
STO.mounted = lambda mp: mp == STO.USB_MP and plugged["on"]
STO.target = lambda settings=None: {"kind": "usb", "uuid": "ABCD-1234"}
logs = []
log = lambda *a, **k: logs.append((a, k))
def mk(host, *files):
    (base / host).mkdir(parents=True, exist_ok=True)
    for f in files:
        (base / host / f).write_text(f)
def names(host):
    return sorted(x.name for x in (base / host).iterdir()) if (base / host).is_dir() else None
# 1) einfach: alter Ordner wird zum neuen
mk("hamscanpidev", "hamscanpi-backup-20261114-0300-data.tar.gz")
STO.follow_rename("hamscanpidev", "do1gmt-dev", log)
assert names("hamscanpidev") is None and names("do1gmt-dev") == ["hamscanpi-backup-20261114-0300-data.tar.gz"]
assert any(a[2] == "backup_dir_renamed" for a, k in logs)
# 2) neuer Name gab es schon: zusammenführen, nichts überschreiben
mk("do1gmt-devpi", "hamscanpi-backup-20261117-1121-data.tar.gz")
(base / "do1gmt-dev" / "hamscanpi-backup-20261117-1121-data.tar.gz").write_text("ALT")
STO.follow_rename("do1gmt-dev", "do1gmt-devpi", log)
assert names("do1gmt-devpi") == ["hamscanpi-backup-20261114-0300-data.tar.gz", "hamscanpi-backup-20261117-1121-data.tar.gz"]
assert (base / "do1gmt-devpi" / "hamscanpi-backup-20261117-1121-data.tar.gz").read_text() != "ALT"
assert names("do1gmt-dev") == ["hamscanpi-backup-20261117-1121-data.tar.gz"]       # gleicher Name: bleibt liegen
# 3) Stick steckt nicht: vorgemerkt, mehrfach umbenannt, beim Anstecken in Reihenfolge nachgeholt
plugged["on"] = False
STO.follow_rename("do1gmt-devpi", "do1gmt-entwicklungpi", log)
STO.follow_rename("do1gmt-entwicklungpi", "do1gmt-pi", log)
assert len(STO.read_state()["pending_renames"]) == 2 and names("do1gmt-devpi")
plugged["on"] = True
STO.apply_renames(log)
assert names("do1gmt-pi") and names("do1gmt-devpi") is None and names("do1gmt-entwicklungpi") is None
assert STO.read_state()["pending_renames"] == []
# 4) hin und zurück und wieder hin
STO.follow_rename("do1gmt-pi", "a-pi", log); STO.follow_rename("a-pi", "do1gmt-pi", log); STO.follow_rename("do1gmt-pi", "a-pi", log)
assert names("a-pi") and names("do1gmt-pi") is None
# 5) ungültige Namen werden nie als Pfad benutzt
STO.follow_rename("../x", "a-pi", log); STO.follow_rename("a-pi", "", log)
assert names("a-pi") and STO.read_state()["pending_renames"] == []
PY

echo "== 72 Veröffentlichen nur mit den Änderungen (Delta) und Prüfsumme"
pub72() {
    local G; G="$(mktemp -d)"
    export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@x GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@x
    git init -q --bare -b main "$G/remote.git" || return 1
    mkdir -p "$G/src" "$G/pub" && cp -r "$SRC/." "$G/src/" && rm -rf "$G/src/.git" "$G/src/data" "$G/src/log" || return 1
    echo 9.9.0 > "$G/src/VERSION"
    (cd "$G/src" && FULL=1 OUT="$G/pub/full.sh" bash devtools/build_publish.sh "Test 9.9.0" >/dev/null) || return 1
    (cd "$G/pub" && HSP_WORK="$G/pub/work" HSP_REPO="$G/remote.git" bash full.sh >/dev/null 2>&1) || return 1
    # Entwicklungsstand als git-Klon des Veröffentlichten
    (cd "$G/src" && git init -q -b main && git remote add origin "$G/remote.git" && git fetch -q origin main && git reset -q origin/main) || return 1
    echo 9.9.1 > "$G/src/VERSION"
    printf '\n## 9.9.1 – Test\n' >> "$G/src/CHANGELOG.md"
    echo "neu" > "$G/src/docs/neu-9.9.1.md"
    rm "$G/src/docs/testing.md"
    printf '\x89PNG\x00\x01\x02binär' > "$G/src/docs/screenshots/test.png"
    chmod +x "$G/src/bandplans/README.md" 2>/dev/null || chmod +x "$G/src/LICENSE"
    local L; L="$(cd "$G/src" && OUT="$G/pub/delta.sh" bash devtools/build_publish.sh "Test 9.9.1")" || return 1
    echo "$L" | grep -q "nur Änderungen seit 9.9.0" || { echo "$L"; return 1; }
    [ "$(stat -c %s "$G/pub/delta.sh")" -lt $(( $(stat -c %s "$G/pub/full.sh") / 4 )) ] || { echo "Delta nicht kleiner"; return 1; }
    local O; O="$(cd "$G/pub" && echo j | HSP_WORK="$G/pub/work" HSP_REPO="$G/remote.git" bash delta.sh 2>&1)" || { echo "$O" | tail -8; return 1; }
    echo "$O" | grep -q "Änderungen seit 9.9.0 eingespielt und geprüft" || { echo "$O" | tail -8; return 1; }
    # auf „GitHub“ steht jetzt genau der Entwicklungsstand
    local want got
    want="$(cd "$G/src" && GIT_INDEX_FILE="$G/idx" git read-tree HEAD && GIT_INDEX_FILE="$G/idx" git add -A && GIT_INDEX_FILE="$G/idx" git write-tree)"
    got="$(git -C "$G/remote.git" rev-parse 'main^{tree}')"
    [ "$want" = "$got" ] || { echo "Baum $got statt $want"; return 1; }
    git -C "$G/remote.git" tag | grep -q v9.9.1 || return 1
    git -C "$G/remote.git" show main:docs/testing.md >/dev/null 2>&1 && return 1
    # veraltete Delta-Datei (baut auf 9.9.0 auf, GitHub ist schon weiter): bricht ab, nichts ändert sich
    local before; before="$(git -C "$G/remote.git" rev-parse main)"
    # der Entwicklungsklon hat GitHub seit 9.9.0 nicht mehr geholt (HSP_BASE: nicht neu holen)
    L="$(cd "$G/src" && echo 9.9.2 > VERSION && HSP_BASE=origin/main OUT="$G/pub/alt.sh" bash devtools/build_publish.sh "Test 9.9.2")" || return 1
    echo "$L" | grep -q "nur Änderungen seit 9.9.0" || { echo "$L"; return 1; }
    O="$(cd "$G/pub" && HSP_WORK="$G/pub/work" HSP_REPO="$G/remote.git" bash alt.sh 2>&1)" && { echo "veraltete Datei lief durch"; echo "$O" | tail -12; return 1; }
    echo "$O" | grep -q "nur die Änderungen seit Version 9.9.0 – auf GitHub steht aber 9.9.1" || { echo "$O" | tail -4; return 1; }
    [ "$(git -C "$G/remote.git" rev-parse main)" = "$before" ] || return 1
    rm -rf "$G"
}
big="$(find "$SRC/docs/screenshots" -name "*.png" -size +400k)"
[ -z "$big" ] && ok "Doku-Bilder unter 400 KB (sonst devtools/shrink_screenshots.sh)" || bad "Doku-Bilder zu groß: $big"
pub72 && ok "Delta-Datei viel kleiner, Ergebnis gleich dem Entwicklungsstand (neu, geändert, gelöscht, binär, Rechte), veraltete Datei bricht ohne Änderung ab" || bad "Veröffentlichen mit Delta"

echo "== 73 SSTV-Termine: Forum und AMSAT-UK auswerten, zusammenführen, eigene Einträge, eigener Empfang"
(cd bin && timeout 30 python3 - "$SRC" <<'PY') && ok "Termine aus beiden Quellen zu einem mit Uhrzeiten und beiden Links, Ausfall einer Quelle behält ihre Termine, eigene Einträge geprüft, Zustand läuft/kommt/vorbei" || bad "SSTV-Termine"
import sys, tempfile, time, sqlite3
from pathlib import Path
import sstv_events as S
tmp = Path(tempfile.mkdtemp()); S.FILE = tmp / "ev.json"
now = time.time()
d = lambda t: time.strftime("%Y-%m-%d", time.gmtime(t))
lt = lambda t: time.strftime("%A, %B %d, %Y", time.gmtime(t))
a, b = now - 86400, now + 2 * 86400
libre = f"""<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>ISS SSTV Event - from {d(a)} to {d(b)}</title><link>https://community.libre.space/t/iss/1</link><description>&lt;p&gt;Robot 36 on 437.550 MHz&lt;/p&gt;</description></item>
<item><title>UmKA-1 SSTV - from {d(now + 5 * 86400)} to {d(now + 6 * 86400)}</title><link>https://community.libre.space/t/umka/2</link><description>PD120</description></item>
<item><title>Some other topic</title><link>x</link><description>y</description></item></channel></rss>""".encode()
amsat = f"""<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>A</title>
<item><title>ISS Slow Scan TV</title><link>https://amsat-uk.org/iss-sstv</link><description>short</description>
<content:encoded>&lt;p&gt;An SSTV event on the ISS is scheduled to begin on {lt(a)} around 15:10 UTC and will end on {lt(b)} at 17:45 UTC.&lt;/p&gt;</content:encoded></item>
</channel></rss>""".encode()
feeds = {S.SOURCES["libre"]["url"]: libre, S.SOURCES["amsatuk"]["url"]: amsat}
S.refresh(force=True, fetch=lambda u: feeds[u])
ev = S.events(settings={})
iss = [e for e in ev if e["sat"] == "ISS"]
assert len(iss) == 1 and iss[0]["state"] == "running" and not iss[0]["dates_only"], ev
assert time.strftime("%H:%M", time.gmtime(iss[0]["start"])) == "15:10" and len(iss[0]["links"]) == 2 and iss[0]["mode"] == "Robot 36"
assert iss[0]["freq"] == "437.550" and sorted(iss[0]["sources"]) == ["amsatuk", "libre"]
um = [e for e in ev if e["sat"] == "UmKA-1"]
assert um and um[0]["state"] == "upcoming" and um[0]["dates_only"] and um[0]["mode"] == "PD120"
# eine Quelle fällt aus: ihre Termine bleiben, der Fehler steht im Zustand
def bad(u):
    if "amsat" in u: raise OSError("Netz weg")
    return feeds[u]
S.refresh(force=True, fetch=bad)
st = S.status(); am = [x for x in st["sources"] if x["key"] == "amsatuk"][0]
assert am["error"] and "Netz weg" in am["error"] and [e for e in S.events(settings={}) if e["sat"] == "ISS"][0]["links"].__len__() == 2
# nicht fällig: kein neuer Abruf
called = []
S.refresh(fetch=lambda u: called.append(u))
assert not called
# eigene Einträge
m = S.clean_manual({"sat": "SONATE-2", "from": d(now + 86400), "to": d(now + 2 * 86400) + " 18:00", "freq": "145.880", "mode": "martin1", "link": "https://example.org/x"})
assert m["mode"] == "Martin 1" and m["freq"] == "145.880"
for badin in ({"sat": "", "from": "2026-01-01", "to": "2026-01-02"}, {"sat": "X", "from": "morgen", "to": "2026-01-02"},
              {"sat": "X", "from": "2026-01-03", "to": "2026-01-02"}, {"sat": "X", "from": "2026-01-01", "to": "2026-01-02", "link": "javascript:alert(1)"}):
    try:
        S.clean_manual(badin); raise SystemExit(f"angenommen: {badin}")
    except ValueError:
        pass
ev = S.events(settings={"sstv_events_manual": [m]})
assert any(e["sat"] == "SONATE-2" and "manual" in e["sources"] and e.get("manual_index") == 0 for e in ev)
# lang Vergangenes fällt weg, kürzlich Beendetes bleibt (vorbei)
old = S.clean_manual({"sat": "Alt", "from": d(now - 10 * 86400), "to": d(now - 9 * 86400)})
rec = S.clean_manual({"sat": "Neu", "from": d(now - 2 * 86400), "to": d(now - 2 * 86400)})
names = {e["sat"]: e["state"] for e in S.events(settings={"sstv_events_manual": [old, rec]})}
assert "Alt" not in names and names.get("Neu") == "ended", names
# eigener Empfang aus der Datenbank
db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE scan (id INTEGER PRIMARY KEY, time TEXT, band TEXT)")
db.execute("CREATE TABLE peak (id INTEGER PRIMARY KEY, scan_id INTEGER, mode TEXT, segment_id TEXT)")
t = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - 3600))
db.execute("INSERT INTO scan VALUES (1, ?, 'Satelliten')", (t,))
for i in range(3):
    db.execute("INSERT INTO peak (scan_id, mode, segment_id) VALUES (1, 'sstv', 'iss_sstv70')")
db.execute("INSERT INTO peak (scan_id, mode, segment_id) VALUES (1, 'sstv', 'umka1')")
r = S.received(db)
assert [x["id"] for x in r] and {x["id"]: x["n"] for x in r} == {"iss_sstv70": 3, "umka1": 1}, r
assert S.received_in(db, iss[0]) == 3
W = Path(sys.argv[1]) / "web"
assert 'id="panel-sstvev"' in (W / "index.html").read_text() and "loadSstvEvents" in (W / "app.js").read_text()
PY

echo "== 74 Live-Favoriten, alle Live-Dekoder wählbar"
(cd bin && timeout 60 python3 - "$SRC" <<'PY') && ok "Favoriten geprüft gespeichert (Frequenz, Demodulator, Raster, Dekoder), Unsinn abgewiesen; CW/RTTY/SSTV/NAVTEX/Wetterfax lassen sich in Live wählen" || bad "Live-Favoriten"
import sys
from pathlib import Path
import webapp as W, live_decode as LDK
f = W.clean_live_favorites([{"name": " DWD Wetterfax ", "freq_hz": 7878100, "mode": "usb", "step_hz": 100, "dec": "wefax"},
                             {"name": "", "freq_hz": 145500000.4, "mode": "nfm", "step_hz": 999999, "dec": "quatsch"}])
assert f[0] == {"name": "DWD Wetterfax", "freq_hz": 7878100, "mode": "usb", "step_hz": 100, "dec": "wefax"}, f
assert f[1]["name"] == "145.5000 MHz" and f[1]["step_hz"] == 0 and f[1]["dec"] == "auto" and f[1]["freq_hz"] == 145500000
for bad in ([{"freq_hz": 5, "mode": "nfm"}], [{"freq_hz": 145e6, "mode": "xyz"}], "keine Liste", [{"freq_hz": 145e6, "mode": "nfm"}] * 101):
    try:
        W.clean_live_favorites(bad); raise SystemExit(f"angenommen: {str(bad)[:60]}")
    except ValueError:
        pass
src = (Path(sys.argv[1]) / "bin" / "webapp.py").read_text()
assert "_live_dec_kind[0] = kind if kind in LDK.KINDS" in src and {"cw", "rtty", "sstv", "navtex", "wefax"} <= set(LDK.KINDS)
html = (Path(sys.argv[1]) / "web" / "index.html").read_text()
opts = set(__import__("re").findall(r'value="([a-z0-9]+)"', html.split('id="liveDecKind"')[1].split("</select>")[0]))
assert opts <= set(LDK.KINDS), opts - set(LDK.KINDS)                 # jede Auswahl im Dashboard kennt der Server
js = (Path(sys.argv[1]) / "web" / "app.js").read_text()
assert 'id="liveFav"' in html and 'id="liveFavStar"' in html and "saveLiveFavs" in js and "/api/live/favorites" in js
PY

echo "== 75 Dienste und Systemdateien hamscanpi-* → hesperosdx-* (sicherer Übergang)"
ren75() {
    local R0; R0="$(mktemp -d)"
    mkfake() {   # mkfake <wurzel> <neue dienste aktiv: 1/0> <alter update-dienst aktiv: 1/0>
        local R="$1"
        mkdir -p "$R/etc/systemd/system" "$R/etc/sudoers.d" "$R/usr/local/sbin" "$R/etc/systemd/journald.conf.d" \
                 "$R/etc/systemd/logind.conf.d" "$R/etc/NetworkManager/conf.d" "$R/etc/udev/rules.d" "$R/etc/hamscanpi" "$R/mnt/hamscanpi-usb" "$R/bin"
        for u in web scheduler rescue mdns update; do echo "[Service]" > "$R/etc/systemd/system/hamscanpi-$u.service"; done
        mkdir -p "$R/etc/systemd/system/hamscanpi-web.service.d"
        touch "$R/etc/sudoers.d/hamscanpi" "$R/etc/sudoers.d/hesperosdx" "$R/usr/local/sbin/hamscanpi-net" "$R/usr/local/sbin/hamscanpi-store" \
              "$R/usr/local/sbin/hesperosdx-net" "$R/usr/local/sbin/hesperosdx-store"
        echo J > "$R/etc/systemd/journald.conf.d/hamscanpi.conf"; echo L > "$R/etc/systemd/logind.conf.d/hamscanpi.conf"
        echo W > "$R/etc/NetworkManager/conf.d/hamscanpi-wifi.conf"; echo U > "$R/etc/udev/rules.d/20-hamscanpi-rtlsdr.rules"
        echo "username=x" > "$R/etc/hamscanpi/nas.cred"
        cat > "$R/bin/systemctl" <<SC
#!/bin/bash
echo "\$*" >> "$R/calls"
if [ "\$1" = is-active ]; then
    u="\${@: -1}"
    case "\$u" in hesperosdx-*) [ "$2" = 1 ];; hamscanpi-update) [ "$3" = 1 ];; *) false;; esac
    exit \$?
fi
exit 0
SC
        chmod +x "$R/bin/systemctl"
    }
    run() {   # run <wurzel> <ping ok: true/false>
        ( t() { printf '%s' "$1"; }; ok() { :; }; warn() { echo "WARN $*"; }; bold() { :; }; asuser() { :; }
          R="$1"; SYSTEMCTL="$1/bin/systemctl"; PING="$2"; MOUNTPOINT=false; WAIT_S=2; RESTART=1; PORT=8080; DIR=/x
          . "$SRC/bin/migrate_services.sh"; migrate_files; start_services )
    }
    # 1) Umstieg klappt; das Update läuft noch im alten Dienst
    local A="$R0/a"; mkfake "$A" 1 1
    run "$A" true >/dev/null || { echo "Umstieg meldet Fehler"; return 1; }
    for u in web scheduler rescue mdns; do [ ! -e "$A/etc/systemd/system/hamscanpi-$u.service" ] || { echo "alter Dienst $u blieb"; return 1; }; done
    [ ! -e "$A/etc/systemd/system/hamscanpi-web.service.d" ] && [ -e "$A/etc/systemd/system/hamscanpi-update.service" ] || { echo "Update-Dienst/Drop-in"; return 1; }
    [ ! -e "$A/etc/sudoers.d/hamscanpi" ] && [ ! -e "$A/usr/local/sbin/hamscanpi-net" ] && [ ! -e "$A/usr/local/sbin/hamscanpi-store" ] || { echo "alte sudo-Regel/Helfer"; return 1; }
    [ "$(cat "$A/etc/systemd/journald.conf.d/hesperosdx.conf")" = J ] && [ "$(cat "$A/etc/systemd/logind.conf.d/hesperosdx.conf")" = L ] \
      && [ -f "$A/etc/NetworkManager/conf.d/hesperosdx-wifi.conf" ] && [ -f "$A/etc/udev/rules.d/20-hesperosdx-rtlsdr.rules" ] \
      && [ ! -e "$A/etc/systemd/journald.conf.d/hamscanpi.conf" ] || { echo "Systemeinstellungen"; return 1; }
    [ -f "$A/etc/hesperosdx/nas.cred" ] && [ ! -e "$A/etc/hamscanpi" ] && [ "$(stat -c %a "$A/etc/hesperosdx")" = 700 ] && [ ! -e "$A/mnt/hamscanpi-usb" ] || { echo "Zugangsdaten/Einhängepunkt"; return 1; }
    # erst die alten stoppen (Port frei), dann die neuen starten
    local sl rl
    sl="$(grep -n "^stop hamscanpi-web" "$A/calls" | head -1 | cut -d: -f1)"; rl="$(grep -n "^restart hesperosdx-web" "$A/calls" | head -1 | cut -d: -f1)"
    [ -n "$sl" ] && [ -n "$rl" ] && [ "$sl" -lt "$rl" ] || { echo "Reihenfolge"; cat "$A/calls"; return 1; }
    grep -q "^stop hamscanpi-update" "$A/calls" && { echo "laufendes Update gestoppt"; return 1; }
    # 2) nächster Lauf: der alte Update-Dienst ist fertig und verschwindet
    : > "$A/calls"; sed -i 's/hamscanpi-update) \[ "1" = 1 \]/hamscanpi-update) [ "0" = 1 ]/' "$A/bin/systemctl"
    run "$A" true >/dev/null && [ ! -e "$A/etc/systemd/system/hamscanpi-update.service" ] || { echo "Update-Dienst blieb"; return 1; }
    grep -q "^restart hesperosdx-web hesperosdx-scheduler hesperosdx-rescue hesperosdx-mdns" "$A/calls" || return 1
    # 3) neue Dienste kommen nicht hoch: alte laufen weiter, nichts Altes gelöscht
    local B="$R0/b"; mkfake "$B" 0 1
    local O; O="$(run "$B" false)" && { echo "Fehlschlag nicht erkannt"; return 1; }
    echo "$O" | grep -q "WARN" || return 1
    for u in web scheduler rescue mdns; do [ -e "$B/etc/systemd/system/hamscanpi-$u.service" ] && grep -q "^start hamscanpi-$u" "$B/calls" || { echo "alter Dienst $u nicht zurück"; return 1; }; done
    grep -q "^disable hesperosdx-web" "$B/calls" && [ -e "$B/etc/sudoers.d/hamscanpi" ] && [ -e "$B/usr/local/sbin/hamscanpi-net" ] || { echo "Rückfall unvollständig"; return 1; }
    rm -rf "$R0"
}
ren75 && ok "alte Dienste erst nach erfolgreichem Start der neuen entfernt, laufendes Update bleibt bis zum Ende, Rückfall auf die alten, Systemdateien und Zugangsdaten umgezogen" || bad "Umstellung der Dienstnamen"

rm -f /tmp/hsp_*.u8 /tmp/hsp_hf.txt /tmp/hsp_lrpt.txt /tmp/hsp_ft.txt
if [ "$FAIL" = 0 ]; then echo "Selbsttest fertig: alles ok."; else echo "Selbsttest: $FAIL FEHLER"; exit 1; fi
