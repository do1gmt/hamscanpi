#!/usr/bin/env python3

import sys
import json
import sqlite3
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "scanner.db"
CFG = BASE / "config" / "scanner.json"
REPORTS = BASE / "reports"

if len(sys.argv) != 2:
    print("Aufruf: ssb_calibrate_test.py <Frequenz Hz>")
    raise SystemExit(1)

TARGET = float(sys.argv[1])

cfg = json.loads(
    CFG.read_text(encoding="utf-8")
)

PPM = float(
    cfg.get(
        "frequency_correction_ppm",
        0.0
    )
)

CONJ = bool(
    cfg.get(
        "iq_conjugate",
        False
    )
)


# --------------------------------------------------
# passenden Treffer suchen
# --------------------------------------------------

db = sqlite3.connect(DB)

row = db.execute(
    """
    SELECT
        p.id,
        p.rf_hz,
        p.recording_path,
        s.center_hz,
        s.band
    FROM peak p
    JOIN scan s
      ON s.id = p.scan_id
    WHERE p.recording_path IS NOT NULL
    ORDER BY ABS(p.rf_hz - ?)
    LIMIT 1
    """,
    (TARGET,)
).fetchone()

db.close()

if not row:
    raise SystemExit(
        "Kein Treffer mit I/Q-Aufnahme gefunden."
    )

peak_id, rf_hz, path, center_hz, band = row

src = Path(path)

if not src.exists():
    raise SystemExit(
        f"I/Q-Datei fehlt: {src}"
    )

print()
print("Treffer")
print("-------")
print(f"ID          : {peak_id}")
print(f"Band        : {band}")
print(f"Frequenz    : {rf_hz/1e6:.6f} MHz")
print(f"Center      : {center_hz/1e6:.6f} MHz")
print(f"I/Q         : {src}")
print()


# --------------------------------------------------
# I/Q laden
# --------------------------------------------------

data, fs = sf.read(
    src,
    always_2d=True
)

if data.shape[1] < 2:
    raise SystemExit(
        "Aufnahme ist nicht Stereo-I/Q."
    )

i = data[:, 0].astype(float)
q = data[:, 1].astype(float)

z = i + 1j*q

if CONJ:
    z = np.conj(z)

z -= np.mean(z)


# --------------------------------------------------
# Frequenzkorrektur
# --------------------------------------------------

corrected_center = (
    center_hz *
    (1.0 + PPM * 1e-6)
)

base_offset = (
    rf_hz -
    corrected_center
)

n = np.arange(len(z))


# --------------------------------------------------
# SSB demodulieren
# --------------------------------------------------

def demod(delta_hz, side):

    offset = (
        base_offset +
        delta_hz
    )

    bb = (
        z *
        np.exp(
            -2j *
            np.pi *
            offset *
            n /
            fs
        )
    )

    Z = np.fft.fft(bb)

    freq = np.fft.fftfreq(
        len(bb),
        1/fs
    )

    mask = np.zeros(
        len(Z),
        dtype=float
    )

    if side == "USB":

        # Sprachband oberhalb des virtuellen Trägers
        mask[
            (freq >= 180)
            &
            (freq <= 3300)
        ] = 1.0

    else:

        # Sprachband unterhalb des virtuellen Trägers
        mask[
            (freq <= -180)
            &
            (freq >= -3300)
        ] = 1.0

    filtered = np.fft.ifft(
        Z * mask
    )

    audio = (
        np.real(filtered) *
        2.0
    )

    audio -= np.mean(audio)

    # 48 kHz -> 12 kHz
    audio = resample_poly(
        audio,
        1,
        4
    )

    return audio


# --------------------------------------------------
# Sprachähnlichkeit bewerten
# --------------------------------------------------

def speech_score(audio, afs=12000):

    if len(audio) < 1000:
        return -999.0

    x = audio.copy()

    x -= np.mean(x)

    rms = np.sqrt(
        np.mean(x*x)
    )

    if rms < 1e-7:
        return -999.0

    N = len(x)

    X = np.abs(
        np.fft.rfft(
            x *
            np.hanning(N)
        )
    ) ** 2

    f = np.fft.rfftfreq(
        N,
        1/afs
    )

    total_mask = (
        (f >= 80)
        &
        (f <= 3500)
    )

    voice_mask = (
        (f >= 300)
        &
        (f <= 2700)
    )

    low_mask = (
        (f >= 80)
        &
        (f < 250)
    )

    upper_mask = (
        (f > 2800)
        &
        (f <= 3500)
    )

    total = (
        np.sum(X[total_mask])
        + 1e-20
    )

    voice = (
        np.sum(X[voice_mask])
        / total
    )

    low = (
        np.sum(X[low_mask])
        / total
    )

    upper = (
        np.sum(X[upper_mask])
        / total
    )

    vf = f[voice_mask]
    vp = X[voice_mask]

    if np.sum(vp) > 0:
        centroid = (
            np.sum(vf * vp)
            /
            np.sum(vp)
        )
    else:
        centroid = 9999

    # Sprache liegt typischerweise nicht direkt
    # am unteren oder oberen Filterrand.
    centroid_penalty = (
        abs(
            centroid - 1200.0
        )
        /
        1800.0
    )

    score = (
        100.0 * voice
        -
        100.0 * low
        -
        70.0 * upper
        -
        20.0 * centroid_penalty
    )

    return float(score)


# --------------------------------------------------
# BFO-Sweep
# --------------------------------------------------

results = []

for side in ("USB", "LSB"):

    for delta in range(
        -2200,
        2201,
        50
    ):

        audio = demod(
            delta,
            side
        )

        score = speech_score(
            audio
        )

        results.append(
            (
                score,
                side,
                delta,
                audio
            )
        )


# --------------------------------------------------
# jeweils bestes USB / LSB
# --------------------------------------------------

best_usb = max(
    (
        r for r in results
        if r[1] == "USB"
    ),
    key=lambda x: x[0]
)

best_lsb = max(
    (
        r for r in results
        if r[1] == "LSB"
    ),
    key=lambda x: x[0]
)

best = max(
    best_usb,
    best_lsb,
    key=lambda x: x[0]
)


def normalize(audio):

    peak = np.max(
        np.abs(audio)
    )

    if peak > 0:
        audio = (
            audio /
            peak *
            0.88
        )

    return audio.astype(
        np.float32
    )


stem = src.stem

usb_file = (
    REPORTS /
    f"{stem}_CAL_USB.wav"
)

lsb_file = (
    REPORTS /
    f"{stem}_CAL_LSB.wav"
)

best_file = (
    REPORTS /
    f"{stem}_CAL_BEST.wav"
)

sf.write(
    usb_file,
    normalize(best_usb[3]),
    12000,
    subtype="PCM_16"
)

sf.write(
    lsb_file,
    normalize(best_lsb[3]),
    12000,
    subtype="PCM_16"
)

sf.write(
    best_file,
    normalize(best[3]),
    12000,
    subtype="PCM_16"
)


print("Ergebnis")
print("--------")

print(
    f"USB : Offset {best_usb[2]:+d} Hz"
    f"   Score {best_usb[0]:.2f}"
)

print(
    f"LSB : Offset {best_lsb[2]:+d} Hz"
    f"   Score {best_lsb[0]:.2f}"
)

print()

print(
    f"BEST: {best[1]} "
    f"{best[2]:+d} Hz "
    f"(Score {best[0]:.2f})"
)

print()
print("Dateien:")
print(usb_file)
print(lsb_file)
print(best_file)
