#!/usr/bin/env python3

import argparse
import math
import wave
from pathlib import Path

import numpy as np


BASE = Path(__file__).resolve().parent.parent
RECORDINGS = BASE / "recordings"


def newest_raw():
    files = sorted(
        RECORDINGS.glob("*.wav"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    # Keine bereits erzeugten USB/LSB/CAL-WAVs nehmen.
    files = [
        p for p in files
        if not any(
            tag in p.stem
            for tag in (
                "_USB",
                "_LSB",
                "_CAL_"
            )
        )
    ]

    if not files:
        raise SystemExit(
            "Keine RAW-I/Q-Aufnahme gefunden."
        )

    return files[0]


def read_iq(path):
    with wave.open(str(path), "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())

    if channels != 2:
        raise SystemExit(
            f"{path}: erwartet Stereo-I/Q, gefunden {channels} Kanäle"
        )

    if width != 2:
        raise SystemExit(
            f"{path}: erwartet 16 Bit PCM"
        )

    x = np.frombuffer(
        frames,
        dtype="<i2"
    ).astype(np.float64)

    if len(x) % 2:
        x = x[:-1]

    x = x.reshape(-1, 2) / 32768.0

    return x[:, 0], x[:, 1], rate


def quiet_samples(i, q, frame_len=2048, fraction=0.30):
    n = min(len(i), len(q))
    frames = n // frame_len

    if frames < 8:
        raise SystemExit(
            "Aufnahme zu kurz für robuste I/Q-Kalibrierung."
        )

    n2 = frames * frame_len

    fi = i[:n2].reshape(frames, frame_len)
    fq = q[:n2].reshape(frames, frame_len)

    power = np.mean(
        fi * fi + fq * fq,
        axis=1
    )

    take = max(
        6,
        int(round(frames * fraction))
    )

    idx = np.argsort(power)[:take]

    return (
        fi[idx].reshape(-1),
        fq[idx].reshape(-1),
        power,
        take,
        frames
    )


def stats(i, q):
    i = i - np.mean(i)
    q = q - np.mean(q)

    si = float(np.std(i))
    sq = float(np.std(q))

    if si < 1e-12 or sq < 1e-12:
        raise SystemExit(
            "I/Q-Pegel zu klein."
        )

    cov = float(
        np.mean(i * q)
    )

    rho = cov / (si * sq)

    rho = max(
        -0.999999,
        min(
            0.999999,
            rho
        )
    )

    # Gram-Schmidt:
    # Q enthält alpha * I. Diesen Anteil entfernen.
    alpha = cov / (si * si)

    q_orth = q - alpha * i
    sq_orth = float(
        np.std(q_orth)
    )

    if sq_orth < 1e-12:
        raise SystemExit(
            "Q-Orthogonalisierung fehlgeschlagen."
        )

    # Danach Q auf denselben RMS wie I bringen.
    q_gain = si / sq_orth

    # Bei ungefähr gleichem I/Q-Gain ist asin(rho)
    # eine brauchbare Darstellung des Quadraturfehlers.
    phase_deg = math.degrees(
        math.asin(rho)
    )

    gain_db = 20.0 * math.log10(
        sq / si
    )

    return {
        "rms_i": si,
        "rms_q": sq,
        "rho": rho,
        "phase_deg": phase_deg,
        "gain_db_q_vs_i": gain_db,
        "q_leak_i": alpha,
        "q_gain": q_gain,
    }


def corrected_stats(i, q, alpha, q_gain):
    i = i - np.mean(i)
    q = q - np.mean(q)

    qc = (
        q - alpha * i
    ) * q_gain

    si = float(np.std(i))
    sq = float(np.std(qc))
    rho = float(
        np.corrcoef(
            i,
            qc
        )[0, 1]
    )

    return si, sq, rho


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "wav",
        nargs="?",
        help=(
            "RAW Stereo-I/Q WAV. "
            "Ohne Angabe wird die neueste Aufnahme verwendet."
        )
    )

    ap.add_argument(
        "--quiet-fraction",
        type=float,
        default=0.30,
        help=(
            "Anteil der leisesten Frames für die Kalibrierung "
            "(Default 0.30)"
        )
    )

    args = ap.parse_args()

    if args.wav:
        path = Path(
            args.wav
        ).expanduser()
    else:
        path = newest_raw()

    if not path.exists():
        raise SystemExit(
            f"Datei fehlt: {path}"
        )

    i, q, rate = read_iq(
        path
    )

    # DC nur für Analyse entfernen.
    i0 = i - np.mean(i)
    q0 = q - np.mean(q)

    qi, qq, power, used, total = quiet_samples(
        i0,
        q0,
        fraction=max(
            0.10,
            min(
                0.70,
                args.quiet_fraction
            )
        )
    )

    all_s = stats(
        i0,
        q0
    )

    quiet_s = stats(
        qi,
        qq
    )

    ci, cq, crho = corrected_stats(
        qi,
        qq,
        quiet_s["q_leak_i"],
        quiet_s["q_gain"]
    )

    print()
    print("PMSDR I/Q-Kalibrierung")
    print("----------------------")
    print(f"Datei       : {path}")
    print(f"Samplerate  : {rate} Hz")
    print(f"Samples     : {len(i)}")
    print(
        f"Ruheframes  : {used}/{total} "
        f"({used/total*100:.1f} %)"
    )
    print()

    print("Gesamte Aufnahme")
    print("----------------")
    print(
        f"I RMS       : {all_s['rms_i']:.9f}"
    )
    print(
        f"Q RMS       : {all_s['rms_q']:.9f}"
    )
    print(
        f"Q/I Gain    : {all_s['gain_db_q_vs_i']:+.3f} dB"
    )
    print(
        f"Korrelation : {all_s['rho']:+.5f}"
    )
    print(
        f"Phase grob  : {all_s['phase_deg']:+.2f} deg"
    )
    print()

    print("Leise 30 %-Frames -> Kalibrierbasis")
    print("-----------------------------------")
    print(
        f"I RMS       : {quiet_s['rms_i']:.9f}"
    )
    print(
        f"Q RMS       : {quiet_s['rms_q']:.9f}"
    )
    print(
        f"Q/I Gain    : {quiet_s['gain_db_q_vs_i']:+.3f} dB"
    )
    print(
        f"Korrelation : {quiet_s['rho']:+.5f}"
    )
    print(
        f"Phase grob  : {quiet_s['phase_deg']:+.2f} deg"
    )
    print()

    print("Empfohlene Korrektur")
    print("--------------------")
    print(
        f"q_leak_i    : {quiet_s['q_leak_i']:+.8f}"
    )
    print(
        f"q_gain      : {quiet_s['q_gain']:.8f}"
    )
    print()

    print("Kontrolle nach Korrektur")
    print("------------------------")
    print(
        f"I RMS       : {ci:.9f}"
    )
    print(
        f"Q RMS       : {cq:.9f}"
    )
    print(
        f"Korrelation : {crho:+.6f}"
    )
    print()

    print(
        "WICHTIG: Noch nichts automatisch in scanner.json "
        "geschrieben."
    )
    print(
        "Erst die Messwerte prüfen; danach bauen wir die "
        "Korrektur kontrolliert ein."
    )


if __name__ == "__main__":
    main()
