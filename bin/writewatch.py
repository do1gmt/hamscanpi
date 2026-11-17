"""Wer schreibt welche Datei? – Zählung für das Diagnose-Paket (Schreiblast der Speicherkarte).

Ein Faden im Webserver lässt sich vom Kernel (inotify) melden, wenn in den Ordnern von Hesperos DX (data, log,
config, recordings, reports) und – soweit lesbar – im Systemprotokoll eine Datei fertig geschrieben, angelegt,
umbenannt oder gelöscht wird. Gezählt wird je Datei: Vorgänge und ungefähr geschriebene Bytes (Zuwachs bzw.
Größe der neu geschriebenen Datei). Kostet fast nichts: nur Ereignisse, kein Abfragen; einzelne write()-Aufrufe
(IN_MODIFY) werden bewusst nicht gezählt, damit Aufnahmen keinen Ereignissturm auslösen.
Ergebnis: report() → Text, im Diagnose-Paket als io_files.txt.
"""
import ctypes
import ctypes.util
import os
import struct
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
IN_CLOSE_WRITE, IN_MOVED_TO, IN_CREATE, IN_DELETE = 0x08, 0x80, 0x100, 0x200
IN_ISDIR = 0x40000000
MASK = IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE | IN_DELETE
MAX_DIRS = 600
MAX_FILES = 4000
_st = {"started": None, "files": {}, "dirs": 0, "error": None, "thread": None}
_lock = threading.Lock()
_new = {}


def _roots():
    out = [BASE / d for d in ("data", "log", "config")]
    rec = [BASE / d for d in ("recordings", "reports")]
    sysd = [Path("/var/log"), Path("/var/log/journal")]
    try:
        sysd += [p for p in Path("/var/log/journal").iterdir() if p.is_dir()]
    except OSError:
        pass
    return out, rec, sysd


def _key(path):
    """Gleichartige Dateien zusammenfassen (Aufnahmen, Bilder mit Zeitstempel) – sonst wird die Liste endlos."""
    p = str(path)
    b = str(BASE) + "/"
    if p.startswith(b + "recordings/") or p.startswith(b + "reports/"):
        top = p[len(b):].split("/")[0]
        ext = os.path.splitext(p)[1] or "(ohne Endung)"
        return f"{top}/…*{ext}"
    if p.startswith(b):
        p = p[len(b):]
    name = os.path.basename(p)
    if name.startswith(".") and name.endswith((".tmp", ".restore")):
        p = os.path.dirname(p) + "/.*.tmp"
    elif name.endswith(".tmp"):
        p = p[:-4] + " (.tmp)"
    return p


def _note(path, kind):
    k = _key(path)
    with _lock:
        f = _st["files"].get(k)
        if f is None:
            if len(_st["files"]) >= MAX_FILES:
                return
            f = _st["files"][k] = {"n": 0, "create": 0, "delete": 0, "bytes": 0, "size": {}}
        if kind == "delete":
            f["delete"] += 1
            f["size"].pop(str(path), None)
            return
        if kind == "create":
            f["create"] += 1
            if len(_new) < 2000:
                _new[str(path)] = True                        # neu angelegt: beim Schließen zählt die ganze Größe
            return
        f["n"] += 1
        try:
            sz = os.stat(path).st_size
        except OSError:
            return
        last = 0 if _new.pop(str(path), None) else f["size"].get(str(path))
        if kind == "replace":
            f["bytes"] += sz                                  # fertig geschriebene Datei ersetzt die alte ganz
        elif last is not None and sz > last:
            f["bytes"] += sz - last                           # angehängt (Protokolle)
        # sonst: an Ort und Stelle geändert (Datenbank) – Größe sagt nichts, die Zahl der Vorgänge zählt
        if len(f["size"]) < 50:
            f["size"][str(path)] = sz


def _loop():
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    fd = libc.inotify_init1(os.O_CLOEXEC)
    if fd < 0:
        _st["error"] = f"inotify_init1: errno {ctypes.get_errno()}"
        return
    wd = {}

    def watch(d):
        if len(wd) >= MAX_DIRS:
            return
        w = libc.inotify_add_watch(fd, str(d).encode(), MASK)
        if w >= 0:
            wd[w] = Path(d)

    own, rec, sysd = _roots()
    for d in own + sysd:
        if d.is_dir():
            watch(d)
    for top in rec:
        if top.is_dir():
            watch(top)
            for dp, dn, _ in os.walk(top):
                for n in dn:
                    watch(Path(dp) / n)
                if len(wd) >= MAX_DIRS:
                    break
    _st["dirs"] = len(wd)
    buf = b""
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            time.sleep(1)
            continue
        buf += chunk
        while len(buf) >= 16:
            w, mask, _cookie, ln = struct.unpack_from("iIII", buf, 0)
            if len(buf) < 16 + ln:
                break
            name = buf[16:16 + ln].split(b"\0", 1)[0].decode("utf-8", "replace")
            buf = buf[16 + ln:]
            base = wd.get(w)
            if base is None or not name:
                continue
            p = base / name
            if mask & IN_ISDIR:
                if mask & IN_CREATE and any(str(p).startswith(str(r)) for r in rec):
                    watch(p)                                  # neuer Tagesordner bei den Aufnahmen
                continue
            if mask & IN_DELETE:
                _note(p, "delete")
            elif mask & IN_CREATE:
                _note(p, "create")
            elif mask & IN_MOVED_TO:
                _note(p, "replace")                           # fertig geschriebene .tmp umbenannt
            elif mask & IN_CLOSE_WRITE:
                if not name.endswith((".tmp", ".part")) and not name.startswith("."):
                    _note(p, "write")                         # .tmp zählt erst beim Umbenennen (sonst doppelt)


def start():
    if _st["thread"]:
        return
    _st["started"] = time.time()
    t = threading.Thread(target=_safe_loop, name="writewatch", daemon=True)
    _st["thread"] = t
    t.start()


def _safe_loop():
    try:
        _loop()
    except Exception as e:                                    # darf den Webserver nie stören
        _st["error"] = f"{type(e).__name__}: {e}"


def report(now=None):
    now = now or time.time()
    if not _st["started"]:
        return "Zählung läuft nicht (Webserver ohne writewatch gestartet)\n"
    hours = max(1 / 60, (now - _st["started"]) / 3600)
    with _lock:
        rows = sorted(_st["files"].items(), key=lambda kv: -kv[1]["bytes"])
    lines = [f"Geschriebene Dateien seit {time.strftime('%Y-%m-%d %H:%M', time.localtime(_st['started']))} "
             f"({hours:.1f} h, {_st['dirs']} Ordner beobachtet){' – Fehler: ' + _st['error'] if _st['error'] else ''}",
             "Vorgänge = fertig geschrieben/ersetzt; Bytes ungefähr (angehängt bzw. Größe der ersetzten Datei; bei",
             "Datenbanken nur die Zahl der Vorgänge); je Stunde. Jeder Vorgang kostet die Karte zusätzlich Journal-Blöcke.",
             "", f"{'MB/h':>8} {'Vorg./h':>8} {'neu/h':>7} {'weg/h':>7}  Datei"]
    for k, f in rows[:60]:
        lines.append(f"{f['bytes'] / 1e6 / hours:8.2f} {f['n'] / hours:8.1f} {f['create'] / hours:7.1f} {f['delete'] / hours:7.1f}  {k}")
    tot = sum(f["bytes"] for _, f in rows)
    lines += ["", f"Summe ≈ {tot / 1e6 / hours:.1f} MB/h ≈ {tot / 1e9 / hours * 24:.2f} GB/Tag "
                  "(nur Dateiinhalt – das Dateisystem schreibt für Verzeichnis und Journal noch etwas dazu)"]
    return "\n".join(lines) + "\n"
