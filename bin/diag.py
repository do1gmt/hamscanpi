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
import re
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
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


def _proc_writes():
    """{pid: (Name, geschriebene Bytes)} – lesbar sind nur die eigenen Prozesse (ohne root)."""
    out = {}
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            wb = int(next(x for x in (d / "io").read_text().splitlines() if x.startswith("write_bytes")).split()[1])
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").replace(b"\n", b" ").decode(errors="replace").strip()[:90]
            out[d.name] = (cmd or d.name, wb)
        except Exception:
            continue
    return out


def io_sample(secs=10):
    """Wer schreibt auf die Speicherkarte? Gesamt (diskstats) und je eigenem Prozess über secs Sekunden,
    dazu wo /tmp und /dev/shm liegen (RAM oder Karte) und die Einhängeoptionen der Systempartition."""
    import sysmon as SM
    disk = SM.root_disk()
    w0, p0 = SM.disk_written_mb(disk) if disk else None, _proc_writes()
    time.sleep(secs)
    w1, p1 = SM.disk_written_mb(disk) if disk else None, _proc_writes()
    lines = [f"Systemlaufwerk {disk}: in {secs} s {((w1 or 0) - (w0 or 0)):.2f} MB geschrieben"
             f" (≈ {((w1 or 0) - (w0 or 0)) * 8640 / secs / 1024:.1f} GB/Tag bei gleicher Last)", "",
             "Eigene Prozesse (Bytes in dieser Zeit · insgesamt seit Start):"]
    rows = sorted(((p1[k][1] - p0.get(k, (0, 0))[1], p1[k][1], p1[k][0]) for k in p1), reverse=True)
    for dlt, tot, cmd in rows[:15]:
        lines.append(f"{dlt / 1024:10.0f} KB  {tot / 1048576:9.1f} MB  {cmd}")
    tmp = (_run(["findmnt", "-n", "-o", "FSTYPE,SIZE", "--target", "/tmp"]).strip().splitlines() or [""])[0]
    lines += ["", "Systempartition: " + _run(["findmnt", "-n", "-o", "FSTYPE,OPTIONS", "/"]).strip(),
              "/tmp: " + (tmp or "?") + (" (Arbeitsspeicher)" if tmp.startswith("tmpfs") else " (Speicherkarte)"),
              "/dev/shm: " + (_run(["findmnt", "-n", "-o", "FSTYPE,SIZE", "--target", "/dev/shm"]).strip().splitlines() or ["?"])[0],
              "dirty_writeback_centisecs: " + _run(["cat", "/proc/sys/vm/dirty_writeback_centisecs"]).strip()]
    return "\n".join(lines) + "\n"


def git_info():
    """Stand der Installation und Herkunft der Updates – Zugangsdaten in der Adresse werden entfernt."""
    g = ["git", "-C", str(BASE)]
    url = re.sub(r"//[^/@\s]+@", "//", _run(g + ["remote", "get-url", "origin"]).strip())
    return "\n".join([f"Ursprung: {url or '?'}",
                      "Zweig: " + _run(g + ["rev-parse", "--abbrev-ref", "HEAD"]).strip(),
                      "Stand: " + _run(g + ["log", "-1", "--format=%h %cI %s"]).strip(),
                      "Geänderte Dateien:", _run(g + ["status", "--short"])[:4000]]) + "\n"


def pmsdr_info(freqs=(3_573_000, 7_074_000, 14_074_000), secs=1.5):
    """Kurzwelle mit der PMSDR: Steuerprogramm, Soundkarte, Mischpult und ein kurzer Empfangstest auf den FT8-
    Frequenzen (Pegel, Übersteuerung, I/Q-Gleichlauf, stärkste Signale). Nur wenn die PMSDR frei ist."""
    out = []
    try:
        import devices as DEV
        cfg = json.loads((BASE / "config" / "scanner.json").read_text(encoding="utf-8"))
    except Exception as e:
        return f"Einstellungen nicht lesbar: {e}\n"
    if not cfg.get("alsa_name"):
        return "keine PMSDR eingerichtet (scanner.json ohne alsa_name)\n"
    out += ["== Steuerprogramm", _run(["systemctl", "status", "--no-pager", "-l", "pmsdr-control"])[:3000],
            _run(["journalctl", "-u", "pmsdr-control", "-n", "60", "--no-pager", "-o", "short-iso"])[-6000:],
            "== USB", _run(["lsusb"]), "== Soundkarten", _run(["arecord", "-l"])]
    try:
        card, dev = DEV.PmsdrDevice._find_alsa(cfg["alsa_name"], int(cfg.get("alsa_device", 0)))
        out += [f"== Mischpult Karte {card}", _run(["amixer", "-c", str(card), "contents"])[:6000]]
    except Exception as e:
        out.append(f"Soundkarte: {e}")
        return "\n".join(out) + "\n"
    try:
        was = DEV.capture_input(card, cfg.get("alsa_capture_source", "Line"))
        if was:
            out.append(f"== Aufnahme-Eingang stand auf „{was}“ – auf Line umgestellt (die PMSDR liefert Line-Pegel)")
    except Exception:
        pass
    # Empfangstest – nicht, während Scan, Überflug oder Live die PMSDR benutzen
    import fcntl
    live = BASE / "data" / "live.lock"
    if live.exists() and time.time() - live.stat().st_mtime < 120:
        out.append("== Empfangstest: übersprungen – Live läuft")
        return "\n".join(out) + "\n"
    lk = open(BASE / "data" / "scan.lock", "a")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lk.close()
        out.append("== Empfangstest: übersprungen – Scan oder Überflug läuft")
        return "\n".join(out) + "\n"
    try:
        import numpy as np
        d = DEV.PmsdrDevice(cfg)
        out.append(f"== Empfangstest ({secs} s je Frequenz, {d.rate} Hz, I/Q {'getauscht' if cfg.get('iq_conjugate') else 'normal'})")
        for f in freqs:
            try:
                c = f - 10000                                  # FT8-Bereich 10 kHz neben der Mitte (Gleichspannung)
                d.tune(c)
                i, q = d.capture(secs)
                raw_peak = max(np.max(np.abs(i)), np.max(np.abs(q)))
                rms_i, rms_q = float(np.sqrt(np.mean(i * i))), float(np.sqrt(np.mean(q * q)))
                corr = float(np.corrcoef(i, q)[0, 1]) if rms_i > 0 and rms_q > 0 else float("nan")
                z = i + (-1j if cfg.get("iq_conjugate") else 1j) * q
                n = 8192
                win = np.hanning(n)
                spec = np.zeros(n)
                for k in range(0, len(z) - n, n // 2):
                    spec += np.abs(np.fft.fftshift(np.fft.fft(z[k:k + n] * win))) ** 2
                db = 10 * np.log10(spec + 1e-20)
                fr = np.fft.fftshift(np.fft.fftfreq(n, 1 / d.rate))
                floor = float(np.median(db))
                top = sorted(((db[k] - floor, fr[k]) for k in range(1, n - 1)
                              if db[k] >= db[k - 1] and db[k] >= db[k + 1] and abs(fr[k]) > 300), reverse=True)[:5]
                db_fs = lambda x: 20 * np.log10(x + 1e-12)
                out.append(f"{f / 1e6:.3f} MHz: Pegel I {db_fs(rms_i):.1f} dBFS, Q {db_fs(rms_q):.1f} dBFS, Spitze {db_fs(raw_peak):.1f} dBFS"
                           f"{' – ÜBERSTEUERT' if raw_peak > 0.95 else ''}{' – fast kein Signal von der Soundkarte' if max(rms_i, rms_q) < 1e-4 else ''}; "
                           f"I/Q-Verhältnis {db_fs(rms_i) - db_fs(rms_q):+.1f} dB, Korrelation {corr:+.2f}; "
                           "stärkste Signale: " + ", ".join(f"{(c + o) / 1e6:.4f} MHz +{s:.0f} dB" for s, o in top))
            except Exception as e:
                out.append(f"{f / 1e6:.3f} MHz: {type(e).__name__}: {e}")
        try:
            os.close(d.fifo_fd)
        except Exception:
            pass
    except Exception as e:
        out.append(f"Empfangstest nicht möglich: {type(e).__name__}: {e}")
    finally:
        try:
            fcntl.flock(lk, fcntl.LOCK_UN)
        finally:
            lk.close()
    return "\n".join(out) + "\n"


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
            + "\n" + _run(["vcgencmd", "measure_temp"]) + "\n" + _run(["python3", str(BASE / "bin" / "lockinfo.py")]) + "\n" + _run(["journalctl", "--list-boots", "--no-pager"])[-3000:]
            + "\n" + _run(["ps", "aux", "--sort=-rss"])[:20000])
        add("io.txt", io_sample())                                                    # Schreiblast
        add("log/app.jsonl", _tail(BASE / "log" / "app.jsonl", 5_000_000))
        add("log/satellite.log", _tail(BASE / "log" / "satellite.log", 1_000_000))
        add("log/scan-cycle.log", _tail(BASE / "log" / "scan-cycle.log", 1_000_000))       # Ausgabe der Scanläufe
        add("log/scheduler.log", _tail(BASE / "log" / "scheduler.log", 500_000))
        add("log/webapp_stall.txt", _tail(BASE / "log" / "webapp_stall.txt", 1_000_000))   # Aussetzer des Dashboards
        # letzte Minuten vor dem vorigen Start (nur mit dauerhaftem Systemprotokoll): warum hat er neu gestartet?
        add("journal/previous-boot-tail.txt", _run(["journalctl", "-b", "-1", "-n", "300", "--no-pager", "-o", "short-iso"]).encode())
        add("journal/kernel.txt", _run(["journalctl", "-k", "--since", "-2d", "--no-pager", "-o", "short-iso"], 60)[-3_000_000:])
        for u in ("hamscanpi-web", "hamscanpi-scheduler", "hamscanpi-rescue"):
            add(f"journal/{u}.txt", _run(["journalctl", "-u", u, "--since", "-2d", "--no-pager", "-o", "short-iso"], 60)[-2_000_000:])
        add("systemd/status.txt", _run(["systemctl", "status", "--no-pager", "-l", "hamscanpi-web", "hamscanpi-scheduler",
                                         "hamscanpi-rescue"]))
        add("git.txt", git_info())                                                    # woher kommen Updates?
        add("pmsdr.txt", pmsdr_info())                                                # Kurzwelle: Soundkarte, Empfangstest
        for name in ("notices.json", "health_state.json", "live_state.json", "sticks.json", "pskreporter_state.json",
                     "runtime_status.json", "sat_stats.json", "update_check.json"):
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


# ------------------------------------------------------------------ Herunterladen
# Handy-Browser holen eine Datei oft zweimal (Vorschau, dann der Download-Manager) oder brechen ab und versuchen es
# neu. Früher baute jeder Abruf das Paket neu (je ~15 s, Stromsparen der Karte egal) und brach mit „Broken pipe“ ab.
# Jetzt: einmal im RAM-Ordner bauen, 2 min für Wiederholungen bereithalten, mit Länge ausliefern (Fortschritt).
_LOCK = threading.Lock()
_LAST = {"path": None, "t": 0.0, "name": None}


def prepared(max_age=120):
    with _LOCK:
        p = _LAST["path"]
        if p and p.exists() and time.time() - _LAST["t"] < max_age:
            return p, _LAST["name"]
        import rundir
        d = rundir.ensure()
        if p:
            p.unlink(missing_ok=True)
        name, p = filename(), d / f"diagnose-{os.getpid()}.tar.gz"
        tmp = p.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            write(f, stream=False)
        os.replace(tmp, p)
        _LAST.update(path=p, t=time.time(), name=name)
        return p, name


def send(handler):
    """An einen http.server-Handler ausliefern. Rückgabe True = ganz angekommen, False = Browser hat abgebrochen."""
    p, name = prepared()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/gzip")
    handler.send_header("Content-Disposition", f'attachment; filename="{name}"')
    handler.send_header("Content-Length", str(p.stat().st_size))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.close_connection = True
    try:
        with open(p, "rb") as f:
            while True:
                b = f.read(65536)
                if not b:
                    return True
                handler.wfile.write(b)
    except (BrokenPipeError, ConnectionResetError, TimeoutError):
        return False
