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
CENTERS = [
    3_698_000,
    3_700_000,
    3_702_000,
    3_704_000,
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


def strongest_peak(i, q):
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

    mag = np.abs(s)

    # DC und extreme Randbereiche ausblenden.
    mask = (
        (np.abs(f) > 300.0)
        & (np.abs(f) < 20_000.0)
    )

    idxs = np.where(mask)[0]
    idx = idxs[
        int(np.argmax(mag[idxs]))
    ]

    return float(f[idx]), float(mag[idx])


def main():
    card, dev = find_alsa()
    fd = open_fifo()

    print()
    print("PMSDR Mittenfrequenz-Sweep")
    print("--------------------------")
    print("IC-705: 3.705000 MHz FM")
    print("PTT ab jetzt gedrückt halten.")
    print()

    for n in (3, 2, 1):
        print(f"Start in {n} ...")
        time.sleep(1)

    rows = []

    try:
        for center in CENTERS:
            tune(fd, center)
            time.sleep(0.6)

            i, q = capture(
                card,
                dev
            )

            peak_hz, amp = strongest_peak(
                i,
                q
            )

            rows.append(
                (
                    center,
                    peak_hz,
                    center + peak_hz
                )
            )

            print(
                f"Center {center/1e6:.6f} MHz"
                f" -> Peak {peak_hz:+8.1f} Hz"
                f" -> naive RF {(center+peak_hz)/1e6:.6f} MHz"
            )

    finally:
        os.close(fd)

    print()
    print("PTT loslassen.")
    print()
    print("Ausgabe komplett hierher kopieren.")
    print(
        "Damit bestimmen wir zuerst die reale "
        "Frequenzabbildung des PMSDR/FIFO."
    )


if __name__ == "__main__":
    main()
