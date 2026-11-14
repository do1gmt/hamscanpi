#!/usr/bin/env python3
"""
Liegengebliebene Satelliten-Rohaufnahmen nachträglich auswerten.

sat_pass.py schreibt während eines Überflugs je FM-Kanal eine Rohdatei
recordings/.sat_<kanal>_<pid>.r16 (I/Q, int16, 24 kHz, Doppler schon heraus; bis 1.7.94 .raw mit float32)
und löscht sie nach
der Auswertung. Wurde die Auswertung abgebrochen (z. B. vom System bei Speichermangel), bleibt die
Datei liegen – mit ihr lassen sich SSTV-Bilder, APRS-Pakete und Sprachdurchgänge noch retten.

  python3 bin/sat_recover.py            auswerten, Treffer speichern, Rohdateien danach löschen
  python3 bin/sat_recover.py --list     nur anzeigen
"""
import argparse
import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import satellites as SAT                                  # noqa: E402
import sat_pass as SP                                     # noqa: E402

RATE = 24000
NAME = re.compile(r"^\.sat_(?P<id>[a-z0-9_]+?)_(?P<tag>[0-9a-z]+)\.(raw|r16)$")


def channel_from_raw(path):
    m = NAME.match(path.name)
    if not m or m.group("id") not in SAT.BY_ID:
        return None
    sat = SAT.BY_ID[m.group("id")]
    if sat["kind"] != "fm":
        return None
    z, k = SP.open_iq(path)
    n = len(z)
    if n < RATE * 5:
        return None
    ch = SP.Channel.__new__(SP.Channel)                    # ohne __init__: keine neue Rohdatei anlegen
    ch.sat, ch.f0, ch.kind, ch.tmp, ch.hits = sat, float(sat["center_hz"]), "fm", path, []
    ch.aud = path.with_suffix(".a16")                       # Ton auf die Karte, wie bei der Aufnahme
    ch.aud_n, pw = 0, []
    af = open(ch.aud, "wb")
    step = RATE // 10                                      # 0,1 s, wie bei der Aufnahme
    prev = 0j
    for a in range(0, n, RATE * 10):                       # blockweise: wenig Speicher
        b = min(n, a + RATE * 10)
        c = (z[a:b, 0].astype(np.float64) + 1j * z[a:b, 1].astype(np.float64)) * k
        pr = np.concatenate(([prev], c[:-1]))
        prev = c[-1]
        a_ = (np.angle(c * np.conj(pr)) * RATE / (2 * np.pi) / 5000.0).astype(np.float32)
        af.write(SP.to_i16(a_, SP.AUD_SCALE).tobytes())
        ch.aud_n += len(a_)
        p = np.abs(c) ** 2
        pw.extend(float(np.mean(p[k:k + step])) for k in range(0, len(p) - step + 1, step))
    af.close()
    pw = np.asarray(pw)
    floor = float(np.percentile(pw, 20)) or 1e-20          # ruhige Abschnitte als Rauschbezug
    ch.snr_track = list(pw / floor)
    dur = n / RATE
    t0 = path.stat().st_mtime - dur                        # Datei wurde bis zum Ende des Überflugs geschrieben
    return ch, t0, dur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    import housekeeping as HK
    files = sorted(HK.sat_raw_orphans(SP.RECDIR), key=lambda p: p.stat().st_mtime)   # nie die eines laufenden Überflugs
    if not files:
        print("Keine liegengebliebenen Rohaufnahmen (die eines gerade laufenden Überflugs zählen nicht).")
        return
    total = 0
    for p in files:
        size = p.stat().st_size / 1e6
        when = time.strftime("%d.%m. %H:%M UTC", time.gmtime(p.stat().st_mtime))
        r = channel_from_raw(p)
        if r is None:
            print(f"{p.name}: {size:.0f} MB, {when} – kein FM-Kanal oder zu kurz, übersprungen")
            continue
        ch, t0, dur = r
        print(f"{p.name}: {ch.sat['label']}, {dur/60:.1f} min, Ende {when}, {size:.0f} MB")
        if a.list:
            continue
        hits = ch.evaluate(t0)                             # löscht die Rohdatei am Ende
        n = SP.db_save(ch.sat, hits)
        total += n
        st = getattr(ch, "stats", {}) or {}
        print(f"   → {n} Treffer · Signal max {st.get('max_db')} dB, 90 % {st.get('p90_db')} dB, "
              f"{st.get('above6_s')} s über 6 dB über dem Rauschen")
    if not a.list:
        print(f"Fertig: {total} Treffer. Im Dashboard unter Treffer (Filter SSTV) ansehen.")


if __name__ == "__main__":
    main()
