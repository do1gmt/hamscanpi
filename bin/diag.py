#!/usr/bin/env python3
"""Diagnose-Paket: alles, was zur Fehlersuche nach einem Hänger hilft, in einer tar.gz-Datei – ohne
Zugangsdaten (Mail-Passwort, Tokens, Zugangsschutz werden weggelassen).

  Protokoll (log/app.jsonl, letzte 5 MB), Satelliten-Protokoll, Kernel- und Dienst-Journal der letzten 2 Tage,
  Systemverlauf (sysmon.db), Hinweise, Zustand der Prüfungen, Einstellungen ohne Geheimnisse, Version,
  Speicher/Platten/Prozesse in diesem Moment.
Nur Standardbibliothek – läuft auch aus der Notfall-Seite, wenn der Webserver hängt.
   python3 bin/diag.py [datei.tar.gz]
"""
import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "bin"))


def _run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr.strip() else "")
    except Exception as e:
        return f"{cmd}: {type(e).__name__}: {e}\n"


def _tail(path, max_bytes):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            n = f.tell()
            f.seek(max(0, n - max_bytes))
            return f.read()
    except OSError:
        return b""


def filename():
    return f"hamscanpi-diagnose-{time.strftime('%Y%m%d-%H%M')}.tar.gz"


def write(fileobj, stream=True):
    def add(name, data):
        if isinstance(data, str):
            data = data.encode("utf-8", errors="replace")
        ti = tarfile.TarInfo(name)
        ti.size, ti.mtime = len(data), int(time.time())
        tar.addfile(ti, io.BytesIO(data))

    with tarfile.open(fileobj=fileobj, mode="w|gz" if stream else "w:gz") as tar:
        try:
            ver = (BASE / "VERSION").read_text().strip()
        except OSError:
            ver = "?"
        add("info.txt", f"HamScanPi {ver}\nerstellt {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n"
            + _run(["uptime"]) + "\n" + _run(["free", "-m"]) + "\n" + _run(["df", "-h", str(BASE)])
            + "\n" + _run(["swapon", "--show"]) + "\n" + _run(["vcgencmd", "get_throttled"])
            + "\n" + _run(["vcgencmd", "measure_temp"]) + "\n" + _run(["ps", "aux", "--sort=-rss"])[:20000])
        add("log/app.jsonl", _tail(BASE / "log" / "app.jsonl", 5_000_000))
        add("log/satellite.log", _tail(BASE / "log" / "satellite.log", 1_000_000))
        add("journal/kernel.txt", _run(["journalctl", "-k", "--since", "-2d", "--no-pager", "-o", "short-iso"], 60)[-3_000_000:])
        for u in ("hamscanpi-web", "hamscanpi-scheduler", "hamscanpi-rescue"):
            add(f"journal/{u}.txt", _run(["journalctl", "-u", u, "--since", "-2d", "--no-pager", "-o", "short-iso"], 60)[-2_000_000:])
        add("systemd/status.txt", _run(["systemctl", "status", "--no-pager", "-l", "hamscanpi-web", "hamscanpi-scheduler",
                                         "hamscanpi-rescue"]))
        for name in ("notices.json", "health_state.json", "live_state.json", "sticks.json", "pskreporter_state.json",
                     "runtime_status.json", "sat_stats.json"):
            p = BASE / "data" / name
            if p.exists():
                add(f"data/{name}", p.read_bytes())
        src = BASE / "data" / "sysmon.db"
        if src.exists():                                   # Systemverlauf stimmig kopieren
            with tempfile.TemporaryDirectory() as td:
                dst = Path(td) / "sysmon.db"
                a = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=20)
                b = sqlite3.connect(dst)
                try:
                    a.backup(b)
                finally:
                    b.close()
                    a.close()
                add("data/sysmon.db", dst.read_bytes())
        try:                                               # Einstellungen ohne Zugangsdaten
            import backup as BK
            for arc, p in BK._config_files(False):
                add(arc, p if isinstance(p, (bytes, bytearray)) else Path(p).read_bytes())
        except Exception as e:
            add("config/FEHLER.txt", f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else filename())
    with open(out, "wb") as f:
        write(f, stream=False)
    print(f"{out} ({out.stat().st_size / 1e6:.1f} MB)")
