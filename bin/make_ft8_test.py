#!/usr/bin/env python3

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "scanner.db"
CFG = BASE / "config" / "scanner.json"

if len(sys.argv) != 2:
    print("Aufruf: make_ft8_test.py RAW_IQ.wav")
    raise SystemExit(1)

src = Path(sys.argv[1]).resolve()

cfg = json.loads(
    CFG.read_text(encoding="utf-8")
)

ppm = float(
    cfg.get(
        "frequency_correction_ppm",
        0.0
    )
)

conjugate = bool(
    cfg.get(
        "iq_conjugate",
        False
    )
)

# 80-m-FT8 Dialfrequenz USB
DIAL = 3573000.0


# ----------------------------------------
# zugehörigen Scan / Center aus DB holen
# ----------------------------------------

db = sqlite3.connect(DB)

row = db.execute(
    """
    SELECT
        p.rf_hz,
        s.center_hz
    FROM peak p
    JOIN scan s
      ON s.id = p.scan_id
    WHERE p.recording_path = ?
    LIMIT 1
    """,
    (str(src),)
).fetchone()

db.close()

if not row:
    raise SystemExit(
        "Aufnahme nicht in Datenbank gefunden"
    )

rf_hz, center_hz = row

print(
    f"Treffer     : {rf_hz/1e6:.6f} MHz"
)
print(
    f"Scan-Center : {center_hz/1e6:.6f} MHz"
)
print(
    f"FT8 Dial    : {DIAL/1e6:.6f} MHz USB"
)
print(
    f"Erwarteter Audioton: "
    f"{rf_hz-DIAL:.0f} Hz"
)


# ----------------------------------------
# RAW I/Q laden
# ----------------------------------------

data, fs = sf.read(
    src,
    always_2d=True
)

if data.shape[1] != 2:
    raise SystemExit(
        "Keine Stereo-I/Q-Datei"
    )

i = data[:, 0]
q = data[:, 1]

z = i + 1j*q

if conjugate:
    z = np.conj(z)

z -= np.mean(z)


# ----------------------------------------
# tatsächlichen PMSDR-Center korrigieren
# ----------------------------------------

corrected_center = (
    center_hz *
    (1.0 + ppm * 1e-6)
)

# Wir setzen NICHT den Treffer auf Null,
# sondern die FT8-Dialfrequenz.
shift = (
    DIAL -
    corrected_center
)

n = np.arange(
    len(z)
)

bb = (
    z *
    np.exp(
        -2j *
        np.pi *
        shift *
        n /
        fs
    )
)

# Beim analytischen I/Q ergibt der Realteil
# das USB-Audiospektrum oberhalb der Dialfrequenz.
audio = np.real(bb)

audio -= np.mean(audio)


# ----------------------------------------
# 48 kHz -> 12 kHz
# ----------------------------------------

audio = resample_poly(
    audio,
    1,
    4
)

# Decoder braucht keine brutale Normalisierung,
# aber vernünftigen Pegel.
peak = np.max(
    np.abs(audio)
)

if peak > 0:
    audio = (
        audio /
        peak *
        0.80
    )


# ft8_lib maximal 15 Sekunden.
# 14.8 s lassen etwas Reserve.
audio = audio[
    :int(14.8 * 12000)
]


out = (
    Path.home() /
    "ft8_test.wav"
)

sf.write(
    out,
    audio,
    12000,
    subtype="PCM_16"
)

print()
print("Erzeugt:")
print(out)
