#!/usr/bin/env python3
"""Wer hält eine Sperrdatei offen? (cycle.lock, scan.lock) – über /proc/*/fd, ohne Zusatzprogramme.
Für Meldungen „Scan nicht gestartet: … gehalten von PID 1234 (python3 bin/sat_pass.py …)“ und das Diagnose-Paket.

    python3 bin/lockinfo.py            # beide Sperren
"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LOCKS = {"cycle": BASE / "data" / "cycle.lock", "scan": BASE / "data" / "scan.lock"}


def holders(path):
    """Prozesse, die die Datei offen haben: [(pid, Befehlszeile)]. Ohne Rechte auf fremde Prozesse: nur eigene."""
    target = str(Path(path).resolve())
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        fd_dir = f"/proc/{pid}/fd"
        try:
            for fd in os.listdir(fd_dir):
                try:
                    if os.readlink(f"{fd_dir}/{fd}") == target:
                        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
                        out.append((int(pid), cmd[:160]))
                        break
                except OSError:
                    continue
        except OSError:
            continue
    return out


def describe(path):
    h = holders(path)
    return ", ".join(f"PID {p} ({c})" for p, c in h) if h else "unbekannt (kein Prozess mit offener Datei gefunden)"


if __name__ == "__main__":
    for name, p in LOCKS.items():
        print(f"{name}.lock: {describe(p)}")
