#!/usr/bin/env python3

import json
import sqlite3
from pathlib import Path

import numpy as np
import soundfile as sf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import butter, sosfiltfilt, resample_poly

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "scanner.db"
CFG = BASE / "config" / "scanner.json"
OUT = BASE / "reports"

OUT.mkdir(parents=True, exist_ok=True)

with open(CFG, "r", encoding="utf-8") as f:
    cfg = json.load(f)

RATE = int(cfg.get("sample_rate", 48000))
PPM = float(cfg.get("frequency_correction_ppm", 0.0))
CONJ = bool(cfg.get("iq_conjugate", False))


def corrected_center(center_hz):
    return center_hz * (1.0 + PPM * 1e-6)


def load_iq(path):
    data, rate = sf.read(path, always_2d=True)

    if data.shape[1] < 2:
        raise RuntimeError("Keine Stereo-I/Q-Datei")

    i = data[:, 0]
    q = data[:, 1]

    z = i + 1j * q

    if CONJ:
        z = np.conj(z)

    z -= np.mean(z)

    return z, rate


def shift_to_baseband(z, rate, offset_hz):
    n = np.arange(len(z))
    osc = np.exp(
        -2j * np.pi * offset_hz * n / rate
    )
    return z * osc


def demod_ssb(z, rate, mode):
    f = np.fft.fftfreq(len(z), 1.0 / rate)
    Z = np.fft.fft(z)

    if mode == "USB":
        mask = (f >= 250) & (f <= 3000)
    else:
        mask = (f <= -250) & (f >= -3000)

    Zf = np.zeros_like(Z)
    Zf[mask] = Z[mask]

    audio_complex = np.fft.ifft(Zf)

    if mode == "USB":
        audio = np.real(audio_complex)
    else:
        audio = np.real(np.conj(audio_complex))

    sos = butter(
        6,
        [250, 3000],
        btype="bandpass",
        fs=rate,
        output="sos"
    )

    audio = sosfiltfilt(sos, audio)

    # auf 12 kHz runter
    audio = resample_poly(audio, 1, 4)
    outrate = rate // 4

    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak * 0.85

    return audio.astype(np.float32), outrate


def make_spectrum(z, rate, rf_center, rf_hit, outfile):
    n = min(len(z), rate * 5)

    z = z[:n]

    win = np.hanning(len(z))
    spec = np.fft.fftshift(
        np.fft.fft(z * win)
    )

    freq = np.fft.fftshift(
        np.fft.fftfreq(len(z), 1 / rate)
    )

    mag = 20 * np.log10(
        np.maximum(np.abs(spec), 1e-12)
    )

    rf = (rf_center + freq) / 1e6

    fig = plt.figure(figsize=(10, 5))
    ax = fig.add_subplot(111)

    ax.plot(rf, mag)
    ax.axvline(
        rf_hit / 1e6,
        linestyle="--"
    )

    ax.set_xlim(
        (rf_hit - 6000) / 1e6,
        (rf_hit + 6000) / 1e6
    )

    ax.set_xlabel("MHz")
    ax.set_ylabel("rel. Pegel [dB]")
    ax.set_title(
        f"Treffer {rf_hit/1e6:.6f} MHz"
    )
    ax.grid(True)

    fig.tight_layout()
    fig.savefig(outfile, dpi=140)
    plt.close(fig)


db = sqlite3.connect(DB)

rows = db.execute("""
SELECT
    p.id,
    p.rf_hz,
    p.recording_path,
    COALESCE(p.rec_center_hz, s.center_hz),
    s.band,
    COALESCE(p.mode, ''),
    COALESCE(p.sideband, '')
FROM peak p
JOIN scan s ON s.id = p.scan_id
WHERE p.recording_path IS NOT NULL
ORDER BY p.id
""").fetchall()

if not rows:
    print("Keine Aufzeichnungen gefunden.")
    raise SystemExit(0)

count = 0

for peak_id, rf_hz, recpath, center_hz, band, mode, sideband in rows:
    src = Path(recpath)
    if not src.exists():
        print("FEHLT:", src)
        continue

    stem = src.stem
    usbfile = OUT / f"{stem}_USB.wav"
    lsbfile = OUT / f"{stem}_LSB.wav"
    pngfile = OUT / f"{stem}_spectrum.png"

    # Welche Seitenbaender werden gebraucht?
    # Sprache/SSTV: nur das laut Bandplan richtige.
    # FM/AM/Sat/Daten/Dauertraeger und VHF-Aufnahmen: keins (analyze_fm / analyze_vhf).
    sideband = str(sideband).upper()
    if mode in ("voice", "sstv") and sideband in ("USB", "LSB"):
        wanted = [sideband]
    elif mode in ("fm", "sat", "data", "carrier", "noise", "am", "wfm", "relais", "dv", "aprs", "pocsag") or float(center_hz) >= 30e6:
        wanted = []
    else:
        wanted = ["USB", "LSB"]

    need_usb = "USB" in wanted and not usbfile.exists()
    need_lsb = "LSB" in wanted and not lsbfile.exists()
    if not need_usb and not need_lsb and pngfile.exists():
        continue

    z, rate = load_iq(src)

    cc = corrected_center(center_hz)
    offset = rf_hz - cc

    bb = shift_to_baseband(
        z,
        rate,
        offset
    )

    if need_usb:
        usb, arate = demod_ssb(bb, rate, "USB")
        sf.write(usbfile, usb, arate, subtype="PCM_16")

    if need_lsb:
        lsb, arate = demod_ssb(bb, rate, "LSB")
        sf.write(lsbfile, lsb, arate, subtype="PCM_16")

    if not pngfile.exists():
        make_spectrum(
            z,
            rate,
            cc,
            rf_hz,
            pngfile
        )

    print(
        f"{band:4s}  "
        f"{rf_hz/1e6:10.6f} MHz  "
        f"{mode or '?'}  {' '.join(wanted) or '-'}"
    )

    count += 1

db.close()

print()
print("Neu ausgewertet:", count)
print("Ausgabe:", OUT)
