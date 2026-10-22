#!/usr/bin/env python3
"""
Mehrstufiges Protokoll: eine JSON-Zeile pro Ereignis in log/app.jsonl.
Stufen: DEBUG, INFO, WARN, ERROR. Komponenten: scanner, cycle, scheduler, webapp, live, decode, notify.

Python:   from applog import log; log("INFO", "scanner", "run_start", bands=["40m"])
Shell:    applog.py INFO cycle phase_start phase=analyze
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LOGFILE = BASE / "log" / "app.jsonl"
MAX_BYTES = 8 * 1024 * 1024
LEVELS = ["DEBUG", "INFO", "WARN", "ERROR"]


def log(level, component, event, **data):
    level = str(level).upper()
    if level not in LEVELS:
        level = "INFO"
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": level,
           "component": component, "event": event}
    if data:
        rec["data"] = {k: v for k, v in data.items() if v is not None}
    try:
        LOGFILE.parent.mkdir(parents=True, exist_ok=True)
        if LOGFILE.exists() and LOGFILE.stat().st_size > MAX_BYTES:
            LOGFILE.replace(LOGFILE.with_suffix(".1.jsonl"))
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    if level == "ERROR":
        try:
            from notify import notify_error
            notify_error(component, event, rec.get("data", {}))
        except Exception:
            pass
    return rec


def read(limit=500, level=None, component=None, since=None, q=None, offset=0):
    """Neueste Einträge zuerst; offset überspringt so viele passende Einträge (Blättern)."""
    out = []
    skip = int(offset or 0)
    files = [LOGFILE, LOGFILE.with_suffix(".1.jsonl")]
    min_idx = LEVELS.index(level) if level in LEVELS else 0
    for fp in files:
        if not fp.exists():
            continue
        try:
            lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for line in reversed(lines):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if LEVELS.index(r.get("level", "INFO")) < min_idx:
                continue
            if component and r.get("component") != component:
                continue
            if since and r.get("ts", "") < since:
                continue
            if q and q.lower() not in line.lower():
                continue
            if skip:
                skip -= 1
                continue
            out.append(r)
            if len(out) >= limit:
                return out
    return out


def summary(hours=24):
    since = datetime.fromtimestamp(time.time() - hours * 3600, timezone.utc).isoformat(timespec="seconds")
    rows = read(limit=20000, since=since)
    by_level = {l: 0 for l in LEVELS}
    by_component = {}
    errors = []
    for r in rows:
        by_level[r["level"]] = by_level.get(r["level"], 0) + 1
        c = by_component.setdefault(r["component"], {l: 0 for l in LEVELS})
        c[r["level"]] += 1
        if r["level"] in ("ERROR", "WARN") and len(errors) < 20:
            errors.append(r)
    return {"hours": hours, "since": since, "total": len(rows), "by_level": by_level,
            "by_component": by_component, "recent_problems": errors}


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("applog.py LEVEL COMPONENT EVENT [key=value ...]")
        sys.exit(1)
    kv = {}
    for a in sys.argv[4:]:
        if "=" in a:
            k, v = a.split("=", 1)
            kv[k] = v
    log(sys.argv[1], sys.argv[2], sys.argv[3], **kv)
