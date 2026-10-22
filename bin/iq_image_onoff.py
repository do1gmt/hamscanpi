#!/usr/bin/env python3

import math
import os
import re
import stat
import subprocess
import time

import numpy as np


FIFO = "/tmp/PMSDRcommands"
RATE = 48000
SECONDS = 2.0
TX_HZ = 3_705_370.0

OFFSETS = [
    1670.0,
    2670.0,
    3670.0,
    4870.0,
    5970.0,
    7370.0,
]


def find_alsa(name="Aureon", device=0):
    r = subprocess.run(
        ["arecord", "-l"],
        text=True,
        capture_output=True
    )

    for line in r.stdout.splitlines():
        if name.lower() not in line.lower():
            continue

        m = re.search(
            r"card\s+(\d+):.*device\s+(\d+):",
            line,
            re.I
        )

        if m and int(m.group(2)) == device:
            return int(m.group(1)), int(m.group(2))

    raise SystemExit("Aureon nicht gefunden")


def open_fifo():
    if not os.path.exists(FIFO):
        raise SystemExit(f"FIFO fehlt: {FIFO}")

    if not stat.S_ISFIFO(os.stat(FIFO).st_mode):
        raise SystemExit(f"Kein FIFO: {FIFO}")

    return os.open(
        FIFO,
        os.O_WRONLY | os.O_NONBLOCK
    )


def tune(fd, hz):
    os.write(
        fd,
        f"f {int(round(hz))}\n".encode()
    )


def capture(card, dev):
    p = subprocess.Popen(
        [
            "arecord",
            "-q",
            "-D", f"plughw:{card},{dev}",
            "-f", "S16_LE",
            "-r", str(RATE),
            "-c", "2",
            "-t", "raw"
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    time.sleep(SECONDS)
    p.terminate()

    raw, _ = p.communicate(timeout=2)

    x = np.frombuffer(
        raw,
        dtype="<i2"
    ).astype(np.float64)

    x = x[:len(x)//2*2].reshape(-1, 2) / 32768.0

    i = x[:, 0]
    q = x[:, 1]

    i -= np.mean(i)
    q -= np.mean(q)

    return i, q


def spectrum(i, q, conjugate=True):
    if conjugate:
        z = i - 1j*q
    else:
        z = i + 1j*q

    n = len(z)
    w = np.hanning(n)

    s = np.fft.fftshift(
        np.fft.fft(z * w)
    )

    f = np.fft.fftshift(
        np.fft.fftfreq(
            n,
            1.0 / RATE
        )
    )

    p = np.abs(s) ** 2

    return f, p


def band_power(f, p, target, width=80.0):
    mask = np.abs(f - target) <= width

    if not np.any(mask):
        return 0.0

    return float(np.max(p[mask]))


def db10(x):
    return 10.0 * math.log10(max(x, 1e-30))


def main():
    card, dev = find_alsa()
    fd = open_fifo()

    print()
    print("PMSDR I/Q On-Off-Image-Test")
    print("---------------------------")
    print(
        f"IC-705: {TX_HZ/1e6:.6f} MHz FM"
    )
    print(
        "Für jeden Offset werden zwei Aufnahmen gemacht:"
    )
    print(
        "erst PTT AUS, dann PTT AN."
    )
    print()

    rows = []

    try:
        for off in OFFSETS:
            center = TX_HZ - off

            tune(fd, center)
            time.sleep(0.5)

            print()
            print(
                f"Offset {off:+.0f} Hz / "
                f"Center {center/1e6:.6f} MHz"
            )

            input("PTT AUS -> ENTER ... ")
            i0, q0 = capture(card, dev)

            input("PTT AN halten -> ENTER ... ")
            i1, q1 = capture(card, dev)

            print("PTT loslassen.")

            # Richtige Orientierung nach dem letzten eindeutigen Test:
            # physisch +Offset muss digital +Offset erscheinen.
            f0, p0 = spectrum(
                i0,
                q0,
                conjugate=True
            )

            f1, p1 = spectrum(
                i1,
                q1,
                conjugate=True
            )

            pos0 = band_power(
                f0,
                p0,
                +off
            )

            pos1 = band_power(
                f1,
                p1,
                +off
            )

            neg0 = band_power(
                f0,
                p0,
                -off
            )

            neg1 = band_power(
                f1,
                p1,
                -off
            )

            pos_excess = max(
                pos1 - pos0,
                0.0
            )

            neg_excess = max(
                neg1 - neg0,
                0.0
            )

            pos_change = db10(
                pos1 / max(pos0, 1e-30)
            )

            neg_change = db10(
                neg1 / max(neg0, 1e-30)
            )

            if pos_excess > 0 and neg_excess > 0:
                irr = db10(
                    pos_excess / neg_excess
                )
                irr_text = f"{irr:.1f} dB"
            elif pos_excess > 0:
                # Spiegel nicht über OFF-Hintergrund nachweisbar:
                # konservative Untergrenze gegen OFF-Level.
                irr_lb = db10(
                    pos_excess / max(neg0, 1e-30)
                )
                irr = None
                irr_text = f"> {irr_lb:.1f} dB"
            else:
                irr = None
                irr_text = "nicht messbar"

            rows.append(
                (
                    off,
                    pos_change,
                    neg_change,
                    irr_text
                )
            )

            print(
                f"  +Offset Änderung : {pos_change:+6.1f} dB"
            )
            print(
                f"  -Offset Änderung : {neg_change:+6.1f} dB"
            )
            print(
                f"  Image-Rejection  : {irr_text}"
            )

    finally:
        os.close(fd)

    print()
    print("Zusammenfassung")
    print("---------------")

    for off, pc, nc, irr in rows:
        print(
            f"{off:+7.0f} Hz  "
            f"+ {pc:+6.1f} dB  "
            f"- {nc:+6.1f} dB  "
            f"IRR {irr}"
        )

    print()
    print(
        "Komplette Ausgabe hierher kopieren."
    )


if __name__ == "__main__":
    main()
