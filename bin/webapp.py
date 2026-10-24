#!/usr/bin/env python3
"""
HamScanPi – Webserver und JSON-API.

Alles, was das Dashboard anzeigt, kommt live aus der Datenbank und den
Dateien in reports/. Es wird kein statischer Report mehr gebaut.
Zeiten: durchgehend UTC (ISO-8601 mit +00:00).
"""
import io
import json
import re
import os
import time
import mimetypes
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("image/svg+xml", ".svg")
import sqlite3
import subprocess
import sys
import threading
import wave
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import numpy as np
from scipy.signal import butter, sosfiltfilt, resample_poly

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live as LIVE
import applog as APPLOG
import devices as DEV
import notify as NOTIFY
import auth as AUTH
import housekeeping as HK
import stations as ST

BASE = Path(__file__).resolve().parent.parent
REPORTS = BASE / "reports"
WEB = BASE / "web"
RECORDINGS = BASE / "recordings"
SETTINGS = BASE / "config" / "web_settings.json"
SCANNER_CONFIG = BASE / "config" / "scanner.json"
TRIGGER = BASE / "data" / "scan_now.trigger"
STATUSFILE = BASE / "data" / "runtime_status.json"
LOCKFILE = BASE / "data" / "scan.lock"
DB = BASE / "data" / "scanner.db"

# 0 = automatischer Scan aus
ALLOWED_INTERVALS = [0, 2, 3, 5, 10, 15, 30, 60, 120, 240, 480]
RASTERS = {"normal": "normal", "fein": "fein (¾ Schritt)", "sehr_fein": "sehr fein (½ Schritt)"}

MODE_LABELS = {
    "cw": "CW", "digital": "Digital", "ft8": "FT8", "ft4": "FT4",
    "wspr": "WSPR", "rtty": "RTTY", "voice": "Sprache", "sstv": "SSTV",
    "beacon": "Bake", "fm": "FM", "sat": "Satellit", "data": "Daten",
    "carrier": "Dauerträger", "noise": "Störung/Breitband", "am": "AM", "wfm": "UKW-Rundfunk", "relais": "Relais",
    "aprs": "APRS", "pocsag": "Pager", "dv": "Digitalfunk",
    "unknown": "Unbekannt", "": "Unbekannt",
}


# ------------------------------------------------------------------ Helfer

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def db_connect():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def ensure_schema():
    db = sqlite3.connect(DB, timeout=10)
    # frische Installation: Tabellen anlegen wie scanner.py, damit die Spalten unten greifen
    db.execute("""CREATE TABLE IF NOT EXISTS scan (id INTEGER PRIMARY KEY, time TEXT NOT NULL,
        band TEXT NOT NULL, center_hz INTEGER NOT NULL, noise_db REAL NOT NULL,
        rms_i REAL NOT NULL, rms_q REAL NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS peak (id INTEGER PRIMARY KEY, scan_id INTEGER NOT NULL,
        rf_hz REAL NOT NULL, offset_hz REAL NOT NULL, level_db REAL NOT NULL, snr_db REAL NOT NULL,
        recording_path TEXT)""")
    for col, typ in (
        ("recording_path", "TEXT"), ("protected", "INTEGER NOT NULL DEFAULT 0"),
        ("segment_id", "TEXT"), ("mode", "TEXT"), ("sideband", "TEXT"),
        ("carrier_delta_hz", "REAL"), ("note", "TEXT"), ("rec_center_hz", "REAL"),
        ("audio", "TEXT"), ("audio_score", "REAL"),
    ):
        try:
            db.execute(f"ALTER TABLE peak ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    db.execute("""CREATE TABLE IF NOT EXISTS run (
        id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
        duration_s INTEGER, status TEXT, peaks INTEGER DEFAULT 0, rejected INTEGER DEFAULT 0, error TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS deletion_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, deleted_at TEXT NOT NULL,
        action TEXT NOT NULL, peak_id INTEGER, band TEXT, rf_hz REAL,
        recording_path TEXT, note TEXT)""")
    ST.ensure_tables(db)                       # gehörte Stationen, Beobachtungsliste
    db.commit()
    db.close()


def scanner_config():
    return read_json(SCANNER_CONFIG, {"ranges": []})


def device_labels():
    """Gerätenamen für die Oberfläche; in config/scanner.json unter device_labels/antennas änderbar."""
    cfg = scanner_config()
    lab = {"pmsdr": "PMSDR", "rtlsdr": "RTL-SDR", **(cfg.get("device_labels") or {})}
    ant = cfg.get("antennas") or {}
    rng = {"pmsdr": "Kurzwelle", "rtlsdr": "VHF/UHF"}
    return {k: f"{lab[k]} ({rng[k]}{', ' + ant[k] if ant.get(k) else ''})" for k in ("pmsdr", "rtlsdr")}


def restricted_allowed():
    return bool(scanner_config().get("allow_restricted_bands"))


def restricted_ranges():
    """Frequenzbereiche gesperrter Bänder (leer, wenn freigeschaltet)."""
    cfg = scanner_config()
    if cfg.get("allow_restricted_bands"):
        return []
    return [(int(b["start_hz"]), int(b["stop_hz"])) for b in cfg.get("ranges", []) if b.get("legal") == "restricted"]


def check_not_restricted(freq_hz):
    for a, b in restricted_ranges():
        if a - 5000 <= int(freq_hz) <= b + 5000:
            raise ValueError(f"{int(freq_hz)/1e6:.4f} MHz liegt in einem Band, dessen Mithören in vielen Ländern "
                             "verboten ist (z. B. Flugfunk) – gesperrt. Freischalten nur, wo es erlaubt ist: "
                             "allow_restricted_bands in config/scanner.json")


def bandplan():
    cfg = scanner_config()
    out = []
    for b in cfg.get("ranges", []):
        if b.get("legal") == "restricted" and not cfg.get("allow_restricted_bands"):
            continue                       # gesperrte Bänder erscheinen gar nicht
        if b.get("satellite"):
            try:
                import satellites as SAT
                b = dict(b, segments=SAT.segments_for_bandplan())
            except Exception:
                pass
        out.append({
            "name": str(b["name"]),
            "device": str(b.get("device", "pmsdr")),
            "legal": str(b.get("legal", "amateur")),
            "start_hz": int(b["start_hz"]),
            "stop_hz": int(b["stop_hz"]),
            "segments": [
                {
                    "id": str(s["id"]),
                    "label": str(s.get("label", s["id"])),
                    "mode": str(s.get("mode", "unknown")),
                    "mode_label": MODE_LABELS.get(str(s.get("mode", "")), str(s.get("mode", ""))),
                    "sideband": s.get("sideband"),
                    "ranges": s.get("ranges", []),
                    "frequencies_hz": s.get("frequencies_hz", []),
                    "offset_hz": s.get("offset_hz"),
                    "note": s.get("note", ""),
                    "legal": str(s.get("legal", b.get("legal", "amateur"))),
                }
                for s in b.get("segments", []) if s.get("id")
            ],
        })
    return out


# ------------------------------------------------------------------ Einstellungen

def load_settings():
    plan = bandplan()
    names = [b["name"] for b in plan]
    data = read_json(SETTINGS, {})
    try:
        interval = int(data.get("scan_interval_minutes", 120))
    except Exception:
        interval = 120
    if interval not in ALLOWED_INTERVALS:
        interval = 120
    selected = [str(x) for x in data.get("selected_bands", []) if str(x) in names]
    raw_segments = data.get("selected_segments", {})
    segments = {}
    for b in plan:
        ids = {s["id"] for s in b["segments"]}
        chosen = raw_segments.get(b["name"], ["all"]) if isinstance(raw_segments, dict) else ["all"]
        if not isinstance(chosen, list) or not chosen:
            chosen = ["all"]
        chosen = [str(x) for x in chosen if str(x) == "all" or str(x) in ids]
        segments[b["name"]] = ["all"] if (not chosen or "all" in chosen) else chosen
    choice = "rtlsdr" if data.get("active_device") == "rtlsdr" else "pmsdr"
    hw = DEV.detect_hardware()
    return {
        "scan_interval_minutes": interval,
        "allowed_intervals": ALLOWED_INTERVALS,
        "selected_bands": selected,
        "selected_segments": segments,
        "record_seconds": float(scanner_config().get("record_seconds", 5.0)),
        "my_locator": str(data.get("my_locator", "") or "").upper(),
        "my_call": str(data.get("my_call", "") or "").upper(),
        "pskreporter": bool(data.get("pskreporter")),
        "watchlist": data.get("watchlist") or [],
        "schedules": data.get("schedules") or [],
        "watch_events": ST.EVENTS,
        "pi_time": time.strftime("%H:%M", time.localtime()), "pi_tz": time.strftime("%Z"),
        "schedule_active": _active_schedule_info(data),
        "scan_raster": {d: (str((data.get("scan_raster") or {}).get(d, "normal"))
                            if str((data.get("scan_raster") or {}).get(d, "normal")) in RASTERS else "normal")
                        for d in ("pmsdr", "rtlsdr")},
        "rasters": RASTERS,
        "cleanup_auto_days": int(data.get("cleanup_auto_days") or 0),
        "allow_restricted": restricted_allowed(),
        "sat_prio": {k: v for k, v in (data.get("sat_prio") or {}).items() if v == "pin" or str(v).isdigit()},
        "active_device": DEV.resolve_device(choice, hw),
        "active_device_choice": choice,
        "hardware": hw,
        "notify": {k: ("••••" if k in ("smtp_pass", "ntfy_token", "tg_token") and v else v)
                   for k, v in (data.get("notify") or {}).items()},
        "devices": device_labels(),
        "antennas": (scanner_config().get("antennas") or {}),
    }


def save_settings(data):
    current = load_settings()
    plan = {b["name"]: b for b in bandplan()}
    out = read_json(SETTINGS, {})
    if "scan_interval_minutes" in data:
        iv = int(data["scan_interval_minutes"])
        if iv not in ALLOWED_INTERVALS:
            raise ValueError("Ungültiges Scanintervall")
        out["scan_interval_minutes"] = iv
    if "selected_bands" in data:
        sel = data["selected_bands"]
        if not isinstance(sel, list):
            raise ValueError("selected_bands muss eine Liste sein")
        out["selected_bands"] = [str(x) for x in sel if str(x) in plan]
    # Änderungen als Einzelschritt auf den GESPEICHERTEN Stand anwenden – eine zweite, ältere
    # Browserseite (Handy + PC) schreibt so nicht mehr ihre veraltete Komplettliste zurück
    if "toggle_band" in data:
        name = str(data["toggle_band"].get("name", ""))
        if name not in plan:
            raise ValueError("Unbekanntes Band")
        cur = [b for b in current["selected_bands"] if b in plan]
        if data["toggle_band"].get("on"):
            cur = [n for n in plan if n == name or n in cur]
        else:
            cur = [n for n in cur if n != name]
        out["selected_bands"] = cur
    if "toggle_segment" in data:
        t = data["toggle_segment"]
        band, sid = str(t.get("band", "")), str(t.get("id", ""))
        if band in plan and sid in {x["id"] for x in plan[band]["segments"]}:
            cur = current["selected_segments"].get(band, ["all"])
            lst = [] if "all" in cur else [x for x in cur if x != "all"]
            lst = [x for x in lst if x != sid] if not t.get("on") else lst + ([sid] if sid not in lst else [])
            data = dict(data)
            data["selected_segments"] = {band: lst or ["all"]}
    if "band_segments" in data:
        band = str(data["band_segments"].get("band", ""))
        ids = data["band_segments"].get("ids") or ["all"]
        if band in plan and isinstance(ids, list):
            data = dict(data)
            data["selected_segments"] = {band: ids}
    if "selected_segments" in data:
        seg = data["selected_segments"]
        if not isinstance(seg, dict):
            raise ValueError("selected_segments muss ein Objekt sein")
        cleaned = dict(current["selected_segments"])
        for band, ids in seg.items():
            if band not in plan or not isinstance(ids, list):
                continue
            valid = {s["id"] for s in plan[band]["segments"]}
            ids = [str(x) for x in ids if str(x) == "all" or str(x) in valid]
            cleaned[band] = ["all"] if (not ids or "all" in ids) else ids
        out["selected_segments"] = cleaned
    if "cleanup_auto_days" in data:
        d = int(data["cleanup_auto_days"] or 0)
        if d not in (0, 7, 14, 30, 60, 90, 180):
            raise ValueError("Aufräumen: 0, 7, 14, 30, 60, 90 oder 180 Tage")
        out["cleanup_auto_days"] = d
    if "sat_prio" in data and isinstance(data["sat_prio"], dict):
        import satellites as SAT
        sp = dict(out.get("sat_prio") or {})
        for sid, v in data["sat_prio"].items():
            if sid not in SAT.BY_ID:
                continue
            if v in ("", None, "default"):
                sp.pop(sid, None)
            elif v == "pin" or (str(v).isdigit() and 0 <= int(v) <= 10):
                sp[sid] = v if v == "pin" else int(v)
            else:
                raise ValueError("Priorität: 0–10 oder pin")
        out["sat_prio"] = sp
    if "scan_raster" in data and isinstance(data["scan_raster"], dict):
        r = dict(out.get("scan_raster") or {})
        for d, v in data["scan_raster"].items():
            if d in ("pmsdr", "rtlsdr") and v in RASTERS:
                r[d] = v
        out["scan_raster"] = r
    if "active_device" in data:
        if data["active_device"] not in ("pmsdr", "rtlsdr"):
            raise ValueError("Unbekanntes Gerät")
        out["active_device"] = data["active_device"]
    if "notify" in data and isinstance(data["notify"], dict):
        n = dict(read_json(SETTINGS, {}).get("notify") or {})
        for k in ("enabled", "on_error", "on_scan_done", "on_live"):
            if k in data["notify"]:
                n[k] = bool(data["notify"][k])
        for k in ("smtp_host", "smtp_user", "mail_from", "mail_to", "tls", "push", "ntfy_url", "tg_chat"):
            if k in data["notify"]:
                n[k] = str(data["notify"][k]).strip()
        for k in ("smtp_port", "min_new_hits", "throttle_min"):
            if k in data["notify"]:
                n[k] = int(data["notify"][k] or 0)
        for k in ("smtp_pass", "ntfy_token", "tg_token"):          # Geheimnisse nur bei echter Eingabe ändern
            if k in data["notify"] and data["notify"][k] != "••••":
                n[k] = str(data["notify"][k])
        if n.get("push") not in ("", "ntfy", "telegram", None):
            raise ValueError("Push: ntfy, telegram oder aus")
        out["notify"] = n
    if "my_locator" in data:
        loc = str(data["my_locator"] or "").strip().upper()
        import re as _re
        if loc and not _re.fullmatch(r"[A-R]{2}[0-9]{2}([A-X]{2}([0-9]{2})?)?", loc):
            raise ValueError("Locator ungültig (z. B. JO31 oder JO31AB)")
        out["my_locator"] = loc
    if "my_call" in data:
        c = str(data["my_call"] or "").strip().upper()
        if c and not __import__("re").fullmatch(r"[A-Z0-9/]{3,12}", c):
            raise ValueError("Rufzeichen ungültig")
        out["my_call"] = c
    if "pskreporter" in data:
        out["pskreporter"] = bool(data["pskreporter"])
        if out["pskreporter"] and not (out.get("my_call") and out.get("my_locator")):
            raise ValueError("Für PSK Reporter zuerst Rufzeichen und Locator eintragen")
    if "watchlist" in data:
        out["watchlist"] = clean_watchlist(data["watchlist"])
    if "schedules" in data:
        out["schedules"] = clean_schedules(data["schedules"])
    write_json(SETTINGS, out)
    return load_settings()


# ------------------------------------------------------------------ Status / Scan

def stop_scan_or_pass():
    """Laufenden Scan bzw. Überflug beenden (Überflug: Aufnahme stoppt, Rest wird ausgewertet)."""
    import signal
    for f in (BASE / "data" / "sat_rec.pid", BASE / "data" / "scan.pid"):
        try:
            os.kill(int(f.read_text().strip()), signal.SIGTERM)
            APPLOG.log("WARN", "webapp", "stopped_for_live", what=f.stem)
            return True
        except (OSError, ValueError):
            continue
    return False


def scan_running():
    r = subprocess.run(["/usr/bin/flock", "-n", str(LOCKFILE), "/usr/bin/true"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode != 0


_interrupted_logged = None


def load_status():
    st = read_json(STATUSFILE, {})
    if not st:
        st = {"state": "idle", "phase": "idle", "progress": 0, "message": "Bereit",
              "run_id": None, "started_at": None, "updated_at": None}
    # Hängender Status (Prozess tot, Datei sagt running)?
    if st.get("state") in ("running",) and not scan_running():
        st["state"] = "error"
        st["phase"] = "error"
        st["message"] = "Scanlauf wurde unterbrochen"
        # einmal je Lauf ins Protokoll, mit dem Ende der Rohausgabe – sonst steht dort gar nichts
        global _interrupted_logged
        rid = st.get("run_id")
        if rid != _interrupted_logged:
            _interrupted_logged = rid
            try:
                lines = (BASE / "log" / "scan-cycle.log").read_text(encoding="utf-8", errors="replace").splitlines()
                tail = "|".join(l[:160] for l in lines[-12:] if l.strip())
            except Exception:
                tail = ""
            APPLOG.log("ERROR", "webapp", "run_interrupted", run_id=rid,
                       hint="Prozess ohne Abschluss verschwunden (Neustart, Dienst gestoppt, Speicher voll?)",
                       output_tail=tail)
    if st.get("state") == "queued" and not scan_running():
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(st.get("updated_at"))).total_seconds()
        except Exception:
            age = 0
        if age > 180:
            hb = BASE / "data" / "scheduler.heartbeat"
            alive = hb.exists() and (time.time() - hb.stat().st_mtime) < 60
            st["state"] = "error"
            st["phase"] = "error"
            st["message"] = ("Scan angefordert, Scheduler läuft, aber run_cycle.sh ist nicht gestartet – siehe Protokoll (scheduler)"
                             if alive else "Scan angefordert, aber der Scheduler-Dienst läuft nicht (pmsdr-scheduler.service prüfen)")
    settings = load_settings()
    st["scan_interval_minutes"] = settings["scan_interval_minutes"]
    st["scan_running"] = scan_running()
    st["live_active"] = LIVE.live_active()
    try:
        st["hardware"] = DEV.detect_hardware()
    except Exception:
        pass
    hb = BASE / "data" / "scheduler.heartbeat"
    st["scheduler_alive"] = hb.exists() and (time.time() - hb.stat().st_mtime) < 60
    st["server_time"] = now_iso()
    # nächster automatischer Scan
    st["next_scan_at"] = None
    try:
        db = db_connect()
        row = db.execute("SELECT started_at, COALESCE(finished_at, started_at) FROM run WHERE status IN ('success','failed') ORDER BY id DESC LIMIT 1").fetchone()
        db.close()
        if settings["scan_interval_minutes"] > 0 and row and row[0]:
            # wie im Scheduler: von Start zu Start, aber mindestens 30 s nach dem Ende
            t0 = datetime.fromisoformat(row[0]).astimezone(timezone.utc)
            t1 = datetime.fromisoformat(row[1]).astimezone(timezone.utc)
            nxt = max(t0 + timedelta(minutes=settings["scan_interval_minutes"]), t1 + timedelta(seconds=30))
            st["next_scan_at"] = nxt.isoformat(timespec="seconds")
    except Exception:
        pass
    pp = read_json(BASE / "data" / "scan_postponed.json", None)        # wegen eines Überflugs zurückgestellt
    st["scan_postponed"] = pp if pp and pp.get("until", 0) > time.time() else None
    return st


def save_status(data):
    write_json(STATUSFILE, data)


# ------------------------------------------------------------------ Treffer

def related_files(recording_path):
    if not recording_path:
        return []
    src = Path(recording_path)
    files = [src]
    for f in REPORTS.glob(src.stem + "_*"):
        files.append(f)
    return files


def hit_files(recording_path):
    """Verfügbare Dateien zu einem Treffer (URLs für das Frontend)."""
    out = {}
    if not recording_path:
        return out
    src = Path(recording_path)
    stem = src.stem
    if src.exists():
        out["iq"] = f"/api/download-iq?peak_id="
    for key, suffix in (("spectrum", "_spectrum.png"), ("usb", "_USB.wav"),
                        ("lsb", "_LSB.wav"), ("fm", "_FM.wav"), ("decode", "_decode.json"),
                        ("sstv", "_sstv.png")):
        if (REPORTS / f"{stem}{suffix}").exists():
            out[key] = f"/files/{stem}{suffix}"
    return out


def decode_summary(recording_path):
    """Dekoderergebnisse lesen und offensichtlichen Müll markieren."""
    if not recording_path:
        return []
    p = REPORTS / f"{Path(recording_path).stem}_decode.json"
    data = read_json(p, None)
    if not data:
        return []
    out = []
    for d in data.get("decoders", []):
        text = str(d.get("text", "") or "")
        conf = int(d.get("confidence", 0) or 0)
        plausible = True
        letters = text.replace(" ", "")
        if letters:
            q = letters.count("?") / len(letters)
            uniq = len(set(letters))
            if q > 0.35:
                plausible = False
            if len(letters) >= 12 and uniq <= 2:
                plausible = False          # "EEEEEEEEE"
        if d.get("type") in ("RTTY",) and letters and plausible:
            # RTTY-Rauschtext: kaum Vokale/Leerzeichen
            vowels = sum(c in "AEIOU" for c in letters.upper())
            if len(letters) >= 12 and vowels / len(letters) < 0.12:
                plausible = False
        out.append({"type": d.get("type"), "confidence": conf, "text": text,
                    "detail": d.get("detail", ""), "plausible": plausible,
                    "messages": d.get("messages", []),
                    "ps": ((d.get("rds") or {}).get("ps") or "").strip() if d.get("type") == "RDS" else None,
                    "rds": d.get("rds") if d.get("type") == "RDS" else None})
    return out


def previous_run_bound():
    """Startzeit des letzten erfolgreichen Laufs (für 'neu'-Markierung)."""
    db = db_connect()
    rows = db.execute("SELECT started_at FROM run WHERE status='success' ORDER BY id DESC LIMIT 2").fetchall()
    db.close()
    if len(rows) >= 2:
        return rows[1][0], rows[0][0]
    if len(rows) == 1:
        return None, rows[0][0]
    return None, None


def list_hits(params):
    band = params.get("band", "")
    mode = params.get("mode", "")
    q = params.get("q", "")
    sort = params.get("sort", "time")
    only_protected = params.get("protected", "") == "1"
    limit = max(1, min(int(params.get("limit", 500) or 500), 2000))

    prev_start, last_start = previous_run_bound()
    seg_index = {}
    for b in bandplan():
        for sg in b["segments"]:
            seg_index[(b["name"], sg["id"])] = (sg, b["device"])

    sql = """SELECT p.id, p.rf_hz, p.snr_db, p.level_db, p.recording_path,
                    COALESCE(p.protected,0) AS protected, p.segment_id, p.mode, p.sideband,
                    p.carrier_delta_hz, p.note, s.time, s.band, s.center_hz, s.noise_db, p.rec_center_hz,
                    p.audio, p.audio_score,
                    (SELECT group_concat(w.label, ' · ') FROM watch_hit w WHERE w.peak_id = p.id) AS watch
             FROM peak p JOIN scan s ON s.id = p.scan_id WHERE 1=1"""
    args = []
    if band and band != "all":
        sql += " AND s.band = ?"
        args.append(band)
    if mode and mode != "all":
        sql += " AND COALESCE(p.mode,'unknown') = ?"
        args.append(mode)
    if only_protected:
        sql += " AND COALESCE(p.protected,0) = 1"
    hidden = [b["name"] for b in scanner_config().get("ranges", [])
              if b.get("legal") == "restricted" and not restricted_allowed()]
    if hidden:                                     # Treffer aus gesperrten Bändern nicht anzeigen
        sql += f" AND s.band NOT IN ({','.join('?' * len(hidden))})"
        args += hidden
    if params.get("audio", "") == "1":
        sql += " AND p.audio = 'ton'"
    if q:
        try:
            mhz = float(q.replace(",", "."))
            sql += " AND ABS(p.rf_hz - ?) <= 5000"
            args.append(mhz * 1e6 if mhz < 1000 else mhz)
        except ValueError:
            sql += " AND (p.note LIKE ? OR s.band LIKE ?)"
            args += [f"%{q}%", f"%{q}%"]
    order = {"time": "s.time DESC, p.id DESC", "time_asc": "s.time ASC, p.id ASC",
             "freq": "p.rf_hz ASC", "snr": "p.snr_db DESC",
             "audio": "CASE p.audio WHEN 'ton' THEN 0 WHEN 'traeger' THEN 2 ELSE 1 END, COALESCE(p.audio_score,-99) DESC, s.time DESC"}.get(sort, "s.time DESC, p.id DESC")
    sql += f" ORDER BY {order} LIMIT ?"
    args.append(limit)

    db = db_connect()
    rows = db.execute(sql, args).fetchall()
    db.close()
    out = []
    for r in rows:
        files = hit_files(r["recording_path"])
        if "iq" in files:
            files["iq"] += str(r["id"])
        mode_v = r["mode"] or "unknown"
        sg, device = seg_index.get((r["band"], r["segment_id"]), ({}, "pmsdr"))
        out.append({
            "device": device,
            "offset_hz": sg.get("offset_hz"),
            "segment_label": sg.get("label", ""),
            "segment_note": sg.get("note", ""),
            "id": r["id"], "time": r["time"], "band": r["band"],
            "rf_hz": r["rf_hz"], "snr_db": r["snr_db"], "level_db": r["level_db"],
            "noise_db": r["noise_db"],
            "segment_id": r["segment_id"], "mode": mode_v,
            "mode_label": MODE_LABELS.get(mode_v, mode_v),
            "sideband": r["sideband"], "carrier_delta_hz": r["carrier_delta_hz"],
            "protected": bool(r["protected"]), "note": r["note"] or "",
            "is_new": bool(last_start and r["time"] >= last_start),
            "files": files, "decodes": decode_summary(r["recording_path"]),
            "rec": Path(r["recording_path"]).stem if r["recording_path"] else "",
            "audio": r["audio"], "audio_score": r["audio_score"],
            "watch": r["watch"] or "",
        })
    return out


def _run_scans(db, r):
    """Abstimmungen eines Laufs (Zeilen der Tabelle scan im Zeitfenster des Laufs)."""
    if not r["started_at"]:
        return []
    return db.execute("""SELECT time, band FROM scan WHERE time >= ? AND time <= ? AND band != 'Satelliten'
                         ORDER BY time""", (r["started_at"], r["finished_at"] or "9999")).fetchall()


def list_runs(limit=25, offset=0):
    """Scan-Verlauf seitenweise, mit Bändern, Gerät, Treffern je Band und Fehlertext."""
    db = db_connect()
    total = db.execute("SELECT COUNT(*) FROM run").fetchone()[0]
    rows = db.execute("SELECT * FROM run ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    devmap = {b["name"]: b["device"] for b in bandplan()}
    out = []
    for r in rows:
        d = dict(r)
        scans = _run_scans(db, r)
        bands = list(dict.fromkeys(x["band"] for x in scans))
        d["bands"] = bands
        d["tunes"] = len(scans)
        d["device"] = (devmap.get(bands[0]) if bands else None)
        if r["started_at"]:
            hb = db.execute("""SELECT s.band, COUNT(*) n FROM peak p JOIN scan s ON s.id = p.scan_id
                               WHERE s.time >= ? AND s.time <= ? AND s.band != 'Satelliten' GROUP BY s.band""",
                            (r["started_at"], r["finished_at"] or "9999")).fetchall()
            d["hits_per_band"] = {x["band"]: x["n"] for x in hb}
        out.append(d)
    db.close()
    # "läuft" nur für den Lauf, der wirklich läuft – ältere Leichen werden als abgebrochen gezeigt
    busy = scan_running()
    for k, r in enumerate(out):
        if r.get("status") == "running" and (k + offset > 0 or not busy):
            r["status"] = "aborted"
    # Fehler- und Abbruchgründe aus dem Protokoll
    want = {r["id"] for r in out if r.get("status") in ("failed", "aborted", "cancelled")}
    if want:
        try:
            for e in APPLOG.read(limit=3000, level="WARN"):
                rid = (e.get("data") or {}).get("run_id")
                try:
                    rid = int(rid)
                except Exception:
                    continue
                if rid in want:
                    for r in out:
                        if r["id"] == rid and not r.get("reason"):
                            dd = e.get("data") or {}
                            r["reason"] = {"run_cancelled": "vom Benutzer oder für einen Überflug abgebrochen",
                                           "run_interrupted": dd.get("hint", "unterbrochen")}.get(
                                e["event"], dd.get("error") or e["event"])
        except Exception:
            pass
    return {"runs": out, "total": total, "offset": offset, "limit": limit}


_est_cache = {"t": 0.0, "data": None}
DEFAULT_S_PER_TUNE = {"pmsdr": 6.0, "rtlsdr": 8.0}


def scan_history_model():
    """Sekunden je Abstimmung pro Band und fester Anteil je Lauf, aus den letzten Läufen gemessen."""
    if _est_cache["data"] and time.time() - _est_cache["t"] < 300:
        return _est_cache["data"]
    db = db_connect()
    runs = db.execute("""SELECT * FROM run WHERE status='success' AND duration_s > 0
                         ORDER BY id DESC LIMIT 40""").fetchall()
    per_band, overhead = {}, []
    for r in runs:
        scans = _run_scans(db, r)
        if not scans:
            continue
        t_prev = datetime.fromisoformat(r["started_at"])
        first = True
        used = 0.0
        for x in scans:
            t = datetime.fromisoformat(x["time"])
            dt = (t - t_prev).total_seconds()
            t_prev = t
            if first:                                     # Start bis erste Abstimmung: Vorlauf
                first = False
                continue
            if 0 < dt < 600:
                per_band.setdefault(x["band"], []).append(dt)
                used += dt
        overhead.append(max(0.0, float(r["duration_s"]) - used))
    db.close()
    med = lambda v: sorted(v)[len(v) // 2] if v else None
    mean = lambda v: sum(v) / len(v) if v else None            # Mittel: langsame Abstimmungen zählen mit
    model = {"per_band": {b: mean(v) for b, v in per_band.items()}, "overhead": med(overhead) or 20.0,
             "runs": len(runs)}
    _est_cache.update(t=time.time(), data=model)
    return model


def scan_estimate(settings=None):
    """Geschätzte Dauer eines Scans mit der aktuellen Auswahl (Sekunden) und Herleitung."""
    try:
        import scan_plan as SP
        plan = [p for p in SP.make_plan(scanner_config(), read_json(SETTINGS, {})) if p["name"] != "Satelliten"]
    except Exception:
        return None
    m = scan_history_model()
    total, parts = m["overhead"], []
    for p in plan:
        spt = m["per_band"].get(p["name"]) or DEFAULT_S_PER_TUNE.get(p.get("device", "pmsdr"), 7.0)
        sec = len(p["centers_hz"]) * spt
        total += sec
        parts.append({"band": p["name"], "tunes": len(p["centers_hz"]), "seconds": round(sec),
                      "measured": p["name"] in m["per_band"]})
    return {"seconds": round(total), "parts": parts, "overhead": round(m["overhead"]), "based_on_runs": m["runs"]}


def scan_summary():
    """Kennzahlen der letzten 24 h für die Leiste auf der Scan-Seite."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    db = db_connect()
    runs = db.execute("SELECT status, COUNT(*) n FROM run WHERE started_at >= ? GROUP BY status", (since,)).fetchall()
    by = {r["status"]: r["n"] for r in runs}
    hits = db.execute("""SELECT COUNT(*) FROM peak p JOIN scan s ON s.id = p.scan_id
                         WHERE s.time >= ? AND s.band != 'Satelliten'""", (since,)).fetchone()[0]
    sat_hits = db.execute("""SELECT COUNT(*) FROM peak p JOIN scan s ON s.id = p.scan_id
                             WHERE s.time >= ? AND s.band = 'Satelliten'""", (since,)).fetchone()[0]
    sstv = db.execute("""SELECT COUNT(*) FROM peak p JOIN scan s ON s.id = p.scan_id
                         WHERE s.time >= ? AND p.mode = 'sstv'""", (since,)).fetchone()[0]
    busy = db.execute("SELECT COALESCE(SUM(duration_s), 0) FROM run WHERE started_at >= ?", (since,)).fetchone()[0]
    db.close()
    sat_done = 0
    try:
        import satellites as SAT
        t0 = time.time() - 86400
        sat_done = len({r.get("started") for r in SAT.read_results().values()
                        if r.get("state") == "done" and (r.get("started") or 0) > t0})
    except Exception:
        pass
    return {"runs": sum(by.values()), "runs_failed": by.get("failed", 0) + by.get("aborted", 0),
            "hits": hits, "sat_passes": sat_done, "sat_hits": sat_hits, "sstv": sstv,
            "scan_minutes": round((busy or 0) / 60)}


def _active_schedule_info(data):
    try:
        import scan_plan as SP
        d = dict(data)
        d["active_device"] = DEV.resolve_device("rtlsdr" if data.get("active_device") == "rtlsdr" else "pmsdr")
        sc = SP.active_schedule(d, scanner_config())
        return {"name": sc["name"], "bands": sc["bands"], "to": sc["to"]} if sc else None
    except Exception:
        return None


def clean_schedules(items):
    import secrets
    import re as _re
    names = {b["name"] for b in scanner_config().get("ranges", [])}
    out = []
    for it in items if isinstance(items, list) else []:
        f, t = str(it.get("from", "")), str(it.get("to", ""))
        if not (_re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", f) and _re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t)) or f == t:
            raise ValueError("Zeitplan: Uhrzeiten als HH:MM, Beginn und Ende verschieden")
        bands = [b for b in it.get("bands") or [] if b in names]
        if not bands:
            raise ValueError("Zeitplan: mindestens ein Band wählen")
        days = sorted({int(d) for d in it.get("days") or [] if 0 <= int(d) <= 6}) or list(range(7))
        out.append({"id": str(it.get("id") or secrets.token_hex(4)), "name": str(it.get("name") or "").strip()[:40] or f"{f}–{t}",
                    "days": days, "from": f, "to": t, "bands": bands, "enabled": bool(it.get("enabled", True))})
    return out


def clean_watchlist(items):
    """Regeln der Beobachtungsliste prüfen (siehe stations.py)."""
    import secrets
    out = []
    for it in items if isinstance(items, list) else []:
        typ = str(it.get("type", ""))
        val = str(it.get("value", "")).strip()
        if typ not in ("freq", "call", "event", "dx", "traffic") or not val:
            raise ValueError("Beobachtung: Typ und Wert angeben")
        if typ == "freq":
            try:
                f = float(val.replace(",", "."))
            except ValueError:
                raise ValueError(f"Frequenz ungültig: {val} (MHz, z. B. 439.400)")
            if not 0.1 <= f <= 2000:
                raise ValueError(f"Frequenz ungültig: {val} (MHz, z. B. 439.400)")
        if typ == "event" and val not in ST.EVENTS:
            raise ValueError("Ereignis unbekannt")
        if typ == "dx":
            try:
                float(val)
            except ValueError:
                raise ValueError("Entfernung in km angeben")
        if typ == "call" and not __import__("re").fullmatch(r"[A-Za-z0-9/*?-]{2,15}", val):
            raise ValueError(f"Rufzeichen ungültig: {val}")
        if typ == "traffic" and not 1 < len(val) <= 40:
            raise ValueError("Verkehr: Straße (z. B. A9) oder Ort angeben")
        out.append({"id": str(it.get("id") or secrets.token_hex(4)), "type": typ,
                    "value": val.upper() if typ == "call" else val,
                    "label": str(it.get("label") or "").strip()[:60],
                    "enabled": bool(it.get("enabled", True)), "mail": bool(it.get("mail", True)),
                    "tolerance_khz": max(0.5, min(100.0, float(it.get("tolerance_khz") or 5))),
                    "quiet_min": max(0, min(1440, int(it.get("quiet_min") or 30)))})
    return out


def stations(days=30, q="", band="", limit=100, offset=0):
    """Gehörte Stationen, je Rufzeichen zusammengefasst, mit Entfernung und Richtung."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds") if days else ""
    db = db_connect()
    ST.ensure_tables(db)
    where, args = ["time >= ?"], [since]
    if q:
        where.append("call LIKE ?"); args.append(q.upper().replace("*", "%") + ("%" if "*" not in q else ""))
    if band:
        where.append("band = ?"); args.append(band)
    w = " AND ".join(where)
    total = db.execute(f"SELECT COUNT(DISTINCT call) FROM heard WHERE {w}", args).fetchone()[0]
    rows = db.execute(f"""SELECT call, COUNT(*) n, MIN(time) first, MAX(time) last,
                                 group_concat(DISTINCT band) bands, group_concat(DISTINCT mode) modes,
                                 MAX(snr) best_snr, MAX(grid) grid
                          FROM heard WHERE {w} GROUP BY call ORDER BY last DESC LIMIT ? OFFSET ?""",
                       args + [limit, offset]).fetchall()
    db.close()
    loc = load_settings().get("my_locator", "")
    out = []
    for r in rows:
        d = dict(r)
        d["distance_km"], d["bearing"] = ST.distance_bearing(r["grid"], loc) if r["grid"] and loc else (None, None)
        d["lat"] = d["lon"] = None
        if r["grid"]:
            try:
                import satellites as SAT
                d["lat"], d["lon"] = (round(x, 3) for x in SAT.locator_to_latlon(r["grid"]))
            except Exception:
                pass
        out.append(d)
    home = None
    try:
        import satellites as SAT
        home = [round(x, 3) for x in SAT.locator_to_latlon(loc)] if loc else None
    except Exception:
        pass
    return {"stations": out, "total": total, "offset": offset, "limit": limit, "home": home}


def activity(days=30):
    """Aktivität je Band und Stunde (UTC) sowie je Wochentag und Stunde.
    Gezählt werden Treffer je Abstimmung – so verfälscht es nicht, wann und wie oft gescannt wurde."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    hidden = [b["name"] for b in scanner_config().get("ranges", []) if b.get("legal") == "restricted" and not restricted_allowed()]
    excl = f" AND s.band NOT IN ({','.join('?' * len(hidden))})" if hidden else ""
    db = db_connect()
    tunes = db.execute(f"""SELECT s.band, CAST(substr(s.time, 12, 2) AS INTEGER) h, COUNT(*) n FROM scan s
                           WHERE s.time >= ? AND s.band != 'Satelliten'{excl} GROUP BY s.band, h""", [since] + hidden).fetchall()
    hits = db.execute(f"""SELECT s.band, CAST(substr(s.time, 12, 2) AS INTEGER) h, COUNT(*) n FROM peak p JOIN scan s ON s.id = p.scan_id
                          WHERE s.time >= ? AND s.band != 'Satelliten'{excl} GROUP BY s.band, h""", [since] + hidden).fetchall()
    wk_t = db.execute(f"""SELECT CAST(strftime('%w', s.time) AS INTEGER) d, CAST(substr(s.time, 12, 2) AS INTEGER) h, COUNT(*) n
                          FROM scan s WHERE s.time >= ? AND s.band != 'Satelliten'{excl} GROUP BY d, h""", [since] + hidden).fetchall()
    wk_h = db.execute(f"""SELECT CAST(strftime('%w', s.time) AS INTEGER) d, CAST(substr(s.time, 12, 2) AS INTEGER) h, COUNT(*) n
                          FROM peak p JOIN scan s ON s.id = p.scan_id WHERE s.time >= ? AND s.band != 'Satelliten'{excl}
                          GROUP BY d, h""", [since] + hidden).fetchall()
    db.close()
    bands = {}
    for r in tunes:
        bands.setdefault(r["band"], {})[r["h"]] = {"tunes": r["n"], "hits": 0}
    for r in hits:
        bands.setdefault(r["band"], {}).setdefault(r["h"], {"tunes": 0, "hits": 0})["hits"] = r["n"]
    week = {}
    for r in wk_t:
        week.setdefault((r["d"] + 6) % 7, {})[r["h"]] = {"tunes": r["n"], "hits": 0}       # Mo = 0
    for r in wk_h:
        week.setdefault((r["d"] + 6) % 7, {}).setdefault(r["h"], {"tunes": 0, "hits": 0})["hits"] = r["n"]
    order = [b["name"] for b in scanner_config().get("ranges", [])]
    return {"days": days,
            "bands": [{"band": b, "hours": {str(h): v for h, v in bands[b].items()}}
                      for b in sorted(bands, key=lambda x: order.index(x) if x in order else 999)],
            "week": {str(d): {str(h): v for h, v in hs.items()} for d, hs in week.items()}}


def fm_stations(days=0):
    db = db_connect()
    ST.ensure_tables(db)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds") if days else ""
    rows = [dict(r) for r in db.execute("SELECT * FROM fm_station WHERE last >= ? ORDER BY freq_khz", (since,))]
    db.close()
    return {"stations": rows}


def stations_adif(days=30, q="", band=""):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds") if days else ""
    db = db_connect()
    ST.ensure_tables(db)
    where, args = ["time >= ?"], [since]
    if q:
        where.append("call LIKE ?"); args.append(q.upper().replace("*", "%") + ("%" if "*" not in q else ""))
    if band:
        where.append("band = ?"); args.append(band)
    rows = [dict(r) for r in db.execute(f"SELECT * FROM heard WHERE {' AND '.join(where)} ORDER BY time", args)]
    db.close()
    st = load_settings()
    return ST.adif(rows, st.get("my_call", ""), st.get("my_locator", ""))


_live_tmc_seen = set()


def live_traffic_check(st):
    """Verkehrsregeln der Beobachtungsliste auch gegen die TMC-Meldungen des Live-Empfangs prüfen."""
    rules = [r for r in st.get("watchlist") or [] if r.get("type") == "traffic" and r.get("enabled", True)]
    if not rules or not (_live and _live.running and _live.rds):
        return
    for m in ((_live.rds or {}).get("tmc") or {}).get("messages", []):
        for rule in rules:
            key = (rule["id"], m.get("location"), m.get("event"), m.get("direction"))
            if key in _live_tmc_seen or not ST.traffic_match(rule["value"], m):
                continue
            _live_tmc_seen.add(key)
            label = rule.get("label") or f"Verkehr {rule['value']}"
            APPLOG.log("INFO", "watch", "watch_hit", rule=label, detail=m.get("text"), source="live")
            if rule.get("mail", True):
                NOTIFY.notify_watch(rule["id"], label, m.get("text") or "", int(rule.get("quiet_min", 30)))


def watch_and_stations_cycle():
    """Neue Treffer: Rufzeichen eintragen, Beobachtungsliste prüfen, melden."""
    if scan_running():                     # erst nach dem Scan: APRS/RDS/FT8 werden am Ende dekodiert
        return
    db = db_connect()
    try:
        st = load_settings()
        while True:
            new = ST.ingest(db)
            if not new:
                break
            skip = ST.watch_skip_until(db)                  # nachträglich ausgewertete alte Treffer: kein Alarm
            new = [p for p in new if p["id"] > skip]
            for rule, p, detail in ST.check_rules(new, st.get("watchlist"), st.get("my_locator", "")):
                label = rule.get("label") or f"{rule['type']} {rule['value']}"
                cur = db.execute("INSERT OR IGNORE INTO watch_hit (peak_id, rule_id, label, time, detail) VALUES (?,?,?,?,?)",
                                 (p["id"], rule["id"], label, p["time"], detail))
                db.commit()
                if cur.rowcount:
                    APPLOG.log("INFO", "watch", "watch_hit", rule=label, detail=detail, peak_id=p["id"])
                    if rule.get("mail", True):
                        NOTIFY.notify_watch(rule["id"], label, detail, int(rule.get("quiet_min", 30)), image=p.get("image"))
        live_traffic_check(st)
        if st.get("pskreporter") and st.get("my_call") and st.get("my_locator"):
            try:
                import pskreporter as PSK
                ant = (scanner_config().get("antennas") or {}).get("pmsdr", "")
                PSK.report(db, st["my_call"], st["my_locator"], ant,
                           log=lambda level, event, **d: APPLOG.log(level, "pskreporter", event, **d))
            except Exception as e:
                APPLOG.log("WARN", "pskreporter", "pskreporter_failed", error=f"{type(e).__name__}: {e}")
    finally:
        db.close()


def stats():
    db = db_connect()
    total = db.execute("SELECT COUNT(*) FROM peak").fetchone()[0]
    prot = db.execute("SELECT COUNT(*) FROM peak WHERE COALESCE(protected,0)=1").fetchone()[0]
    last = db.execute("SELECT * FROM run WHERE status IN ('success','failed') ORDER BY id DESC LIMIT 1").fetchone()
    per_band = db.execute("""SELECT s.band, COUNT(*) AS n FROM peak p JOIN scan s ON s.id=p.scan_id
                             GROUP BY s.band""").fetchall()
    per_mode = db.execute("""SELECT COALESCE(p.mode,'unknown') AS m, COUNT(*) AS n FROM peak p
                             GROUP BY m""").fetchall()
    db.close()
    prev_start, last_start = previous_run_bound()
    new_count = 0
    if last_start:
        db = db_connect()
        new_count = db.execute("SELECT COUNT(*) FROM peak p JOIN scan s ON s.id=p.scan_id WHERE s.time >= ?",
                               (last_start,)).fetchone()[0]
        db.close()
    return {
        "hits_total": total, "hits_protected": prot, "hits_new": new_count,
        "last_run": dict(last) if last else None,
        "per_band": {r["band"]: r["n"] for r in per_band},
        "per_mode": {r["m"]: r["n"] for r in per_mode},
        "disk_free_mb": int(__import__("shutil").disk_usage(str(BASE)).free / 1e6),
    }


def log_delete(db, action, peak_id, band, rf_hz, recording_path, note=""):
    db.execute("""INSERT INTO deletion_log (deleted_at, action, peak_id, band, rf_hz, recording_path, note)
                  VALUES (?,?,?,?,?,?,?)""", (now_iso(), action, peak_id, band, rf_hz, recording_path, note))


def delete_hit(peak_id, action="manual"):
    db = db_connect()
    row = db.execute("""SELECT p.id, p.rf_hz, p.recording_path, COALESCE(p.protected,0) AS protected, s.band
                        FROM peak p JOIN scan s ON s.id=p.scan_id WHERE p.id=?""", (peak_id,)).fetchone()
    if row is None:
        db.close()
        raise ValueError("Treffer nicht gefunden")
    log_delete(db, action, row["id"], row["band"], row["rf_hz"], row["recording_path"],
               "geschützter Treffer manuell gelöscht" if row["protected"] else "")
    db.execute("DELETE FROM peak WHERE id=?", (peak_id,))
    db.commit()
    db.close()
    for f in related_files(row["recording_path"]):
        try:
            Path(f).unlink()
        except FileNotFoundError:
            pass


def delete_candidates(days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    db = db_connect()
    rows = db.execute("""SELECT p.id, COALESCE(p.protected,0) AS protected, s.time
                         FROM peak p JOIN scan s ON s.id=p.scan_id WHERE s.time < ?""", (cutoff,)).fetchall()
    db.close()
    return [r["id"] for r in rows if not r["protected"]], [r["id"] for r in rows if r["protected"]]


def bulk_delete(days):
    ids, kept = delete_candidates(days)
    for pid in ids:
        delete_hit(pid, action=f"older_than_{days}d")
    return len(ids), len(kept)


def delete_all_unprotected():
    db = db_connect()
    ids = [r[0] for r in db.execute("SELECT id FROM peak WHERE COALESCE(protected,0)=0").fetchall()]
    db.close()
    for pid in ids:
        delete_hit(pid, action="all_unprotected")
    return len(ids)


def set_protected(peak_id, value):
    db = db_connect()
    db.execute("UPDATE peak SET protected=? WHERE id=?", (1 if value else 0, peak_id))
    db.commit()
    db.close()


def set_note(peak_id, note):
    db = db_connect()
    db.execute("UPDATE peak SET note=? WHERE id=?", (str(note)[:500], peak_id))
    db.commit()
    db.close()


def deletion_log(limit=100):
    db = db_connect()
    rows = db.execute("SELECT * FROM deletion_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ Demodulation auf Anfrage

def _lowpass_decimate(x, rate, cutoff, target):
    sos = butter(6, cutoff, btype="low", fs=rate, output="sos")
    x = sosfiltfilt(sos, x)
    from math import gcd
    g = gcd(int(target), int(rate))
    return resample_poly(x, int(target) // g, int(rate) // g), target


def demodulate(bb, rate, mode, side="USB"):
    """Basisband (Signal bei 0 Hz) -> Audio. Modi: ssb, am, nfm, wfm."""
    from scipy.signal import lfilter
    if mode == "am":
        if rate > 24000:
            bb, rate = _lowpass_decimate(bb, rate, 4500, 24000)
        env = np.abs(bb)
        env = env - np.mean(env)
        sos = butter(4, [250, 4500], btype="bandpass", fs=rate, output="sos")
        audio = sosfiltfilt(sos, env)
        return resample_poly(audio, 1, 2), rate // 2
    if mode == "nfm":
        if rate > 48000:
            bb, rate = _lowpass_decimate(bb, rate, 12000, 48000)
        # Kanalfilter ±6,5 kHz vor dem Diskriminator – sonst geht die Stimme im Rauschen
        # der übrigen 48 kHz Bandbreite unter
        ch = butter(6, 6500, btype="low", fs=rate, output="sos")
        bb = sosfiltfilt(ch, bb.real) + 1j * sosfiltfilt(ch, bb.imag)
        d = np.angle(bb[1:] * np.conj(bb[:-1]))
        a = np.exp(-1.0 / (rate * 530e-6))          # Deemphasis (NFM, ~300 Hz Eckfrequenz)
        d = lfilter([1 - a], [1, -a], d)
        sos = butter(4, [250, 3400], btype="bandpass", fs=rate, output="sos")
        audio = sosfiltfilt(sos, d)
        g = __import__("math").gcd(12000, int(rate))
        return resample_poly(audio, 12000 // g, int(rate) // g), 12000
    if mode == "wfm":
        if rate > 240000:
            bb, rate = _lowpass_decimate(bb, rate, 100000, 240000)
        d = np.angle(bb[1:] * np.conj(bb[:-1]))
        a = np.exp(-1.0 / (rate * 50e-6))          # Deemphasis 50 us
        y = lfilter([1 - a], [1, -a], d)
        audio, out = _lowpass_decimate(y, rate, 15000, 48000)
        return audio, out
    # SSB
    Z = np.fft.fft(bb)
    f = np.fft.fftfreq(len(bb), 1.0 / rate)
    mask = ((f >= 200) & (f <= 3000)) if side == "USB" else ((f <= -200) & (f >= -3000))
    audio = np.real(np.fft.ifft(Z * mask)) * 2.0
    sos = butter(6, [200, 3000], btype="bandpass", fs=rate, output="sos")
    audio = sosfiltfilt(sos, audio)
    g = __import__("math").gcd(12000, int(rate))
    return resample_poly(audio, 12000 // g, int(rate) // g), 12000


_demod_cache = OrderedDict()
_demod_lock = threading.Lock()


def demod_on_demand(peak_id, side, offset_hz, mode="ssb", nr=0):
    """SSB-Demodulation aus der gespeicherten I/Q-Aufnahme mit frei wählbarem
    Trägerversatz – damit lässt sich der Treffer im Browser 'feinabstimmen'."""
    cfg = scanner_config()
    db = db_connect()
    row = db.execute("SELECT p.rf_hz, p.recording_path, COALESCE(p.rec_center_hz, s.center_hz) AS center_hz FROM peak p JOIN scan s ON s.id=p.scan_id WHERE p.id=?",
                     (peak_id,)).fetchone()
    db.close()
    if not row or not row["recording_path"] or not Path(row["recording_path"]).exists():
        raise ValueError("Keine I/Q-Aufnahme vorhanden")
    # Schlüssel über die Aufnahme, nicht die Treffer-ID: IDs werden nach dem Löschen neu vergeben
    key = (row["recording_path"], side, int(offset_hz), mode, int(nr))
    with _demod_lock:
        if key in _demod_cache:
            _demod_cache.move_to_end(key)
            return _demod_cache[key]
    with wave.open(row["recording_path"], "rb") as w:
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    samples = np.frombuffer(frames, dtype="<i2")
    samples = samples[: len(samples) // 2 * 2].reshape(-1, 2).astype(np.float64) / 32768.0
    i, q = samples[:, 0], samples[:, 1]
    i -= i.mean(); q -= q.mean()
    # Aufnahmen vom RTL-SDR sind bereits konjugationsfrei; die Konjugation gilt nur fuer PMSDR-Aufnahmen
    conj = cfg.get("iq_conjugate", False) and float(row["center_hz"]) < 30e6
    z = (i - 1j * q) if conj else (i + 1j * q)
    ppm = float(cfg.get("frequency_correction_ppm", 0.0)) if float(row["center_hz"]) < 30e6 else 0.0
    cc = float(row["center_hz"]) * (1.0 + ppm * 1e-6)
    offset = float(row["rf_hz"]) + float(offset_hz) - cc
    n = np.arange(len(z))
    bb = z * np.exp(-2j * np.pi * offset * n / rate)
    audio, out_rate = demodulate(bb, rate, mode, side)
    if nr and mode != "wfm":
        import denoise
        audio = denoise.denoise(audio, out_rate, int(nr))
    # Lautstärke nach dem 99,5-%-Pegel statt nach der lautesten Spitze: ein einzelner
    # Knacks (Filter-Einschwingen, Tastklick) machte bisher die ganze Aufnahme leise
    if len(audio):
        edge = min(len(audio) // 20, int(out_rate * 0.05))
        if edge > 0:
            audio[:edge] *= np.linspace(0, 1, edge); audio[-edge:] *= np.linspace(1, 0, edge)
        ref = float(np.percentile(np.abs(audio), 99.5))
        if ref > 0:
            audio = np.clip(audio / ref * 0.7, -0.98, 0.98)
    pcm = (audio * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(int(out_rate))
        w.writeframes(pcm)
    data = buf.getvalue()
    with _demod_lock:
        _demod_cache[key] = data
        while len(_demod_cache) > 12:
            _demod_cache.popitem(last=False)
    return data


# ------------------------------------------------------------------ Spektrumbild auf Anfrage

def spectrum_png(peak_id, lang="de"):
    """Spektrum um den Treffer aus der I/Q-Aufnahme, als PNG; wird in reports/ abgelegt."""
    db = db_connect()
    row = db.execute("""SELECT p.rf_hz, p.recording_path, p.carrier_delta_hz, p.mode, p.sideband,
                               COALESCE(p.rec_center_hz, s.center_hz) AS center_hz, s.band
                        FROM peak p JOIN scan s ON s.id=p.scan_id WHERE p.id=?""", (peak_id,)).fetchone()
    db.close()
    if not row or not row["recording_path"] or not Path(row["recording_path"]).exists():
        raise ValueError("Keine I/Q-Aufnahme vorhanden")
    en = lang == "en"
    out = REPORTS / f"{Path(row['recording_path']).stem}_spectrum3{'_en' if en else ''}.png"
    if out.exists():
        return out
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from scipy.signal import welch
    cfg = scanner_config()
    with wave.open(row["recording_path"], "rb") as w:
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    sm = np.frombuffer(frames, dtype="<i2")
    sm = sm[: len(sm) // 2 * 2].reshape(-1, 2).astype(np.float64) / 32768.0
    i, q = sm[:, 0] - sm[:, 0].mean(), sm[:, 1] - sm[:, 1].mean()
    hf = float(row["center_hz"]) < 30e6
    z = (i - 1j * q) if (cfg.get("iq_conjugate", False) and hf) else (i + 1j * q)
    ppm = float(cfg.get("frequency_correction_ppm", 0.0)) if hf else 0.0
    cc = float(row["center_hz"]) * (1.0 + ppm * 1e-6)
    f, P = welch(z, fs=rate, nperseg=4096, return_onesided=False)
    f = np.fft.fftshift(f); P = np.fft.fftshift(P)
    pdb = 10 * np.log10(P + 1e-20)
    pdb -= np.median(pdb)
    mhz = (cc + f) / 1e6
    rf = float(row["rf_hz"]) / 1e6
    fig, ax = plt.subplots(figsize=(8, 3.4), dpi=130)
    fig.patch.set_facecolor("#182230"); ax.set_facecolor("#0f1620")
    ax.plot(mhz, pdb, color="#38bdf8", linewidth=.6)
    half = 0.006 if rate <= 50000 else rate / 2e6 * 0.9
    lo, hi = rf - half, rf + half
    ax.set_xlim(max(mhz.min(), lo), min(mhz.max(), hi))
    m = (mhz >= lo) & (mhz <= hi)
    ax.set_ylim(min(-10, pdb[m].min() - 3) if m.any() else -10, (pdb[m].max() + 5) if m.any() else 40)
    ax.axvline(rf, color="#f5b942", linestyle="--", linewidth=1, label="hit" if en else "Treffer")
    sb = (row["sideband"] or "").upper()
    if row["mode"] in ("voice", "sstv") and sb in ("USB", "LSB"):
        d = (row["carrier_delta_hz"] or 0.0) / 1e6
        a, b = (rf + d - 0.003, rf + d) if sb == "LSB" else (rf + d, rf + d + 0.003)
        ax.axvspan(a, b, color="#2dd4bf", alpha=.15, label=f"{'voice window' if en else 'Sprachfenster'} {sb}")
    ax.ticklabel_format(useOffset=False, style="plain", axis="x")
    ax.set_xlabel("MHz", color="#9aaabc"); ax.set_ylabel("SNR (dB)", color="#9aaabc")      # 0 dB = Rauschboden (Median)
    ax.tick_params(colors="#9aaabc", labelsize=8)
    for sp in ax.spines.values():
        sp.set_color("#2c3a4d")
    ax.grid(True, color="#2c3a4d", linewidth=.5)
    ml = MODE_LABELS.get(row['mode'] or '', row['mode'] or '?')
    if en:
        ml = {"Sprache": "voice", "Bake": "beacon", "Satellit": "satellite", "Daten": "data", "Dauerträger": "carrier",
              "Störung/Breitband": "noise", "UKW-Rundfunk": "FM broadcast", "Relais": "repeater", "Digitalfunk": "digital voice",
              "Unbekannt": "unknown"}.get(ml, ml)
    ax.set_title(f"{row['band']}  {rf:.6f} MHz  ({ml})", color="#e7edf4", fontsize=10)
    ax.legend(loc="upper right", fontsize=7, facecolor="#1f2b3b", edgecolor="#2c3a4d", labelcolor="#e7edf4")
    fig.tight_layout()
    REPORTS.mkdir(exist_ok=True)
    fig.savefig(out, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out


# ------------------------------------------------------------------ Live-Empfang

_live = None
_live_lock = threading.Lock()


def live_presets():
    """Kanallisten fuer den Suchlauf aus dem Bandplan (diskrete Frequenzen oder Raster)."""
    out = []
    plan = bandplan()
    def add(name, band, chans, mode="nfm"):
        legal = next((x.get("legal", "amateur") for x in plan if x["name"] == band), "amateur")
        out.append({"id": f"{band}:{name}", "name": name, "band": band, "mode": mode, "legal": legal,
                    "channels_hz": sorted(set(int(c) for c in chans))})
    for b in plan:
        for sg in b["segments"]:
            if b["device"] != "rtlsdr":
                continue
            if sg.get("frequencies_hz") and sg["mode"] in ("fm", "pocsag", "aprs", "am", "sat"):
                add(sg["label"], b["name"], sg["frequencies_hz"], "am" if sg["mode"] == "am" else "nfm")
            elif sg["mode"] in ("relais", "fm") and sg.get("ranges"):
                chans = []
                for a, z in sg["ranges"]:
                    f = a
                    while f <= z:
                        chans.append(f)
                        f += 12500
                add(sg["label"], b["name"], chans, "nfm")
    return out


def live_status():
    with _live_lock:
        if _live and _live.running:
            return dict(_live.status(), active=True)
    return {"active": False, "running": False, "text": "aus", "level_db": -120, "listeners": 0}


_sat_cache = {"t": 0.0, "key": None, "data": None}
_sat_lock = threading.Lock()

SAT_STATE_TEXT = {"running": "nimmt auf", "evaluating": "wird ausgewertet", "cancelled": "abgebrochen", "done": "aufgenommen", "error": "Fehler", "skipped": "übersprungen"}


def sat_passes():
    """Überflüge der gewählten Satelliten mit Aufnahmeplan und Ergebnis (2 min zwischengespeichert,
    nur ein Thread rechnet)."""
    import satellites as SAT
    st = load_settings()
    seg = (st.get("selected_segments") or {}).get("Satelliten") or ["all"]
    ids = [x["id"] for x in SAT.SATELLITES] if "all" in seg else [x for x in seg if x in SAT.BY_ID]
    eff = (st.get("schedule_active") or {}).get("bands") if st.get("schedule_active") else st.get("selected_bands")
    chip = "Satelliten" in (eff or [])
    # Satelliten nur mit dem RTL-SDR (gewähltes Gerät = angeschlossene Antenne)
    on = chip and st.get("active_device") == "rtlsdr"
    prio = st.get("sat_prio") or {}
    key = (tuple(ids), st.get("my_locator", ""), on, json.dumps(prio, sort_keys=True))
    with _sat_lock:
        if _sat_cache["key"] == key and time.time() - _sat_cache["t"] < 120:
            res = _sat_cache["data"]
        else:
            try:
                res = (SAT.upcoming(ids, st.get("my_locator", ""), hours=24, past_s=12 * 3600, prio=prio) if on
                       else {"passes": [], "recordings": []})
            except Exception as e:
                res = {"passes": [], "problem": f"{type(e).__name__}: {e}"}
            res["enabled"] = on
            res["paused_device"] = chip and not on
            res["satellites"] = [{"id": x["id"], "name": x["name"], "label": x["label"], "center_hz": x["center_hz"],
                                  "prio": x["prio"], "norad": x["norad"], "group": x.get("group", "")}
                                 for x in SAT.SATELLITES]
            # je Satellit: letzte Aufnahme und Treffer der letzten 14 Tage
            stt, now_ = SAT.read_stats(), time.time()
            res["stats"] = {}
            for k, e in stt.items():
                ps = e.get("passes", [])
                rec = [x for x in ps if x[0] > now_ - 14 * 86400]
                res["stats"][k] = {"last": max((x[0] for x in ps), default=None), "last_hit": e.get("last_hit"),
                                   "passes_14d": len(rec), "hits_14d": sum(x[1] for x in rec)}
            _sat_cache.update(t=time.time(), key=key, data=res)
    # Ergebnisse (laufend/fertig) immer frisch dazu – ohne den Zwischenspeicher zu verändern
    results = SAT.read_results()
    now = time.time()
    passes = []
    for p in res.get("passes", []):
        q = dict(p)
        r = results.get(p["key"]) or _near_result(results, p["key"], p.get("aos"), p.get("los"))
        if r and r.get("state") in ("running", "evaluating") and now > p["los"] + 1800:
            # Aufnahme bzw. Auswertung nie fertig geworden (Prozess beendet, z. B. Speicher voll)
            q["result"] = dict(r, state="error", text=SAT_STATE_TEXT["error"],
                               error="Auswertung nicht beendet" if r.get("state") == "evaluating" else "Aufnahme nicht beendet")
        elif r:
            q["result"] = dict(r, text=SAT_STATE_TEXT.get(r.get("state"), r.get("state")))
        elif p["los"] < now:
            q["result"] = {"state": "missed", "text": "nicht aufgenommen" if p.get("rec_start") else "übersprungen"}
        passes.append(q)
    return dict(res, passes=passes, nesdr=bool((st.get("hardware") or {}).get("rtlsdr", {}).get("present")))


def _near_result(results, key, aos=None, los=None, tol_min=3):
    """Ergebnis eines Überflugs auch unter einem anderen Schlüssel finden (NORAD_Minute).
    Der Schlüssel entsteht aus der Aufgangszeit, wie der Scheduler sie gerade kennt: rechnet er den
    Plan während eines laufenden Überflugs neu, beginnt der Überflug für ihn „jetzt“ – das Ergebnis
    steht dann z. B. unter 13:49 statt unter 13:42. Neue Bahndaten verschieben die Zeit ebenfalls.
    Deshalb: gleicher Satellit und Schlüsselzeit innerhalb des Überflugs (oder ±3 min)."""
    try:
        norad, minute = key.rsplit("_", 1)
        minute = int(minute)
    except ValueError:
        return None
    lo, hi = minute - tol_min, minute + tol_min
    if aos and los:
        lo, hi = min(lo, int(aos // 60) - tol_min), max(hi, int(los // 60))
    best = None
    for k, v in results.items():
        try:
            n2, m2 = k.rsplit("_", 1)
            m2 = int(m2)
        except ValueError:
            continue
        if n2 == norad and lo <= m2 <= hi:
            d = abs(m2 - minute)
            # ein fertiges Ergebnis schlägt ein abgebrochenes, sonst das zeitlich nächste
            rank = (0 if v.get("state") == "done" else 1, d)
            if best is None or rank < best[0]:
                best = (rank, v)
    return best[1] if best else None


def _gain_value(v):
    """Verstärkung für rtl_sdr: 0 = automatisch, sonst 0…49,6 dB."""
    g = float(v)
    return 0 if g <= 0 else max(0.9, min(49.6, g))


def live_start(data):
    global _live
    with _live_lock:
        if scan_running() and data.get("stop_scan"):
            stop_scan_or_pass()
            for _ in range(40):                      # bis der Stick frei ist (Scan beendet sich, Überflug gibt frei)
                if not scan_running():
                    break
                time.sleep(0.5)
        if scan_running():
            raise ValueError("Gerade läuft ein Scan – bitte warten oder abbrechen lassen")
        if _live and _live.running:
            _live.stop()
        settings = load_settings()
        device = data.get("device") or settings["active_device"]
        mode = str(data.get("mode", "nfm"))
        if mode not in ("nfm", "wfm", "am", "usb", "lsb"):
            mode = "nfm"
        freq = int(float(data.get("freq_hz", 145500000)))
        if not data.get("seek"):
            check_not_restricted(freq)
        scan = None
        if data.get("seek") in ("up", "down"):
            if device != "rtlsdr":
                raise ValueError("Suchlauf geht nur mit dem RTL-SDR")
            thr = float(data.get("squelch_db", 0) or 0)
            scan = {"kind": "seek", "name": "Suchlauf " + ("▲" if data["seek"] == "up" else "▼"),
                    "dir": 1 if data["seek"] == "up" else -1,
                    "step": int(float(data.get("step_hz", 12500))),
                    "threshold_db": thr if thr > 0 else 10.0, "hold_s": float(data.get("hold_s", 3)),
                    "blocked": restricted_ranges()}
        elif data.get("preset"):
            for p in live_presets():
                if p["id"] == data["preset"]:
                    scan = {"name": p["name"], "channels_hz": p["channels_hz"],
                            "threshold_db": float(data.get("threshold_db", 12)), "hold_s": float(data.get("hold_s", 3))}
                    mode = p["mode"]
                    freq = p["channels_hz"][0]
        # beim Suchlauf keine rtl_fm-Rauschsperre: der Regler dient dort als Suchlauf-Schwelle
        sq = 0.0 if (scan and scan.get("kind") == "seek") else float(data.get("squelch_db", 0))
        _live = LIVE.LiveSession(scanner_config(), device, freq, mode, sq, scan)
        if data.get("gain") not in (None, ""):
            _live.gain = _gain_value(data.get("gain"))
        _live.start()
        APPLOG.log("INFO", "live", "start", device=device, freq_hz=freq, mode=mode, scan=scan["name"] if scan else None)
        time.sleep(0.8)
        st = _live.status()
        if not _live.running or st["text"].startswith("Fehler") or "beendet" in st["text"]:
            msg = st["text"]
            _live.stop()
            _live = None
            APPLOG.log("ERROR", "live", "start_failed", error=msg)
            raise ValueError(msg)
        return st


def live_stop():
    global _live
    with _live_lock:
        if _live:
            _live.stop()
            _live = None
            APPLOG.log("INFO", "live", "stop")
    return {"active": False}


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "PMSDR/2"

    def log_message(self, fmt, *args):  # ruhig bleiben
        if "/api/status" in (args[0] if args else ""):
            return
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # ---- Antworten
    # ---------------- Zugangsschutz (bin/auth.py)
    PUBLIC = ("/login", "/api/login")

    def client_ip(self):
        return self.client_address[0]

    def gate(self, path):
        """True = weiter. Sonst ist die Antwort schon geschickt (Umleitung bzw. 401)."""
        if path in self.PUBLIC or path.startswith("/static/"):
            return True
        if AUTH.allowed(self.client_ip(), self.headers.get("Cookie")):
            return True
        if path in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_json({"ok": False, "error": "Anmeldung nötig", "login": True}, 401)
        return False

    def set_session_cookie(self):
        val, age = AUTH.new_session()
        self._cookie = f"{AUTH.COOKIE}={val}; Max-Age={age}; Path=/; HttpOnly; SameSite=Strict"

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        if getattr(self, "_cookie", None):
            self.send_header("Set-Cookie", self._cookie)
            self._cookie = None
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, download_name=None, cache=True):
        path = Path(path)
        if not path.is_file():
            self.send_json({"ok": False, "error": "Datei nicht gefunden"}, 404)
            return
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw or b"{}")
        except Exception:
            return {}

    # ---- GET
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            if not self.gate(path):
                return
            if path == "/login":
                return self.send_file(WEB / "login.html", cache=False)
            if path == "/api/auth":
                st = AUTH.status()
                return self.send_json(dict(st, trusted_here=AUTH.trusted(self.client_ip()),
                                           logged_in=AUTH.valid_session(self.headers.get("Cookie")), ip=self.client_ip()))
            if path in ("/", "/index.html"):
                return self.send_file(WEB / "index.html", cache=False)
            if path.startswith("/static/"):
                name = Path(path[len("/static/"):]).name
                return self.send_file(WEB / name, cache=False)
            if path.startswith("/files/"):
                name = Path(path[len("/files/"):]).name
                return self.send_file(REPORTS / name)
            if path == "/api/status":
                return self.send_json(load_status())
            if path == "/api/sat/passes":
                return self.send_json(sat_passes())
            if path == "/api/settings":
                return self.send_json(load_settings())
            if path == "/api/bandplan":
                return self.send_json({"bands": bandplan(), "modes": MODE_LABELS})
            if path == "/api/hits":
                return self.send_json({"hits": list_hits(qs), "server_time": now_iso()})
            if path == "/api/runs":
                return self.send_json(list_runs(max(1, min(int(qs.get("limit", 25)), 500)),
                                                max(0, int(qs.get("offset", 0)))))
            if path == "/api/scan-estimate":
                return self.send_json(scan_estimate() or {})
            if path == "/api/stations":
                return self.send_json(stations(int(qs.get("days", 30) or 0), qs.get("q", ""), qs.get("band", ""),
                                               max(1, min(int(qs.get("limit", 100)), 1000)), max(0, int(qs.get("offset", 0)))))
            if path == "/api/fmstations":
                return self.send_json(fm_stations(int(qs.get("days", 0) or 0)))
            if path == "/api/activity":
                return self.send_json(activity(max(1, min(int(qs.get("days", 30) or 30), 365))))
            if path == "/api/stations.adi":
                days = int(qs.get("days", 30) or 0)
                body = stations_adif(days, qs.get("q", ""), qs.get("band", "")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="hamscanpi_swl_{datetime.now(timezone.utc).strftime("%Y%m%d")}.adi"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/system":
                return self.send_json(HK.system_status())
            if path == "/api/scan-summary":
                return self.send_json(scan_summary())
            if path == "/api/stats":
                return self.send_json(stats())
            if path == "/api/deletion-log":
                return self.send_json({"entries": deletion_log()})
            if path == "/api/delete-preview":
                ids, kept = delete_candidates(int(qs.get("days", 7)))
                return self.send_json({"delete": len(ids), "protected": len(kept)})
            if path == "/api/download-iq":
                db = db_connect()
                row = db.execute("SELECT recording_path FROM peak WHERE id=?", (int(qs.get("peak_id", 0)),)).fetchone()
                db.close()
                if not row or not row[0]:
                    return self.send_json({"ok": False, "error": "Keine Aufnahme"}, 404)
                return self.send_file(row[0], download_name=Path(row[0]).name)
            if path == "/api/demod":
                side = "USB" if qs.get("side", "USB").upper() == "USB" else "LSB"
                off = max(-100000.0, min(100000.0, float(qs.get("offset_hz", 0) or 0)))
                dmode = qs.get("mode", "ssb")
                if dmode not in ("ssb", "am", "nfm", "wfm"):
                    dmode = "ssb"
                nr = int(qs.get("nr", 0) or 0)
                data = demod_on_demand(int(qs["peak_id"]), side, off, dmode, nr if nr in (0, 1, 2) else 0)
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                if qs.get("download") == "1":
                    db = db_connect()
                    r = db.execute("SELECT p.rf_hz, s.band FROM peak p JOIN scan s ON s.id=p.scan_id WHERE p.id=?",
                                   (int(qs["peak_id"]),)).fetchone()
                    db.close()
                    name = f"{r['band']}_{r['rf_hz']/1e6:.4f}MHz_{dmode.upper() if dmode != 'ssb' else side}_{int(off):+d}Hz.wav" if r else "audio.wav"
                    self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/spectrum":
                return self.send_file(spectrum_png(int(qs["peak_id"]), "en" if qs.get("lang") == "en" else "de"))
            if path == "/api/logs":
                lvl = qs.get("level") or None
                lim, off = max(1, min(int(qs.get("limit", 100)), 2000)), max(0, int(qs.get("offset", 0)))
                rows = APPLOG.read(limit=lim + 1, level=lvl, component=qs.get("component") or None,
                                   since=qs.get("since") or None, q=qs.get("q") or None, offset=off)
                return self.send_json({"entries": rows[:lim], "has_more": len(rows) > lim, "offset": off, "limit": lim})
            if path == "/api/logs/summary":
                return self.send_json(APPLOG.summary(int(qs.get("hours", 24))))
            if path == "/api/live/waterfall":
                w = _live.wf if (_live and _live.running) else {"seq": 0, "row": []}
                return self.send_json(dict(w, active_hz=getattr(_live, "active_hz", 0) if _live else 0))
            if path == "/api/live/status":
                return self.send_json(live_status())
            if path == "/api/live/presets":
                return self.send_json({"presets": live_presets()})
            if path in ("/api/live/audio", "/api/live/stream.wav"):
                with _live_lock:
                    sess = _live if (_live and _live.running) else None
                if not sess:
                    return self.send_json({"ok": False, "error": "Kein Live-Empfang aktiv"}, 409)
                q = sess.subscribe()
                as_wav = path.endswith(".wav")
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav" if as_wav else "application/octet-stream")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Audio-Rate", str(LIVE.OUT_RATE))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    if as_wav:
                        # WAV-Kopf mit "unendlicher" Länge – Browser spielen den Strom fortlaufend
                        import struct
                        r = LIVE.OUT_RATE
                        self.wfile.write(b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt " +
                                         struct.pack("<IHHIIHH", 16, 1, 1, r, r * 2, 2, 16) +
                                         b"data" + struct.pack("<I", 0xFFFFFFFF))
                    while sess.running:
                        try:
                            chunk = q.get(timeout=2)
                        except Exception:
                            continue
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    sess.unsubscribe(q)
                return
            if path == "/api/log":
                log = BASE / "log" / "scan-cycle.log"
                lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-int(qs.get("n", 80)):] if log.exists() else []
                return self.send_json({"lines": lines})
            self.send_json({"ok": False, "error": "Nicht gefunden"}, 404)
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

    # ---- POST
    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if not self.gate(path):
                return
            data = self.read_body()
            ip = self.client_ip()
            if path == "/api/login":
                if AUTH.login_blocked(ip):
                    APPLOG.log("WARN", "webapp", "login_blocked", ip=ip)
                    return self.send_json({"ok": False, "error": "Zu viele Fehlversuche – bitte 5 Minuten warten"}, 429)
                if not AUTH.enabled() or AUTH.login_attempt(ip, str(data.get("password", ""))):
                    self.set_session_cookie()
                    APPLOG.log("INFO", "webapp", "login", ip=ip)
                    return self.send_json({"ok": True})
                APPLOG.log("WARN", "webapp", "login_failed", ip=ip)
                return self.send_json({"ok": False, "error": "Passwort falsch"}, 401)
            if path == "/api/logout":
                self._cookie = f"{AUTH.COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict"
                return self.send_json({"ok": True})
            if path == "/api/auth/password":
                if AUTH.enabled() and not AUTH.check_password(str(data.get("current", ""))):
                    return self.send_json({"ok": False, "error": "Aktuelles Passwort falsch"}, 403)
                AUTH.set_password(str(data.get("new", "")))
                self.set_session_cookie()                  # diese Sitzung bleibt angemeldet
                APPLOG.log("INFO", "webapp", "password_set", ip=ip)
                return self.send_json({"ok": True, "auth": AUTH.status()})
            if path == "/api/auth/off":
                if AUTH.enabled() and not AUTH.check_password(str(data.get("current", ""))):
                    return self.send_json({"ok": False, "error": "Aktuelles Passwort falsch"}, 403)
                AUTH.disable()
                APPLOG.log("WARN", "webapp", "auth_disabled", ip=ip)
                return self.send_json({"ok": True, "auth": AUTH.status()})
            if path == "/api/auth/trusted":
                AUTH.set_trusted(AUTH.parse_networks(data.get("networks", "")))
                return self.send_json({"ok": True, "auth": AUTH.status()})
            if path == "/api/settings":
                return self.send_json({"ok": True, "settings": save_settings(data)})
            if path == "/api/scan-now":
                if live_status().get("active"):
                    return self.send_json({"ok": False, "error": "Live-Empfang läuft – erst stoppen"}, 409)
                st = load_status()
                if st.get("scan_running") or st.get("state") == "queued":
                    return self.send_json({"ok": True, "already_running": True})
                save_status({"state": "queued", "phase": "queued", "progress": 0,
                             "message": "Scan wurde angefordert", "run_id": None,
                             "started_at": None, "updated_at": now_iso()})
                hb = BASE / "data" / "scheduler.heartbeat"
                if hb.exists() and (time.time() - hb.stat().st_mtime) < 60:
                    TRIGGER.touch()
                    return self.send_json({"ok": True})
                # Scheduler tot -> Scan direkt starten, damit der Knopf immer funktioniert
                APPLOG.log("WARN", "webapp", "scheduler_dead_fallback", note="run_cycle.sh direkt gestartet")
                with open(BASE / "log" / "scan-cycle.log", "a", encoding="utf-8") as out:
                    subprocess.Popen(["/usr/bin/flock", "-n", str(LOCKFILE), str(BASE / "bin" / "run_cycle.sh")],
                                     stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
                return self.send_json({"ok": True, "fallback": True})
            if path == "/api/scan-stop":
                # Satellitenaufnahme? Dann die abbrechen (sie wertet das bisher Aufgenommene noch aus)
                import signal
                rec = BASE / "data" / "sat_rec.pid"
                try:
                    rpid = int(rec.read_text().strip())
                    os.kill(rpid, signal.SIGTERM)
                    APPLOG.log("WARN", "webapp", "satellite_stop_requested", pid=rpid)
                    return self.send_json({"ok": True, "what": "satellite"})
                except (OSError, ValueError):
                    rec.unlink(missing_ok=True)
                st = read_json(BASE / "data" / "runtime_status.json", {})
                if st.get("phase") == "satellite" and st.get("state") == "running":
                    return self.send_json({"ok": False, "error": "Der Überflug wird gerade ausgewertet – der Empfänger ist schon frei."}, 409)
                pidf = BASE / "data" / "scan.pid"
                try:
                    pid = int(pidf.read_text().strip())
                except Exception:
                    return self.send_json({"ok": False, "error": "Kein laufender Scan gefunden"}, 409)
                import signal
                try:
                    __import__("os").kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pidf.unlink(missing_ok=True)
                    return self.send_json({"ok": False, "error": "Scanprozess existiert nicht mehr"}, 409)
                APPLOG.log("WARN", "webapp", "scan_stop_requested", pid=pid)
                return self.send_json({"ok": True})
            if path == "/api/telegram-chats":
                # Chat-ID ermitteln: Telegram nach den letzten Nachrichten an den Bot fragen
                tok = str(data.get("token") or "").strip()
                if not tok or "•" in tok:
                    tok = str((read_json(SETTINGS, {}).get("notify") or {}).get("tg_token") or "").strip()
                if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", tok):
                    raise ValueError("Bot-Token fehlt oder hat nicht das Format 123456:ABC…")
                import urllib.request, urllib.error
                try:
                    with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/getUpdates", timeout=15) as r:
                        res = json.loads(r.read().decode("utf-8"))
                except urllib.error.HTTPError as e:
                    raise ValueError("Telegram lehnt den Token ab – bitte prüfen" if e.code in (401, 404)
                                     else f"Telegram: HTTP {e.code}")
                except OSError as e:
                    raise ValueError(f"Telegram nicht erreichbar: {e}")
                chats = {}
                for u in res.get("result", []):
                    m = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
                    c = m.get("chat") or {}
                    if c.get("id") is not None:
                        chats[c["id"]] = {"id": c["id"], "type": c.get("type"),
                                          "name": c.get("title") or " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x)
                                          or c.get("username") or ""}
                return self.send_json({"chats": list(chats.values())})
            if path == "/api/push-test":
                raw = read_json(SETTINGS, {}).get("notify") or {}
                if not NOTIFY.push_on(raw):
                    raise ValueError("Push ist nicht eingerichtet")
                NOTIFY.send_push("HamScanPi", "Testnachricht – Push funktioniert. 73!", raw)
                APPLOG.log("INFO", "notify", "test_push_sent", via=raw.get("push"))
                return self.send_json({"ok": True})
            if path == "/api/notify-test":
                raw = read_json(SETTINGS, {}).get("notify") or {}
                NOTIFY.send_mail("Testnachricht", "Der Mailversand von HamScanPi funktioniert.\n", raw)
                APPLOG.log("INFO", "notify", "test_mail_sent", to=raw.get("mail_to"))
                return self.send_json({"ok": True})
            if path == "/api/live/start":
                return self.send_json({"ok": True, "live": live_start(data)})
            if path == "/api/live/stop":
                return self.send_json({"ok": True, "live": live_stop()})
            if path == "/api/live/tune":
                with _live_lock:
                    if not (_live and _live.running):
                        raise ValueError("Kein Live-Empfang aktiv")
                    if data.get("freq_hz"):
                        check_not_restricted(float(data["freq_hz"]))
                    _live.tune(data.get("freq_hz"), data.get("mode"), data.get("squelch_db"),
                               _gain_value(data["gain"]) if data.get("gain") not in (None, "") else None)
                    return self.send_json({"ok": True, "live": _live.status()})
            if path == "/api/protect-hit":
                set_protected(int(data["peak_id"]), bool(data.get("protected", True)))
                return self.send_json({"ok": True})
            if path == "/api/note":
                set_note(int(data["peak_id"]), data.get("note", ""))
                return self.send_json({"ok": True})
            if path == "/api/delete-hit":
                delete_hit(int(data["peak_id"]))
                return self.send_json({"ok": True})
            if path == "/api/delete-old":
                n, kept = bulk_delete(int(data.get("days", 7)))
                return self.send_json({"ok": True, "deleted": n, "protected": kept})
            if path == "/api/delete-all-unprotected":
                return self.send_json({"ok": True, "deleted": delete_all_unprotected()})
            self.send_json({"ok": False, "error": "Nicht gefunden"}, 404)
        except Exception as e:
            APPLOG.log("WARN", "webapp", "api_error", path=path, error=str(e))
            self.send_json({"ok": False, "error": str(e)}, 400)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    for d in ("data", "log", "recordings", "reports"):           # frische Installation
        (BASE / d).mkdir(parents=True, exist_ok=True)
    _cfg = BASE / "config" / "scanner.json"                      # lokale Kopie des Bandplans anlegen
    if not _cfg.exists() and (BASE / "config" / "scanner.default.json").exists():
        import shutil
        shutil.copy(BASE / "config" / "scanner.default.json", _cfg)
    ensure_schema()

    def housekeeping_loop():
        time.sleep(60)
        while True:
            try:
                HK.run(load_settings(), bulk_delete,
                       lambda level, event, **d: APPLOG.log(level, "housekeeping", event, **d))
            except Exception as e:
                APPLOG.log("WARN", "housekeeping", "housekeeping_failed", error=f"{type(e).__name__}: {e}")
            time.sleep(900)
    threading.Thread(target=housekeeping_loop, daemon=True).start()

    def watch_loop():
        time.sleep(20)
        while True:
            try:
                watch_and_stations_cycle()
            except Exception as e:
                APPLOG.log("WARN", "watch", "watch_failed", error=f"{type(e).__name__}: {e}")
            time.sleep(60)
    threading.Thread(target=watch_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"HamScanPi Webserver auf Port {port}, Dateien aus {WEB}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
