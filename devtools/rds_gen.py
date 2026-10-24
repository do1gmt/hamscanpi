#!/usr/bin/env python3
"""RDS-Testsignal: MPX (Ton + 19-kHz-Pilot + RDS auf 57 kHz) bzw. FM-modulierte I/Q-Daten.
Enthält PS, PTY, TP/TA, RT (mit Umlauten), RT+, CT, AF, ECC – zum Prüfen von bin/rds.py."""
import sys
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfiltfilt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import rds as R  # noqa: E402

EBU_REV = {v: k for k, v in R._EBU.items()}


def enc(s):
    return [EBU_REV.get(c, ord(c)) for c in s]


def group(A, B, C, D, cprime=False):
    out = []
    for data, off in ((A, "A"), (B, "B"), (C, "Cp" if cprime else "C"), (D, "D")):
        w = (data << 10) | R.checkword(data, off)
        out += [(w >> (25 - k)) & 1 for k in range(26)]
    return out


def groups(pi=0xD3C2, ps="TEST FM ", rt="Hallo Welt – Grüße aus JN59 äöü ß\r", pty=10):
    g = []
    psb = enc(ps)
    rtb = enc(rt.replace("–", "-"))
    rtb += [0x20] * (64 - len(rtb))
    tp, ta = 1, 0
    # 3A: RT+ in Gruppe 11A (Code 11<<1|0 = 22)
    g += group(pi, (3 << 12) | (tp << 10) | (pty << 5) | 22, 0, 0x4BD7)
    # 1A: ECC E0 (Deutschland)
    g += group(pi, (1 << 12) | (tp << 10) | (pty << 5), (0 << 12) | 0xE0, 0)
    for rep in range(3):
        for seg in range(4):
            af = (224 + 3) << 8 | 26 if seg == 0 else ((26 << 8) | 75)    # 90.1 / 95.0 MHz
            g += group(pi, (0 << 12) | (tp << 10) | (pty << 5) | (ta << 4) | (1 << 3) | seg, af,
                       (psb[seg * 2] << 8) | psb[seg * 2 + 1])
        for seg in range(16):
            c = rtb[seg * 4:seg * 4 + 4]
            g += group(pi, (2 << 12) | (tp << 10) | (pty << 5) | seg, (c[0] << 8) | c[1], (c[2] << 8) | c[3])
    # 4A: 24.10.2026 09:30 UTC, +2 h
    mjd = (np.datetime64("2026-10-24") - np.datetime64("1858-11-17")).astype(int)
    hour, minute, offs = 9, 30, 4
    g += group(pi, (4 << 12) | (tp << 10) | (pty << 5) | (mjd >> 15 & 3), ((mjd & 0x7FFF) << 1) | (hour >> 4),
               ((hour & 0xF) << 12) | (minute << 6) | offs)
    # 11A: RT+ – Interpret ab 6 („Welt“, Länge 4-1), Titel ab 0 („Hallo“)
    t1, s1, l1, t2, s2, l2 = 1, 0, 4, 4, 6, 3
    B = (11 << 12) | (tp << 10) | (pty << 5) | (1 << 4) | (1 << 3) | (t1 >> 3)
    C = ((t1 & 7) << 13) | (s1 << 7) | (l1 << 1) | (t2 >> 5)
    D = ((t2 & 0x1F) << 11) | (s2 << 5) | l2
    g += group(pi, B, C, D)
    # TMC: 3A (AID CD46, Ortstabelle 1, national), Eingruppenmeldung „Stau“ A9 AS Allersberg, Ausdehnung 2,
    # Mehrgruppenmeldung „(Q) Fahrstreifen gesperrt“ mit Menge 2 und Länge 3 km in Gegenrichtung
    g += group(pi, (3 << 12) | (tp << 10) | (pty << 5) | 16, (0 << 14) | (1 << 6) | (1 << 2), 0xCD46)
    tmc_single = group(pi, (8 << 12) | (tp << 10) | (pty << 5) | (1 << 3) | 3, (0 << 15) | (0 << 14) | (2 << 11) | 101, 12748)
    free = "0100" + "00010" + "0010" + "00011"
    free = free + "0" * (28 - len(free))
    tmc_multi = (group(pi, (8 << 12) | (tp << 10) | (pty << 5) | 5, (1 << 15) | (1 << 14) | (0 << 11) | 500, 12748)
                 + group(pi, (8 << 12) | (tp << 10) | (pty << 5) | 5, (0 << 15) | (1 << 14) | (0 << 12) | int(free[:12], 2),
                         int(free[12:], 2)))
    g += tmc_single + tmc_single + tmc_multi
    return g * 3


def mpx(rate=240000, seconds=None, noise=0.0, freq_err_hz=0.0, clock_ppm=0.0, **kw):
    bits = groups(**kw)
    diff, prev = [], 0
    for b in bits:
        prev ^= b
        diff.append(prev)
    rb = R.BITRATE * (1 + clock_ppm * 1e-6)
    n = int(len(diff) / rb * rate) if seconds is None else int(seconds * rate)
    t = np.arange(n) / rate
    k = np.minimum((t * rb).astype(int), len(diff) - 1)
    half = ((t * rb) % 1.0) < 0.5
    sym = np.where(half, 1.0, -1.0) * np.where(np.array(diff)[k] == 1, 1.0, -1.0)
    sos = butter(4, 2400, fs=rate, output="sos")
    sym = sosfiltfilt(sos, sym)
    sub = sym * np.cos(2 * np.pi * (R.SUB + freq_err_hz) * t + 0.7)
    rng = np.random.default_rng(3)
    audio = sosfiltfilt(butter(4, 15000, fs=rate, output="sos"), rng.standard_normal(n)) * 0.5
    pilot = 0.09 * np.sin(2 * np.pi * 19000 * t)
    m = 0.8 * audio / (np.max(np.abs(audio)) + 1e-9) + pilot + 0.05 * sub / (np.max(np.abs(sub)) + 1e-9)
    return m + noise * rng.standard_normal(n)


def iq(rate=240000, dev_hz=75000, snr_db=30.0, **kw):
    m = mpx(rate=rate, **kw)
    ph = 2 * np.pi * dev_hz * np.cumsum(m) / rate
    z = np.exp(1j * ph)
    rng = np.random.default_rng(5)
    z = z + 10 ** (-snr_db / 20) * (rng.standard_normal(len(z)) + 1j * rng.standard_normal(len(z))) / np.sqrt(2)
    return z.real.astype(np.float32), z.imag.astype(np.float32)


if __name__ == "__main__":
    info = R.decode_mpx(mpx(), 240000)
    print(info)
