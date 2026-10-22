#!/usr/bin/env python3

import os
import re
import stat
import subprocess
import time

import numpy as np


FIFO = "/tmp/PMSDRcommands"
RATE = 48000
SECONDS = 1.5

TX_HZ = 3_705_000.0

CENTERS = [
    3_698_000.0,
    3_700_000.0,
    3_702_000.0,
    3_704_000.0,
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
        f"f {int(hz)}\n".encode("ascii")
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


def spectrum(i, q):
    z = i + 1j*q
    n = len(z)

    w = np.hanning(n)

    s = np.fft.fftshift(
        np.fft.fft(z * w)
    )

    mag = np.abs(s)

    f = np.fft.fftshift(
        np.fft.fftfreq(
            n,
            1.0 / RATE
        )
    )

    db = 20.0 * np.log10(
        np.maximum(
            mag,
            1e-15
        )
    )

    return f, db


def local_peak(f, db, target, width=180.0):
    mask = np.abs(f - target) <= width

    if not np.any(mask):
        return None

    ii = np.where(mask)[0]
    k = ii[int(np.argmax(db[ii]))]

    return float(f[k]), float(db[k])


def noise_floor(f, db):
    mask = (
        (np.abs(f) > 500.0)
        & (np.abs(f) < 18_000.0)
    )

    vals = db[mask]

    if len(vals) == 0:
        return -999.0

    return float(
        np.median(vals)
    )


def top_peaks(f, db, limit=10):
    mask = (
        (np.abs(f) > 500.0)
        & (np.abs(f) < 18_000.0)
    )

    idx = np.where(mask)[0]

    order = idx[
        np.argsort(
            db[idx]
        )[::-1]
    ]

    out = []

    for k in order:
        freq = float(f[k])

        if any(
            abs(freq - x[0]) < 250.0
            for x in out
        ):
            continue

        out.append(
            (
                freq,
                float(db[k])
            )
        )

        if len(out) >= limit:
            break

    return out


def main():
    card, dev = find_alsa()
    fd = open_fifo()

    print()
    print("PMSDR Träger-Locator")
    print("--------------------")
    print(
        f"IC-705 fest auf {TX_HZ/1e6:.6f} MHz FM"
    )
    print(
        "PTT während des gesamten Tests gedrückt halten."
    )
    print()

    for n in (3, 2, 1):
        print(f"Start in {n} ...")
        time.sleep(1)

    try:
        for center in CENTERS:
            expected = TX_HZ - center

            tune(fd, center)
            time.sleep(0.6)

            i, q = capture(
                card,
                dev
            )

            f, db = spectrum(
                i,
                q
            )

            nf = noise_floor(
                f,
                db
            )

            pos = local_peak(
                f,
                db,
                +expected
            )

            neg = local_peak(
                f,
                db,
                -expected
            )

            print()
            print(
                f"Center          : {center/1e6:.6f} MHz"
            )
            print(
                f"Erwarteter Offset: {expected:+.0f} Hz"
            )
            print(
                f"Noise median    : {nf:.1f} dB"
            )

            if pos:
                print(
                    f"bei +Offset     : {pos[0]:+8.1f} Hz  "
                    f"{pos[1]:7.1f} dB  "
                    f"SNR {pos[1]-nf:5.1f} dB"
                )

            if neg:
                print(
                    f"bei -Offset     : {neg[0]:+8.1f} Hz  "
                    f"{neg[1]:7.1f} dB  "
                    f"SNR {neg[1]-nf:5.1f} dB"
                )

            print("Top Peaks:")
            for freq, level in top_peaks(
                f,
                db
            ):
                print(
                    f"  {freq:+8.1f} Hz  "
                    f"{level:7.1f} dB  "
                    f"SNR {level-nf:5.1f} dB"
                )

    finally:
        os.close(fd)

    print()
    print("PTT loslassen.")
    print()
    print(
        "Komplette Ausgabe hierher kopieren."
    )


if __name__ == "__main__":
    main()
