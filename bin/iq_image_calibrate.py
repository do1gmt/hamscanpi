#!/usr/bin/env python3

import cmath
import json
import math
import os
import re
import stat
import subprocess
import time
from pathlib import Path

import numpy as np


BASE = Path(__file__).resolve().parent.parent
FIFO = "/tmp/PMSDRcommands"
RATE = 48000
SECONDS = 2.0

# Sender bleibt fest hier.
TX_HZ = 3_705_370.0

# Krumme Offsets, damit wir nicht auf der sichtbaren 1-kHz-Störkammstruktur sitzen.
OFFSETS = [
    1670.0,
    2670.0,
    3670.0,
    4870.0,
    5970.0,
    7370.0,
]

CALFILE = BASE / "config" / "iq_calibration.json"


def find_alsa(name="Aureon", device=0):
    r = subprocess.run(
        ["arecord", "-l"],
        text=True,
        capture_output=True
    )

    if r.returncode != 0:
        raise SystemExit("arecord -l fehlgeschlagen")

    for line in r.stdout.splitlines():
        if name.lower() not in line.lower():
            continue

        m = re.search(
            r"card\s+(\d+):.*device\s+(\d+):",
            line,
            re.IGNORECASE
        )

        if m:
            card = int(m.group(1))
            dev = int(m.group(2))

            if dev == device:
                return card, dev

    raise SystemExit(
        f"ALSA-Gerät '{name}', device {device} nicht gefunden"
    )


def open_fifo():
    if not os.path.exists(FIFO):
        raise SystemExit(f"FIFO fehlt: {FIFO}")

    if not stat.S_ISFIFO(os.stat(FIFO).st_mode):
        raise SystemExit(f"Kein FIFO: {FIFO}")

    try:
        return os.open(
            FIFO,
            os.O_WRONLY | os.O_NONBLOCK
        )
    except OSError as e:
        raise SystemExit(
            f"FIFO nicht beschreibbar: {e}"
        )


def tune(fd, hz):
    os.write(
        fd,
        f"f {int(round(hz))}\n".encode("ascii")
    )


def capture(card, dev):
    cmd = [
        "arecord",
        "-q",
        "-D", f"plughw:{card},{dev}",
        "-f", "S16_LE",
        "-r", str(RATE),
        "-c", "2",
        "-t", "raw"
    ]

    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    time.sleep(SECONDS)
    p.terminate()

    try:
        raw, err = p.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        p.kill()
        raw, err = p.communicate()

    if len(raw) < RATE:
        raise RuntimeError(
            f"Zu wenig Audiodaten: {len(raw)} Byte"
        )

    x = np.frombuffer(
        raw,
        dtype="<i2"
    ).astype(np.float64)

    if len(x) % 2:
        x = x[:-1]

    x = x.reshape(-1, 2) / 32768.0

    i = x[:, 0]
    q = x[:, 1]

    i -= np.mean(i)
    q -= np.mean(q)

    # Aus den bisherigen Messungen ist iq_conjugate=true die richtige Richtung.
    return i - 1j * q


def tone_coeff(z, freq_hz):
    n = len(z)
    t = np.arange(n, dtype=np.float64) / RATE

    # Hann-Fenster und komplexe Projektion direkt auf die Sollfrequenz.
    w = np.hanning(n)

    return np.sum(
        z * w * np.exp(-2j * np.pi * freq_hz * t)
    )


def db_ratio(a, b):
    a = abs(a)
    b = abs(b)

    if a <= 0 or b <= 0:
        return -999.0

    return 20.0 * math.log10(a / b)


def corrected_coeffs(A, B, c):
    # y = z - c * conj(z)
    # Positiver Ton:
    Ap = A - c * np.conj(B)
    # Spiegel:
    Bp = B - c * np.conj(A)

    return Ap, Bp


def main():
    card, dev = find_alsa()
    fd = open_fifo()

    print()
    print("PMSDR I/Q-Image-Kalibrierung")
    print("----------------------------")
    print(
        f"IC-705 fest auf {TX_HZ/1e6:.6f} MHz FM"
    )
    print(
        "PTT während der gesamten Messreihe gedrückt halten."
    )
    print()
    print(
        "Es werden mehrere krumme Basisband-Offsets gemessen."
    )
    print(
        "Aus Nutzsignal und Spiegel wird die komplexe "
        "I/Q-Korrektur bestimmt."
    )
    print()

    for n in (3, 2, 1):
        print(f"Start in {n} ...")
        time.sleep(1)

    rows = []

    try:
        for off in OFFSETS:
            center = TX_HZ - off

            tune(fd, center)
            time.sleep(0.6)

            z = capture(card, dev)

            A = tone_coeff(z, +off)
            B = tone_coeff(z, -off)

            rows.append({
                "offset": off,
                "center": center,
                "A": A,
                "B": B,
                "raw_irr_db": db_ratio(A, B),
            })

    finally:
        os.close(fd)

    print()
    print("PTT loslassen.")
    print()

    # B ≈ c * conj(A)
    num = 0j
    den = 0.0

    for r in rows:
        A = r["A"]
        B = r["B"]

        num += B * A
        den += abs(A) ** 2

    if den <= 0:
        raise SystemExit(
            "Kalibrierung fehlgeschlagen: kein Nutzsignal."
        )

    c = num / den

    print("Einzelmessungen")
    print("---------------")

    corrected = []

    for r in rows:
        Ap, Bp = corrected_coeffs(
            r["A"],
            r["B"],
            c
        )

        irr2 = db_ratio(Ap, Bp)
        corrected.append(irr2)

        print(
            f"Offset {r['offset']:+7.0f} Hz  "
            f"roh {r['raw_irr_db']:6.1f} dB  "
            f"mit Korrektur {irr2:6.1f} dB"
        )

    print()
    print("Globale komplexe Korrektur")
    print("--------------------------")
    print(
        f"c.real = {c.real:+.10f}"
    )
    print(
        f"c.imag = {c.imag:+.10f}"
    )
    print(
        f"|c|    = {abs(c):.10f}"
    )
    print(
        f"phase  = {math.degrees(cmath.phase(c)):+.3f} deg"
    )
    print()

    raw_vals = [
        r["raw_irr_db"]
        for r in rows
    ]

    print(
        f"IRR roh Median       : {np.median(raw_vals):.1f} dB"
    )
    print(
        f"IRR korrigiert Median: {np.median(corrected):.1f} dB"
    )
    print(
        f"IRR korrigiert min   : {np.min(corrected):.1f} dB"
    )
    print()

    result = {
        "iq_conjugate": True,
        "method": "widely_linear",
        "correction_real": float(c.real),
        "correction_imag": float(c.imag),
        "tx_hz": TX_HZ,
        "sample_rate": RATE,
        "offsets_hz": OFFSETS,
        "raw_irr_median_db": float(
            np.median(raw_vals)
        ),
        "corrected_irr_median_db": float(
            np.median(corrected)
        ),
        "corrected_irr_min_db": float(
            np.min(corrected)
        ),
    }

    CALFILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    CALFILE.write_text(
        json.dumps(
            result,
            indent=2
        )
        + "\n",
        encoding="utf-8"
    )

    print(
        f"Kalibrierwerte gespeichert: {CALFILE}"
    )
    print()
    print(
        "NOCH NICHT in scanner.py aktiviert."
    )
    print(
        "Komplette Ausgabe hierher kopieren."
    )


if __name__ == "__main__":
    main()
