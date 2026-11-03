#!/usr/bin/env python3
"""I/Q-Aufnahmen speicherschonend laden – für die Auswertung nach dem Scan.

Früher wurde jede Aufnahme komplett in doppelter Genauigkeit geladen und in voller Rate verschoben und
gefiltert: dabei lagen 6–8 Kopien gleichzeitig im Speicher. Bei den langen Kurzwellen-Aufnahmen (Weiter-
Aufnehmen bis 90 s, SSTV 120 s) waren das 400–500 MB – genug, um den Pi mit 1 GB lahmzulegen.

Hier wird die Datei in Stücken gelesen, jedes Stück gleich auf die Trefferfrequenz gemischt (Phase läuft
über die Stücke durch) und auf die Zielrate heruntergerechnet (Überlappung, damit an den Stückgrenzen
nichts springt), in einfacher Genauigkeit. Speicher: proportional zur Zielrate statt zur Aufnahmerate.

  baseband(path, offset_hz, out_rate)  komplexes Basisband (complex64), Treffer bei 0 Hz
  head(path, seconds)                  die ersten Sekunden in voller Rate (für das Spektrumbild)
"""
import math

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

CHUNK_S = 2.0


def _dc(path, rate, conj):
    """Gleichanteil über die ganze Datei (zweiter, billiger Durchgang in Stücken)."""
    s, n = np.zeros(2, dtype=np.float64), 0
    for blk in sf.blocks(str(path), blocksize=int(rate * CHUNK_S), dtype="float32", always_2d=True):
        s += blk[:, :2].sum(axis=0, dtype=np.float64)
        n += len(blk)
    if not n:
        return 0j
    m = complex(s[0] / n, s[1] / n)
    return m.conjugate() if conj else m


def info(path):
    i = sf.info(str(path))
    if i.channels < 2:
        raise RuntimeError("Keine Stereo-I/Q-Datei")
    return i.samplerate, i.frames


def baseband(path, offset_hz, out_rate=12000, conj=False):
    """Aufnahme -> komplexes Basisband bei out_rate (complex64), offset_hz (Ablage der Trefferfrequenz von der
    Aufnahmemitte) liegt danach bei 0 Hz. out_rate >= Aufnahmerate: nur mischen, nicht herunterrechnen."""
    rate, frames = info(path)
    dc = _dc(path, rate, conj)
    g = math.gcd(int(out_rate), int(rate))
    up, down = int(out_rate) // g, int(rate) // g
    same = out_rate >= rate
    pad = 20 * down
    w = 2.0 * np.pi * float(offset_hz) / rate
    out, tail, n0 = [], None, 0
    for blk in sf.blocks(str(path), blocksize=int(rate * CHUNK_S), dtype="float32", always_2d=True):
        q = -blk[:, 1] if conj else blk[:, 1]
        z = (blk[:, 0] + 1j * q).astype(np.complex64) - np.complex64(dc)
        n = np.arange(n0, n0 + len(z), dtype=np.float64)
        z *= np.exp(-1j * np.mod(w * n, 2 * np.pi)).astype(np.complex64)
        n0 += len(z)
        if same:
            out.append(z)
            continue
        zz = z if tail is None else np.concatenate([tail, z])
        y = resample_poly(zz, up, down)
        skip = 0 if tail is None else len(tail) * up // down
        out.append(y[skip:skip + len(z) * up // down].astype(np.complex64))
        tail = z[-pad:] if len(z) >= pad else zz[-pad:]
    y = np.concatenate(out) if out else np.zeros(0, np.complex64)
    return y, (rate if same else int(out_rate))


def head(path, seconds=5.0, conj=False):
    """Die ersten Sekunden in voller Rate (complex64, ohne Gleichanteil) – reicht für das Spektrumbild."""
    rate, frames = info(path)
    n = int(min(frames, rate * seconds))
    data, _ = sf.read(str(path), frames=n, dtype="float32", always_2d=True)
    q = -data[:, 1] if conj else data[:, 1]
    z = (data[:, 0] + 1j * q).astype(np.complex64)
    z -= z.mean()
    return z, rate
