#!/usr/bin/env python3

import json
import sqlite3
from pathlib import Path

import numpy as np
import soundfile as sf

from scipy.signal import (
    butter,
    sosfiltfilt,
    resample_poly
)

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "scanner.db"
CFG = BASE / "config" / "scanner.json"
REPORTS = BASE / "reports"

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


def load_iq(path):

    data, fs = sf.read(
        path,
        always_2d=True
    )

    if data.shape[1] < 2:
        raise RuntimeError(
            "Keine Stereo-I/Q-Aufnahme"
        )

    i = data[:, 0]
    q = data[:, 1]

    z = i + 1j * q

    if CONJ:
        z = np.conj(z)

    z -= np.mean(z)

    return z, fs


def shift_signal(
    z,
    fs,
    center_hz,
    rf_hz
):

    corrected_center = (
        center_hz *
        (1.0 + PPM * 1e-6)
    )

    offset = (
        rf_hz -
        corrected_center
    )

    n = np.arange(
        len(z)
    )

    return (
        z *
        np.exp(
            -2j *
            np.pi *
            offset *
            n /
            fs
        )
    )


def demod_fm(
    z,
    fs
):

    # FM-Diskriminator:
    # Phasendifferenz zwischen zwei Samples
    audio = np.angle(
        z[1:] *
        np.conj(z[:-1])
    )

    # DC entfernen
    audio -= np.mean(audio)

    # Sprachband etwa 300–3000 Hz
    sos = butter(
        5,
        [250, 3200],
        btype="bandpass",
        fs=fs,
        output="sos"
    )

    try:
        audio = sosfiltfilt(
            sos,
            audio
        )
    except Exception:
        pass

    # auf 12 kHz herunter
    target_fs = 12000

    audio = resample_poly(
        audio,
        target_fs,
        fs
    )

    # Pegel normalisieren
    peak = np.max(
        np.abs(audio)
    )

    if peak > 0:
        audio = (
            audio /
            peak *
            0.82
        )

    return (
        audio.astype(
            np.float32
        ),
        target_fs
    )


db = sqlite3.connect(DB)

rows = db.execute(
    """
    SELECT
        p.id,
        p.rf_hz,
        p.recording_path,
        COALESCE(p.rec_center_hz, s.center_hz),
        s.band
    FROM peak p
    JOIN scan s
      ON s.id = p.scan_id
    WHERE p.recording_path IS NOT NULL
      AND (
            COALESCE(p.mode, '') IN ('fm', 'sat', 'data')
         OR (COALESCE(p.mode, '') = '' AND s.band IN ('10m', '11m'))
      )
    ORDER BY p.id
    """
).fetchall()

db.close()

count = 0

for (
    peak_id,
    rf_hz,
    recording_path,
    center_hz,
    band
) in rows:

    src = Path(
        recording_path
    )

    if not src.exists():
        continue

    out = (
        REPORTS /
        f"{src.stem}_FM.wav"
    )

    if out.exists():
        continue

    try:

        z, fs = load_iq(src)

        z = shift_signal(
            z,
            fs,
            center_hz,
            rf_hz
        )

        audio, afs = demod_fm(
            z,
            fs
        )

        sf.write(
            out,
            audio,
            afs,
            subtype="PCM_16"
        )

        print(
            f"{band:4s} "
            f"{rf_hz/1e6:.6f} MHz "
            f"-> {out.name}"
        )

        count += 1

    except Exception as e:

        print(
            f"FM Fehler Peak {peak_id}: {e}"
        )

print()
print(
    "Neue FM-Dateien:",
    count
)
