#!/usr/bin/env python3

import os
import re
import stat
import subprocess
import time

import numpy as np


FIFO = "/tmp/PMSDRcommands"
RATE = 48000
SECONDS = 2.0

# Absichtlich NICHT auf glatten kHz-Werten:
# so vermeiden wir die bei dir sichtbare 1-kHz-Störkammstruktur.
TX_HZ = 3_705_370.0

CENTERS = [
    3_698_000.0,   # +7370 Hz
    3_700_500.0,   # +4870 Hz
    3_702_700.0,   # +2670 Hz
]


def find_alsa(name="Aureon", device=0):
    r = subprocess.run(
        ["env", "LC_ALL=C", "arecord", "-l"],
        text=True,
        capture_output=True
    )

    if r.returncode != 0:
        raise SystemExit("arecord -l fehlgeschlagen")

    for line in r.stdout.splitlines():
        if name.lower() not in line.lower():
            continue

        m = re.search(
            r"(?:card|karte)\s+(\d+):.*(?:device|gerät)\s+(\d+):",
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

    return i, q


def tone_level(i, q, target_hz, conjugate=False):
    if conjugate:
        z = i - 1j*q
    else:
        z = i + 1j*q

    n = len(z)
    w = np.hanning(n)

    spec = np.fft.fftshift(
        np.fft.fft(z * w)
    )

    f = np.fft.fftshift(
        np.fft.fftfreq(
            n,
            1.0 / RATE
        )
    )

    mag = np.abs(spec)

    mask = np.abs(f - target_hz) <= 60.0
    if not np.any(mask):
        return None

    ii = np.where(mask)[0]
    k = ii[int(np.argmax(mag[ii]))]

    return float(f[k]), float(mag[k])


def db_ratio(a, b):
    if a <= 0 or b <= 0:
        return -999.0
    return 20.0 * np.log10(a / b)


def main():
    card, dev = find_alsa()
    fd = open_fifo()

    print()
    print("PMSDR sauberer Trägertest")
    print("-------------------------")
    print(
        f"IC-705 auf {TX_HZ/1e6:.6f} MHz FM stellen."
    )
    print(
        "PTT während des gesamten Tests gedrückt halten."
    )
    print()
    print(
        "Die Frequenz ist absichtlich nicht glatt gewählt, "
        "damit die 1-kHz-Störlinien nicht auf dem Träger liegen."
    )
    print()

    for n in (3, 2, 1):
        print(f"Start in {n} ...")
        time.sleep(1)

    rows = []

    try:
        for center in CENTERS:
            expected = TX_HZ - center

            tune(fd, center)
            time.sleep(0.6)

            i, q = capture(card, dev)

            row = {
                "center": center,
                "expected": expected,
            }

            for conj in (False, True):
                p = tone_level(
                    i,
                    q,
                    +expected,
                    conjugate=conj
                )
                m = tone_level(
                    i,
                    q,
                    -expected,
                    conjugate=conj
                )

                key = "true" if conj else "false"

                if p is None or m is None:
                    row[key] = None
                else:
                    row[key] = {
                        "plus_freq": p[0],
                        "plus_amp": p[1],
                        "minus_freq": m[0],
                        "minus_amp": m[1],
                        "plus_over_minus_db":
                            db_ratio(p[1], m[1]),
                        "minus_over_plus_db":
                            db_ratio(m[1], p[1]),
                    }

            rows.append(row)

    finally:
        os.close(fd)

    print()
    print("PTT loslassen.")
    print()

    for row in rows:
        print(
            f"Center {row['center']/1e6:.6f} MHz "
            f"(Soll-Offset {row['expected']:+.0f} Hz)"
        )

        for key in ("false", "true"):
            r = row[key]

            print(
                f"  iq_conjugate={key}"
            )

            if r is None:
                print("    Messung fehlgeschlagen")
                continue

            print(
                f"    +Offset: {r['plus_freq']:+8.1f} Hz"
            )
            print(
                f"    -Offset: {r['minus_freq']:+8.1f} Hz"
            )
            print(
                f"    + / -  : "
                f"{r['plus_over_minus_db']:+6.1f} dB"
            )
            print(
                f"    - / +  : "
                f"{r['minus_over_plus_db']:+6.1f} dB"
            )

        print()

    print(
        "Komplette Ausgabe hierher kopieren."
    )


if __name__ == "__main__":
    main()
