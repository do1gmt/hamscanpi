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

    return i, q


def coeff(x, freq_hz):
    n = len(x)
    t = np.arange(n, dtype=np.float64) / RATE
    w = np.hanning(n)

    return np.sum(
        x * w * np.exp(-2j * np.pi * freq_hz * t)
    )


def wrap_deg(x):
    while x <= -180.0:
        x += 360.0
    while x > 180.0:
        x -= 360.0
    return x


def main():
    card, dev = find_alsa()
    fd = open_fifo()

    print()
    print("PMSDR I/Q-Phasendiagnose")
    print("-------------------------")
    print(
        f"IC-705 fest auf {TX_HZ/1e6:.6f} MHz FM"
    )
    print(
        "PTT während der gesamten Messreihe gedrückt halten."
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

            i, q = capture(card, dev)

            ci = coeff(i, off)
            cq = coeff(q, off)

            ai = abs(ci)
            aq = abs(cq)

            if ai <= 0 or aq <= 0:
                gain_db = float("nan")
                phase = float("nan")
            else:
                gain_db = 20.0 * math.log10(aq / ai)
                phase = wrap_deg(
                    math.degrees(
                        np.angle(cq) - np.angle(ci)
                    )
                )

            rows.append(
                (
                    off,
                    gain_db,
                    phase
                )
            )

    finally:
        os.close(fd)

    print()
    print("PTT loslassen.")
    print()
    print("Messwerte")
    print("---------")

    for off, gain_db, phase in rows:
        print(
            f"Offset {off:+7.0f} Hz  "
            f"Q/I {gain_db:+7.2f} dB  "
            f"Phase(Q-I) {phase:+8.2f} deg"
        )

    gains = np.array(
        [r[1] for r in rows],
        dtype=float
    )
    phases = np.array(
        [r[2] for r in rows],
        dtype=float
    )

    print()
    print("Median")
    print("------")
    print(
        f"Q/I Gain      : {np.nanmedian(gains):+.2f} dB"
    )
    print(
        f"Phase(Q-I)    : {np.nanmedian(phases):+.2f} deg"
    )
    print()
    print(
        "Für sauberes I/Q erwarten wir ungefähr 0 dB "
        "und +/-90 Grad."
    )
    print(
        "Komplette Ausgabe hierher kopieren."
    )


if __name__ == "__main__":
    main()
