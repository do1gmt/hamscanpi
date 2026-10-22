#!/usr/bin/env python3

import json
import sqlite3
import subprocess
import time
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent))
from applog import log as applog
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SETTINGS = BASE / "config" / "web_settings.json"
TRIGGER = BASE / "data" / "scan_now.trigger"
DB = BASE / "data" / "scanner.db"
LOCK = BASE / "data" / "scan.lock"
LOG = BASE / "log" / "scheduler.log"

DEFAULT_INTERVAL = 30


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{ts} {msg}\n")


def interval_minutes():
    try:
        with open(SETTINGS, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        value = int(cfg.get("scan_interval_minutes", DEFAULT_INTERVAL))
        if value <= 0:
            return 0          # automatischer Scan ausgeschaltet
        return max(2, min(value, 1440))
    except Exception:
        return DEFAULT_INTERVAL


def live_active_now():
    lk = BASE / "data" / "live.lock"
    return lk.exists() and (time.time() - lk.stat().st_mtime) < 120


def last_scan_time():
    """(Start, Ende) des letzten abgeschlossenen Laufs."""
    try:
        db = sqlite3.connect(DB)
        row = db.execute("""
            SELECT started_at, COALESCE(finished_at, started_at)
            FROM run
            WHERE status IN ('success','failed')
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()
        db.close()
        if not row or not row[0]:
            return None
        return datetime.fromisoformat(row[0]), datetime.fromisoformat(row[1])
    except Exception:
        return None


MIN_PAUSE_S = 30          # zwischen zwei Scans bleibt der Stick mindestens so lange frei


def scan_running():
    result = subprocess.run(
        [
            "/usr/bin/flock",
            "-n",
            str(LOCK),
            "/usr/bin/true"
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # flock bekommt die Sperre nicht:
    # also läuft tatsächlich gerade ein Scan.
    return result.returncode != 0


def run_scan():
    if scan_running():
        log("Scan bereits aktiv - Start verworfen")
        return

    log("Scan gestartet")

    cmd = [
        "/usr/bin/flock",
        "-n",
        str(LOCK),
        str(BASE / "bin" / "run_cycle.sh")
    ]

    with open(BASE / "log" / "scan-cycle.log", "a", encoding="utf-8") as out:
        subprocess.Popen(
            cmd,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True
        )


# ---------------------------------------------------------------- Satelliten
SAT_PID = BASE / "data" / "sat_pass.pid"
_sat = {"checked": 0.0, "key": None, "passes": [], "started": set(), "postponed": None, "problem": None, "procs": []}


def selected_satellites():
    try:
        st = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return [], ""
    _sat["prio"] = st.get("sat_prio") or {}
    if "Satelliten" not in (st.get("selected_bands") or []):
        return [], st.get("my_locator", "")
    seg = (st.get("selected_segments") or {}).get("Satelliten") or ["all"]
    import satellites as SAT
    ids = [s["id"] for s in SAT.SATELLITES] if "all" in seg else [x for x in seg if x in SAT.BY_ID]
    return ids, st.get("my_locator", "")


def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(") ", 1)[1][0] not in "ZX"      # Zombie zählt nicht
    except Exception:
        return False


def sat_pass_active():
    try:
        return _alive(int(SAT_PID.read_text()))
    except Exception:
        return False


def next_passes():
    """Plan alle 10 min neu rechnen – sofort, wenn sich Auswahl oder Locator ändern."""
    ids, loc = selected_satellites()
    key = (tuple(ids), loc, json.dumps(_sat.get("prio") or {}, sort_keys=True))
    if time.time() - _sat["checked"] < 600 and key == _sat["key"]:
        return _sat["passes"]
    _sat["checked"], _sat["key"] = time.time(), key
    if not ids:
        _sat["passes"] = []
        return []
    try:
        import satellites as SAT
        from devices import detect_hardware
        res = SAT.upcoming(ids, loc, hours=8, past_s=0, prio=_sat.get("prio"))
        # geplant wird je Aufnahme (eine Aufnahme kann mehrere Satelliten mitschneiden)
        _sat["passes"] = [dict(key=r["key"], keys=r["keys"], name=r["name"], channels=r["channels"],
                               rec_start=r["start"], rec_end=r["end"], prio=r["prio"], max_el=r["max_el"],
                               status="shared" if len(r["keys"]) > 1 else "single")
                          for r in res.get("recordings", [])]
        prob = res.get("problem")
        if prob and prob != _sat["problem"]:
            applog("WARN", "scheduler", "satellite_problem", problem=prob)
        _sat["problem"] = prob
        if not detect_hardware()["rtlsdr"]["present"]:
            if _sat["problem"] != "nesdr":
                applog("WARN", "scheduler", "satellite_problem", problem="RTL-SDR nicht angeschlossen – keine Aufnahme")
            _sat["problem"] = "nesdr"
            _sat["passes"] = []
    except Exception as e:
        import traceback
        applog("WARN", "scheduler", "satellite_failed", error=f"{type(e).__name__}: {e}",
               where=traceback.format_exc().strip().splitlines()[-3:])
        _sat["passes"] = []
    return _sat["passes"]


def stop_running_scan(reason):
    pidf = BASE / "data" / "scan.pid"
    try:
        pid = int(pidf.read_text())
        import os
        import signal
        os.kill(pid, signal.SIGTERM)
        applog("WARN", "scheduler", "scan_stopped", reason=reason)
    except Exception:
        pass
    for _ in range(30):
        if not scan_running():
            return True
        time.sleep(1)
    return False


def start_sat_pass(p):
    """True, wenn der Überflug erledigt ist (gestartet oder endgültig übersprungen);
    False = Stick noch belegt, im nächsten Durchlauf (10 s) erneut versuchen."""
    import satellites as SAT
    if p["key"] in _sat["started"]:
        return True
    if live_active_now():
        _sat["started"].add(p["key"])
        applog("WARN", "scheduler", "satellite_skipped", sat=p["name"], reason="Live-Empfang läuft")
        for k in p.get("keys", [p["key"]]):
            SAT.write_result(k, state="skipped", error="Live-Empfang lief")
        return True
    if sat_pass_active():
        return False
    if scan_running():
        if p["prio"] <= SAT.OPPORTUNISTIC_PRIO:
            return False                # Bildsatelliten: nur bei freiem Stick, Scan nicht abbrechen
        stop_running_scan(f"Überflug {p['name']}")
    if scan_running():
        return False
    _sat["started"].add(p["key"])
    applog("INFO", "scheduler", "satellite_start", sat=p["name"], channels=p["channels"], max_el=p["max_el"],
           plan=p["status"])
    cmd = ["/usr/bin/flock", "-n", str(LOCK), "python3", str(BASE / "bin" / "sat_pass.py"),
           "--sats", ",".join(p["channels"]), "--until", str(p["rec_end"]), "--keys", ",".join(p.get("keys", [p["key"]]))]
    with open(BASE / "log" / "satellite.log", "a", encoding="utf-8") as out:
        pr = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    SAT_PID.write_text(str(pr.pid))
    _sat["procs"] = [x for x in _sat["procs"] if x.poll() is None] + [pr]   # beendete abholen
    return True


for _d in ("data", "log", "recordings", "reports"):              # frische Installation
    (BASE / _d).mkdir(parents=True, exist_ok=True)
_cfg = BASE / "config" / "scanner.json"                          # lokale Kopie des Bandplans anlegen
if not _cfg.exists() and (BASE / "config" / "scanner.default.json").exists():
    import shutil
    shutil.copy(BASE / "config" / "scanner.default.json", _cfg)
log("Scheduler gestartet")

while True:
    try:
        (BASE / "data" / "scheduler.heartbeat").touch()

        # manueller Scan
        if TRIGGER.exists():
            try:
                TRIGGER.unlink()
            except FileNotFoundError:
                pass

            if live_active_now():
                applog("WARN", "scheduler", "trigger_ignored", reason="live")
            else:
                applog("INFO", "scheduler", "scan_start", reason="trigger")
                run_scan()

        # automatischer Scan: „alle N min“ von Start zu Start; dauert ein Scan länger,
        # folgt der nächste nach MIN_PAUSE_S
        last = last_scan_time()
        interval = interval_minutes()
        due = False
        if interval == 0:
            due = False
        elif last is None:
            due = True
        else:
            started, finished = last
            now = datetime.now(timezone.utc)
            due = ((now - started).total_seconds() >= interval * 60
                   and (now - finished).total_seconds() >= MIN_PAUSE_S)

        # Satellitenüberflug (Vorrang vor dem Scan). Wer bei Überschneidungen wann aufnimmt,
        # steht schon im Plan (satellites.make_plan).
        now_t = time.time()
        soon = None
        for p in next_passes():
            if p["key"] in _sat["started"]:
                continue
            if p["rec_start"] <= now_t < p["rec_end"] - 60:
                start_sat_pass(p)
                break
            if now_t < p["rec_start"]:
                import satellites as SAT
                if p["prio"] > SAT.OPPORTUNISTIC_PRIO:      # nur für wichtige Überflüge Scans verschieben
                    soon = p
                    break
        if due and soon and soon["rec_start"] - now_t < 300:
            if _sat["postponed"] != soon["key"]:
                _sat["postponed"] = soon["key"]
                applog("INFO", "scheduler", "scan_postponed", reason=f"Überflug {soon['name']} in "
                       f"{int((soon['rec_start'] - now_t) // 60)} min")
            due = False
        if due and sat_pass_active():
            due = False

        live_lock = BASE / "data" / "live.lock"
        live = live_lock.exists() and (time.time() - live_lock.stat().st_mtime) < 120
        if due and not scan_running() and not live:
            applog("INFO", "scheduler", "scan_start", reason="interval", interval_min=interval)
            run_scan()

        time.sleep(10)

    except Exception as exc:
        import traceback
        applog("ERROR", "scheduler", "loop_exception", error=f"{type(exc).__name__}: {exc}",
               where=traceback.format_exc().strip().splitlines()[-2:])
        time.sleep(10)
