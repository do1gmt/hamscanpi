#!/usr/bin/env python3
"""Sicherung und Wiederherstellung von HamScanPi – Einstellungen, Datenbestände, Aufnahmen.

Umfang
  settings  config/ (ohne Vorlagen), Geräteregister, Satelliten-Statistik und -Ergebnisse, kleine Zustände
  data      + Datenbanken (Treffer, Stationen, UKW-Sender, Störlinien, Systemverlauf)
  all       + recordings/ und reports/ (Ton, I/Q, Wasserfälle, SSTV-Bilder – kann groß sein)

Die Datenbanken werden über die Sicherungsfunktion von SQLite kopiert: stimmig, auch wenn gerade
geschrieben wird. Kurzlebiges (Sperren, PIDs, Status, Pegel) kommt nicht hinein. Ohne Zugangsdaten
(--no-secrets) fehlen Mail-Passwort, Telegram-/ntfy-Token und der Zugangsschutz (config/auth.json).

Kommandozeile
  python3 bin/backup.py create [--scope settings|data|all] [--no-secrets] [-o datei.tar.gz]
  python3 bin/backup.py info datei.tar.gz
  python3 bin/backup.py restore datei.tar.gz [--yes]
Beim Wiederherstellen wird gewartet, bis kein Scan und kein Überflug läuft (Stick-Sperre); der bisherige
Stand von config/ und data/ wird vorher nach restore-previous/<zeit>/ gelegt. Danach die Dienste neu
starten: sudo systemctl restart hamscanpi-scheduler hamscanpi-web (im Dashboard geschieht das selbst).
"""
import argparse
import fcntl
import io
import json
import os
import shutil
import socket
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FORMAT = 1
SCOPES = ("settings", "data", "all")
SECRET_KEYS = ("smtp_pass", "tg_token", "ntfy_token")
DATA_SMALL = ("sticks.json", "sat_stats.json", "sat_results.json", "fm_gain.json", "pskreporter_state.json", "live_state.json",
              "notify_state.json", "tle.json")
DATABASES = ("scanner.db", "sysmon.db")
SKIP_SUFFIX = (".lock", ".pid", ".tmp", ".trigger")
ALLOWED_TOP = ("config/", "data/", "recordings/", "reports/")
UPLOAD = BASE / "data" / "restore_upload.tar.gz"
PENDING = BASE / "data" / "restore.pending"


def _version():
    try:
        return (BASE / "VERSION").read_text().strip()
    except OSError:
        return "?"


def _settings():
    try:
        return json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _config_files(secrets):
    """[(arcname, Pfad oder Bytes)] aus config/ – ohne Vorlagen, ohne Zugangsdaten auf Wunsch."""
    out = []
    for p in sorted((BASE / "config").glob("*")):
        if not p.is_file() or p.name.endswith((".default.json", ".example.json")) or p.name.startswith("."):
            continue
        if p.name == "auth.json" and not secrets:
            continue
        if p.name == "web_settings.json" and not secrets:
            d = json.loads(p.read_text(encoding="utf-8"))
            n = d.get("notify") or {}
            for k in SECRET_KEYS:
                n.pop(k, None)
            d["notify"] = n
            out.append((f"config/{p.name}", json.dumps(d, indent=2, ensure_ascii=False).encode("utf-8")))
            continue
        out.append((f"config/{p.name}", p))
    return out


def _walk(top):
    root = BASE / top
    if not root.is_dir():
        return []
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not d.startswith(".")]
        for f in fn:
            if f.startswith(".") or f.endswith(SKIP_SUFFIX):
                continue
            p = Path(dp) / f
            out.append((p.relative_to(BASE).as_posix(), p))
    return sorted(out)


def sizes():
    """Ungefähre Größe je Umfang in Bytes (für die Anzeige im Dashboard)."""
    s = sum(p.stat().st_size for _, p in _config_files(True) if isinstance(p, Path))
    s += sum((BASE / "data" / f).stat().st_size for f in DATA_SMALL if (BASE / "data" / f).exists())
    d = sum((BASE / "data" / f).stat().st_size for f in DATABASES if (BASE / "data" / f).exists())
    r = sum(p.stat().st_size for top in ("recordings", "reports") for _, p in _walk(top))
    return {"settings": s, "data": s + d, "all": s + d + r}


def _db_snapshot(name, tmpdir):
    """Stimmige Kopie einer SQLite-Datenbank über die Sicherungsfunktion (auch während geschrieben wird)."""
    src = BASE / "data" / name
    if not src.exists():
        return None
    dst = Path(tmpdir) / name
    a = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    b = sqlite3.connect(dst)
    try:
        a.backup(b)
    finally:
        b.close()
        a.close()
    return dst


def write(fileobj, scope="data", secrets=True, stream=False):
    """Sicherung als tar.gz in fileobj schreiben (stream=True: ohne Zurückspulen, für die direkte Ausgabe)."""
    if scope not in SCOPES:
        raise ValueError(f"Umfang: {', '.join(SCOPES)}")
    st = _settings()
    files = _config_files(secrets)
    files += [(f"data/{f}", BASE / "data" / f) for f in DATA_SMALL if (BASE / "data" / f).exists()]
    with tempfile.TemporaryDirectory(dir=BASE / "data") as td:
        if scope in ("data", "all"):
            for name in DATABASES:
                snap = _db_snapshot(name, td)
                if snap:
                    files.append((f"data/{name}", snap))
        if scope == "all":
            files += _walk("recordings") + _walk("reports")
        manifest = {"format": FORMAT, "app": "HamScanPi", "version": _version(),
                    "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": socket.gethostname(),
                    "call": st.get("my_call", ""), "locator": st.get("my_locator", ""), "scope": scope,
                    "secrets": bool(secrets), "files": len(files)}
        with tarfile.open(fileobj=fileobj, mode="w|gz" if stream else "w:gz") as tar:
            data = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
            ti = tarfile.TarInfo("manifest.json")
            ti.size, ti.mtime = len(data), int(time.time())
            tar.addfile(ti, io.BytesIO(data))
            for arc, src in files:
                if isinstance(src, (bytes, bytearray)):
                    ti = tarfile.TarInfo(arc)
                    ti.size, ti.mtime = len(src), int(time.time())
                    tar.addfile(ti, io.BytesIO(src))
                else:
                    try:
                        tar.add(str(src), arcname=arc, recursive=False)
                    except FileNotFoundError:
                        pass                                   # zwischendurch gelöscht (Aufräumen)
    return manifest


def filename(scope):
    return f"hamscanpi-backup-{time.strftime('%Y%m%d-%H%M')}-{scope}.tar.gz"


def info(path):
    """Inhaltsliste einer Sicherung prüfen und zurückgeben (wirft ValueError bei fremden Dateien)."""
    try:
        with tarfile.open(path, "r:gz") as tar:
            m = tar.extractfile("manifest.json")
            man = json.loads(m.read().decode("utf-8")) if m else None
            names = tar.getnames()
    except (tarfile.TarError, KeyError, OSError, json.JSONDecodeError) as e:
        raise ValueError(f"keine HamScanPi-Sicherung ({type(e).__name__})")
    if not man or man.get("app") != "HamScanPi":
        raise ValueError("keine HamScanPi-Sicherung (Inhaltsliste fehlt)")
    man["size"] = Path(path).stat().st_size
    man["has_db"] = any(n in ("data/scanner.db", "data/sysmon.db") for n in names)
    man["has_recordings"] = any(n.startswith(("recordings/", "reports/")) for n in names)
    return man


def _safe_members(tar):
    for m in tar.getmembers():
        n = m.name
        if n == "manifest.json":
            continue
        if n.startswith("/") or ".." in Path(n).parts or not n.startswith(ALLOWED_TOP):
            raise ValueError(f"unerlaubter Pfad in der Sicherung: {n}")
        if not (m.isfile() or m.isdir()):
            raise ValueError(f"unerlaubter Eintrag in der Sicherung: {n}")
        yield m


def restore(path, wait=True, log=print):
    """Sicherung einspielen: unter der Stick-Sperre (kein Scan, kein Überflug), bisherigen Stand von config/
    und data/ nach restore-previous/<zeit>/ legen, dann auspacken. Aufnahmen werden ergänzt, nicht gelöscht."""
    man = info(path)
    lock = open(BASE / "data" / "scan.lock", "a")
    if wait:
        log("warte, bis kein Scan und kein Überflug läuft …")
        fcntl.flock(lock, fcntl.LOCK_EX)
    else:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Scan oder Überflug läuft – später noch einmal")
    try:
        aside = BASE / "restore-previous" / time.strftime("%Y%m%d-%H%M%S")
        aside.mkdir(parents=True, exist_ok=True)
        for top in ("config", "data"):
            src = BASE / top
            if src.is_dir():
                shutil.copytree(src, aside / top, ignore=shutil.ignore_patterns(
                    "*.lock", "*.pid", "restore_upload.tar.gz", "tmp*"), dirs_exist_ok=True)
        log(f"bisheriger Stand gesichert: {aside.relative_to(BASE)}")
        n = 0
        with tarfile.open(path, "r:gz") as tar:
            for m in _safe_members(tar):
                dst = BASE / m.name
                if m.isdir():
                    dst.mkdir(parents=True, exist_ok=True)
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                tmp = dst.with_name(f".{dst.name}.restore")
                with tar.extractfile(m) as f, open(tmp, "wb") as out:
                    shutil.copyfileobj(f, out, 1 << 20)
                os.utime(tmp, (m.mtime, m.mtime))
                if dst.suffix == ".db":                      # alte WAL-/SHM-Dateien gehören nicht zur neuen Datenbank
                    for ext in ("-wal", "-shm"):
                        Path(str(dst) + ext).unlink(missing_ok=True)
                os.replace(tmp, dst)
                n += 1
        log(f"{n} Dateien eingespielt (Sicherung vom {man['created']}, Version {man['version']}, Umfang {man['scope']})")
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    try:                                                     # Konfiguration auf den aktuellen Stand bringen
        import subprocess
        subprocess.run([sys.executable, str(BASE / "bin" / "migrate_config.py")], cwd=BASE, timeout=120,
                       capture_output=True)
    except Exception:
        pass
    return man


def apply_pending(log=print):
    """Für den Scheduler: eine im Dashboard bestätigte Wiederherstellung ausführen (wenn der Stick frei ist).
    Rückgabe True, wenn eingespielt wurde (der Aufrufer startet dann neu)."""
    if not PENDING.exists():
        return False
    try:
        restore(UPLOAD, wait=False, log=log)
    except RuntimeError:
        return False                                         # Stick belegt: nächste Runde
    finally:
        pass
    PENDING.unlink(missing_ok=True)
    UPLOAD.unlink(missing_ok=True)
    return True


def main():
    ap = argparse.ArgumentParser(description="HamScanPi sichern und wiederherstellen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="Sicherung erstellen")
    c.add_argument("--scope", choices=SCOPES, default="data")
    c.add_argument("--no-secrets", action="store_true", help="ohne Mail-Passwort, Tokens und Zugangsschutz")
    c.add_argument("-o", "--output")
    i = sub.add_parser("info", help="Inhalt einer Sicherung anzeigen")
    i.add_argument("file")
    r = sub.add_parser("restore", help="Sicherung einspielen")
    r.add_argument("file")
    r.add_argument("--yes", action="store_true", help="ohne Rückfrage")
    a = ap.parse_args()
    if a.cmd == "create":
        out = Path(a.output or filename(a.scope))
        with open(out, "wb") as f:
            man = write(f, a.scope, not a.no_secrets)
        print(f"{out}  ({out.stat().st_size / 1e6:.1f} MB, {man['files']} Dateien, Umfang {a.scope}"
              f"{', ohne Zugangsdaten' if a.no_secrets else ''})")
    elif a.cmd == "info":
        print(json.dumps(info(a.file), indent=2, ensure_ascii=False))
    else:
        man = info(a.file)
        print(f"Sicherung vom {man['created']} · Version {man['version']} · {man.get('call') or '–'} "
              f"{man.get('locator') or ''} · Umfang {man['scope']} · {man['files']} Dateien")
        if not a.yes and input("Einspielen? Der bisherige Stand wird zur Seite gelegt. [j/N] ").strip().lower() not in ("j", "ja", "y", "yes"):
            sys.exit("abgebrochen")
        restore(a.file)
        print("Fertig. Dienste neu starten: sudo systemctl restart hamscanpi-scheduler hamscanpi-web")


if __name__ == "__main__":
    main()
