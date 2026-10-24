#!/usr/bin/env python3
"""
Systemzustand und Haushalt – läuft im Webserver alle 15 Minuten.

  * Systemzustand für Einstellungen → System: Speicherkarte, Aufnahmen, Arbeitsspeicher, CPU-Temperatur
    und Drosselung, Uhrzeit-Synchronisation (wichtig für den Doppler), Dienste, Version, Update.
  * Automatisch aufräumen: ungeschützte Treffer älter als N Tage löschen (Einstellung, 0 = aus).
  * Speicherschutz: ab 85 % Belegung Warnung, ab 95 % Fehler (Mail, falls eingerichtet) und keine neuen
    Scans oder Überflüge, bis wieder Platz ist (data/disk_full).
  * Liegengebliebene Satelliten-Rohdateien (recordings/.sat_*.raw) nach 48 h löschen – vorher lassen sie
    sich mit bin/sat_recover.py noch auswerten.
  * Abgebrochene Überflüge erkennen (Aufnahme bzw. Auswertung nie fertig geworden): Fehler im Protokoll,
    damit Mail, und im Dashboard „Fehler“.
"""
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DISK_FLAG = BASE / "data" / "disk_full"
UPDATE_CACHE = BASE / "data" / "update_check.json"
WARN_PCT, FULL_PCT = 85.0, 95.0
RAW_MAX_AGE_S = 48 * 3600
_state = {"disk_level": None, "dir_cache": (0.0, None)}


def version():
    v = "unbekannt"
    try:
        v = (BASE / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        pass
    commit = ""
    try:
        r = subprocess.run(["git", "-C", str(BASE), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=3)
        commit = r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        pass
    return v, commit


def _vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def update_check(force=False):
    """Neueste Version auf GitHub (einmal am Tag). Rückgabe dict(latest, newer) oder {}."""
    try:
        c = json.loads(UPDATE_CACHE.read_text(encoding="utf-8"))
    except Exception:
        c = {}
    if not force and time.time() - c.get("checked", 0) < 86400:
        return c
    url = None
    try:
        r = subprocess.run(["git", "-C", str(BASE), "remote", "get-url", "origin"], capture_output=True, text=True, timeout=3)
        m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", r.stdout)
        if m:
            url = f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/main/VERSION"
    except Exception:
        pass
    url = url or "https://raw.githubusercontent.com/do1gmt/hamscanpi/main/VERSION"
    c = {"checked": time.time(), "url": url}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HamScanPi update check"})
        with urllib.request.urlopen(req, timeout=10) as r:
            c["latest"] = r.read(64).decode("ascii", "replace").strip()
    except Exception as e:
        c["error"] = f"{type(e).__name__}"
    UPDATE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    UPDATE_CACHE.write_text(json.dumps(c), encoding="utf-8")
    return c


def _dir_size(path):
    t, cached = _state["dir_cache"]
    if cached is not None and time.time() - t < 300:
        return cached
    total = n = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
                n += 1
            except OSError:
                pass
    _state["dir_cache"] = (time.time(), (total, n))
    return total, n


def _read(path):
    try:
        return Path(path).read_text().strip()
    except Exception:
        return None


def system_status():
    st = {}
    du = shutil.disk_usage(str(BASE))
    st["disk"] = {"total_gb": round(du.total / 1e9, 1), "free_gb": round(du.free / 1e9, 1),
                  "used_pct": round(100.0 * du.used / du.total, 1)}
    size, n = _dir_size(BASE / "recordings")
    st["recordings"] = {"gb": round(size / 1e9, 2), "files": n}
    raw = list((BASE / "recordings").glob(".sat_*.raw"))
    st["sat_raw"] = {"files": len(raw), "mb": round(sum(p.stat().st_size for p in raw) / 1e6)}
    mem = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            mem[k] = int(v.split()[0]) * 1024
        st["memory"] = {"total_mb": round(mem["MemTotal"] / 1e6), "available_mb": round(mem["MemAvailable"] / 1e6),
                        "swap_used_mb": round((mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)) / 1e6)}
    except Exception:
        st["memory"] = None
    t = _read("/sys/class/thermal/thermal_zone0/temp")
    st["cpu_temp_c"] = round(int(t) / 1000, 1) if t and t.isdigit() else None
    st["throttled"] = None
    if shutil.which("vcgencmd"):
        try:
            r = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3)
            val = int(r.stdout.strip().split("=")[1], 16)
            st["throttled"] = {"now": bool(val & 0xF), "since_boot": bool(val & 0xF0000),
                               "undervoltage": bool(val & 0x1 or val & 0x10000), "raw": hex(val)}
        except Exception:
            pass
    try:
        st["load"] = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        st["load"] = None
    up = _read("/proc/uptime")
    st["uptime_h"] = round(float(up.split()[0]) / 3600, 1) if up else None
    st["time_sync"] = None
    if shutil.which("timedatectl"):
        try:
            r = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                               capture_output=True, text=True, timeout=3)
            st["time_sync"] = r.stdout.strip() == "yes"
        except Exception:
            pass
    services = {}
    for u in ("hamscanpi-web", "hamscanpi-scheduler", "pmsdr-control"):
        try:
            r = subprocess.run(["systemctl", "is-active", u], capture_output=True, text=True, timeout=3)
            services[u] = r.stdout.strip() or "unknown"
        except Exception:
            services[u] = "unknown"
    st["services"] = services
    v, commit = version()
    upd = update_check()
    st["version"] = {"version": v, "commit": commit, "latest": upd.get("latest"),
                     "newer": bool(upd.get("latest") and _vtuple(upd["latest"]) > _vtuple(v)),
                     "check_error": upd.get("error")}
    st["disk_full"] = DISK_FLAG.exists()
    return st


def run(settings, bulk_delete, log):
    """Ein Haushaltslauf. bulk_delete(days) und log(level, event, **data) kommen vom Webserver."""
    # 1. automatisch aufräumen
    days = int(settings.get("cleanup_auto_days") or 0)
    if days > 0:
        try:
            n, kept = bulk_delete(days)
            if n:
                log("INFO", "auto_cleanup", deleted=n, kept_protected=kept, older_than_days=days)
        except Exception as e:
            log("WARN", "auto_cleanup_failed", error=f"{type(e).__name__}: {e}")
    # 2. liegengebliebene Satelliten-Rohdateien
    now = time.time()
    for p in (BASE / "recordings").glob(".sat_*.raw"):
        try:
            if now - p.stat().st_mtime > RAW_MAX_AGE_S:
                mb = round(p.stat().st_size / 1e6)
                p.unlink()
                log("WARN", "sat_raw_deleted", file=p.name, mb=mb,
                    hint="Rohaufnahme eines abgebrochenen Überflugs, älter als 48 h")
        except OSError:
            pass
    # 2b. Wasserfallbilder der Überflüge nach 14 Tagen (die Überflugliste zeigt ohnehin nur 3 Tage)
    for p in (BASE / "reports").glob("*_waterfall.png"):
        try:
            if now - p.stat().st_mtime > 14 * 86400:
                p.unlink()
        except OSError:
            pass
    # 3. Speicherplatz
    du = shutil.disk_usage(str(BASE))
    pct = 100.0 * du.used / du.total
    level = "full" if pct >= FULL_PCT else "warn" if pct >= WARN_PCT else "ok"
    if level != _state["disk_level"]:
        if level == "full":
            log("ERROR", "disk_full", used_pct=round(pct, 1), free_gb=round(du.free / 1e9, 2),
                hint="keine neuen Scans und Überflüge, bis wieder Platz ist – unter Einstellungen → Aufräumen löschen")
        elif level == "warn":
            log("WARN", "disk_low", used_pct=round(pct, 1), free_gb=round(du.free / 1e9, 2))
        elif _state["disk_level"] in ("warn", "full"):
            log("INFO", "disk_ok", used_pct=round(pct, 1))
        _state["disk_level"] = level
    if level == "full":
        DISK_FLAG.write_text(str(round(pct, 1)))
    elif DISK_FLAG.exists():
        DISK_FLAG.unlink()
    # 4. abgebrochene Überflüge
    try:
        import satellites as SAT
        res = SAT.read_results()
        for key, r in res.items():
            if r.get("state") not in ("running", "evaluating"):
                continue
            age = now - float(r.get("updated") or r.get("started") or now)
            limit = 3600 if r.get("state") == "evaluating" else 2 * 3600
            if age > limit:
                what = "Auswertung" if r.get("state") == "evaluating" else "Aufnahme"
                SAT.write_result(key, state="error", error=f"{what} nicht beendet (Prozess abgebrochen, evtl. Speicher)")
                log("ERROR", "pass_aborted", key=key, channels=r.get("channels"), stage=what,
                    hint="Rohdaten lassen sich 48 h lang mit bin/sat_recover.py auswerten")
    except Exception as e:
        log("DEBUG", "pass_check_failed", error=str(e))


def disk_full():
    """Für den Scheduler: True, solange die Speicherkarte fast voll ist."""
    try:
        return DISK_FLAG.exists() and time.time() - DISK_FLAG.stat().st_mtime < 3600
    except OSError:
        return False
