#!/usr/bin/env python3

import argparse
import datetime
import os
import re
import stat
import subprocess
import time
import wave
from pathlib import Path

import numpy as np


BASE = Path(__file__).resolve().parent.parent
OUTDIR = BASE / "recordings"


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


def open_fifo(path):
    if not os.path.exists(path):
        raise SystemExit(f"FIFO fehlt: {path}")

    if not stat.S_ISFIFO(
        os.stat(path).st_mode
    ):
        raise SystemExit(f"Kein FIFO: {path}")

    try:
        return os.open(
            path,
            os.O_WRONLY | os.O_NONBLOCK
        )
    except OSError as e:
        raise SystemExit(
            f"FIFO nicht beschreibbar: {e}"
        )


def tune(fd, frequency):
    cmd = f"f {int(frequency)}\n".encode("ascii")
    os.write(fd, cmd)


def capture(card, dev, rate, seconds, path):
    cmd = [
        "arecord",
        "-q",
        "-D", f"plughw:{card},{dev}",
        "-f", "S16_LE",
        "-r", str(rate),
        "-c", "2",
        "-d", str(int(round(seconds))),
        str(path)
    ]

    r = subprocess.run(
        cmd,
        text=True,
        capture_output=True
    )

    if r.returncode != 0:
        raise SystemExit(
            "Aufnahme fehlgeschlagen: "
            + r.stderr.strip()
        )


def analyse(path, center_hz):
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        data = w.readframes(
            w.getnframes()
        )

    if channels != 2 or width != 2:
        raise SystemExit(
            "Erwartet 16-Bit Stereo-I/Q."
        )

    x = np.frombuffer(
        data,
        dtype="<i2"
    ).astype(np.float64)

    if len(x) % 2:
        x = x[:-1]

    x = x.reshape(-1, 2) / 32768.0

    i = x[:, 0]
    q = x[:, 1]

    i -= np.mean(i)
    q -= np.mean(q)

    n = len(i)

    if n < 8192:
        raise SystemExit(
            "Zu wenig Samples."
        )

    window = np.hanning(n)

    results = []

    for conjugate in (False, True):
        if conjugate:
            z = i - 1j*q
        else:
            z = i + 1j*q

        spec = np.fft.fftshift(
            np.fft.fft(
                z * window
            )
        )

        mag = np.abs(spec)
        freqs = np.fft.fftshift(
            np.fft.fftfreq(
                n,
                1.0 / rate
            )
        )

        # DC-Zone ausblenden
        mask = np.abs(freqs) > 500.0

        mag2 = np.where(
            mask,
            mag,
            0.0
        )

        idx = int(np.argmax(mag2))
        f_peak = float(freqs[idx])
        a_peak = float(mag[idx])

        # Spiegel exakt gegenüber suchen, +/- 100 Hz Fenster
        mirror_target = -f_peak
        mirror_mask = (
            np.abs(
                freqs - mirror_target
            ) <= 100.0
        )

        if np.any(mirror_mask):
            a_mirror = float(
                np.max(
                    mag[mirror_mask]
                )
            )
        else:
            a_mirror = 0.0

        if a_mirror > 0:
            irr_db = 20.0 * np.log10(
                a_peak / a_mirror
            )
        else:
            irr_db = 120.0

        results.append({
            "conjugate": conjugate,
            "peak_hz": f_peak,
            "rf_hz": center_hz + f_peak,
            "irr_db": irr_db,
            "peak_amp": a_peak,
            "mirror_amp": a_mirror
        })

    return results


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--center",
        type=float,
        default=3_700_000.0
    )

    ap.add_argument(
        "--seconds",
        type=float,
        default=5.0
    )

    ap.add_argument(
        "--alsa-name",
        default="Aureon"
    )

    ap.add_argument(
        "--alsa-device",
        type=int,
        default=0
    )

    ap.add_argument(
        "--fifo",
        default="/tmp/PMSDRcommands"
    )

    args = ap.parse_args()

    card, dev = find_alsa(
        args.alsa_name,
        args.alsa_device
    )

    fifo_fd = open_fifo(
        args.fifo
    )

    try:
        tune(
            fifo_fd,
            args.center
        )

        time.sleep(
            0.5
        )

        now = datetime.datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )

        path = (
            OUTDIR
            / f"{now}_IQCAL_{int(args.center)}Hz.wav"
        )

        print()
        print("PMSDR I/Q-Trägertest")
        print("--------------------")
        print(
            f"PMSDR Mitte : {args.center/1e6:.6f} MHz"
        )
        print(
            "Sender      : 3.705000 MHz empfohlen"
        )
        print(
            "Jetzt PTT drücken und während der Aufnahme halten."
        )
        print()

        for n in (3, 2, 1):
            print(
                f"Start in {n} ..."
            )
            time.sleep(
                1.0
            )

        print(
            f"Aufnahme {args.seconds:.0f} s ..."
        )

        capture(
            card,
            dev,
            48000,
            args.seconds,
            path
        )

        print(
            "Aufnahme beendet. PTT loslassen."
        )
        print()

        results = analyse(
            path,
            args.center
        )

        for r in results:
            label = (
                "iq_conjugate=true"
                if r["conjugate"]
                else "iq_conjugate=false"
            )

            print(label)
            print(
                f"  stärkster Peak : {r['peak_hz']:+.1f} Hz"
            )
            print(
                f"  RF geschätzt   : {r['rf_hz']/1e6:.6f} MHz"
            )
            print(
                f"  Spiegelabstand : {r['irr_db']:.1f} dB"
            )
            print()

        best = max(
            results,
            key=lambda x: x["irr_db"]
        )

        print("BESTE I/Q-RICHTUNG")
        print("------------------")
        print(
            "iq_conjugate = "
            + (
                "true"
                if best["conjugate"]
                else "false"
            )
        )
        print(
            f"Image-Rejection = {best['irr_db']:.1f} dB"
        )
        print(
            f"Datei = {path}"
        )

    finally:
        os.close(
            fifo_fd
        )


if __name__ == "__main__":
    main()
