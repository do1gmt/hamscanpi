"""Prüfsatellit simulieren: uint8-I/Q mit 2,048 MS/s, Rauschen plus ein LRPT-ähnliches Band (±60 kHz),
das über die Dauer an- und abschwillt (wie ein Überflug).
   python3 sim_lrpt.py OUT.u8 DAUER_S ABLAGE_HZ PEGEL      (ABLAGE relativ zur Abstimmung, PEGEL 0 = nur Rauschen)"""
import sys
import numpy as np
from scipy.signal import firwin, lfilter
FS = 2048000
out, dur, off, amp = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
h = firwin(255, 60000, fs=FS)
rng = np.random.default_rng(1)
n = np.arange(FS // 10)
zi = np.zeros(254, complex)
with open(out, "wb") as f:
    for b in range(dur * 10):
        x = rng.standard_normal(FS // 10) + 1j * rng.standard_normal(FS // 10)
        y, zi = lfilter(h, 1, x, zi=zi)
        env = amp * np.sin(np.pi * (b + 0.5) / (dur * 10)) ** 2
        z = 0.08 * (rng.standard_normal(FS // 10) + 1j * rng.standard_normal(FS // 10)) \
            + 0.25 * env * y * np.exp(2j * np.pi * off * (n + b * FS // 10) / FS)
        u = np.empty(2 * len(z), np.uint8)
        u[0::2] = np.clip(z.real * 127.5 + 127.5, 0, 255)
        u[1::2] = np.clip(z.imag * 127.5 + 127.5, 0, 255)
        f.write(u.tobytes())
