#!/usr/bin/env python3
"""Handfunken- und Empfangstest: die nächsten gut hörbaren FM-Satelliten-Überflüge und, für einen davon,
eine Minutentabelle mit Richtung, Höhe und der Frequenz, die an der Handfunke einzustellen ist
(Doppler auf 5-kHz-Raster gerundet). Rechnet mit den Bahndaten und dem Locator von HamScanPi.

   python3 ~/hamscanpi/bin/funktest.py            Überflüge der nächsten 24 h (ab 20°)
   python3 ~/hamscanpi/bin/funktest.py 3          Minutentabelle für Überflug Nr. 3
"""
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "bin"))
import satellites as SAT  # noqa: E402

# FM-Satelliten mit Sprache/Paketen, die man mit einer Handfunke eindeutig erkennt
TEST = [("so50", "SO-50 (Sprache, nur wenn jemand ihn mit 74,4 Hz auftastet)"),
        ("ao123", "AO-123 / ASRTU-1 (Sprache)"),
        ("iss_rpt", "ISS-Relais (Sprache, nicht immer eingeschaltet)"),
        ("iss_aprs", "ISS APRS-Digipeater (kurze Paket-Schnarrer)"),
        ("ao91", "AO-91 (Sprache, nur bei Sonne)"),
        ("po101", "PO-101 (Sprache, nur zu angekündigten Zeiten)"),
        ("meteor23", "Meteor-M N2-3 (LRPT 137,900/137,100 – digital: Test für den Pi, Handfunke hört nur Rauschen)"),
        ("meteor24", "Meteor-M N2-4 (LRPT 137,900/137,100 – digital: Test für den Pi, Handfunke hört nur Rauschen)")]
MIN_EL = 20.0
STEP_HZ = 5000


def local(t):
    lt = time.localtime(t)
    return ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")[lt.tm_wday] + time.strftime(" %H:%M", lt)


def main():
    st = json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
    loc = st.get("my_locator", "")
    lat, lon = SAT.locator_to_latlon(loc)
    tles, _ = SAT.load_tles(fetch=False)                    # {norad: [name, l1, l2]}
    now = time.time()
    rows = []
    for sid, desc in TEST:
        sat = SAT.BY_ID.get(sid)
        tle = tles.get(sat["norad"]) if sat else None
        if not tle:
            continue
        tr = SAT.Tracker(tle, lat, lon)
        for p in SAT.passes(tr, now, hours=24, min_el=MIN_EL):
            rows.append((p["aos"], p["los"], p["max_el"], sid, desc, sat["center_hz"], tr))
    rows.sort(key=lambda r: r[0])
    if not rows:
        print("Keine Überflüge ab 20° in den nächsten 24 h (oder keine Bahndaten).")
        return
    if len(sys.argv) < 2:
        print(f"Locator {loc} – FM-Satelliten ab {MIN_EL:.0f}° in den nächsten 24 h (Ortszeit):\n")
        for i, (aos, los, el, sid, desc, f, _) in enumerate(rows, 1):
            print(f"{i:2d}. {local(aos)}–{time.strftime('%H:%M', time.localtime(los))}  {el:3.0f}°  "
                  f"{f / 1e6:.3f} MHz  {desc}")
        print("\nMinutentabelle: python3 ~/hamscanpi/bin/funktest.py <Nr>")
        return
    aos, los, el, sid, desc, f, tr = rows[int(sys.argv[1]) - 1]
    print(f"{desc}\nNennfrequenz {f / 1e6:.3f} MHz · Wide (25 kHz) · Rauschsperre aus · "
          f"höchste Stelle {el:.0f}°\n")
    print(" Uhrzeit  Richtung  Höhe   an der Handfunke einstellen")
    t = aos - aos % 60 + 60
    last = None
    while t <= los:
        e, az, _, _ = tr.look(t)
        if e >= 0:
            dial = f + tr.doppler_hz(f, t)
            dial = round(dial / STEP_HZ) * STEP_HZ
            mark = "  <- umstellen" if last is not None and dial != last else ""
            print(f" {time.strftime('%H:%M', time.localtime(t))}    {az:3.0f}°    {e:3.0f}°   "
                  f"{dial / 1e6:.3f} MHz{mark}")
            last = dial
        t += 60
    print("\nRichtung: 0° Nord, 90° Ost, 180° Süd, 270° West. Antenne Richtung Satellit neigen und langsam drehen.")


if __name__ == "__main__":
    main()
