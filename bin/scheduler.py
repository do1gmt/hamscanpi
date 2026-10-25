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
_sat = {"checked": 0.0, "key": None, "passes": [], "started": set(), "postponed": None, "problem": None, "procs": [],
        "pending": {}, "blocked": {}}


def selected_satellites():
    try:
        st = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return [], ""
    _sat["prio"] = st.get("sat_prio") or {}
    try:
        from scan_plan import effective_settings
        st = effective_settings(st)                 # aktiver Zeitplan bestimmt auch die Satelliten
    except Exception:
        pass
    if "Satelliten" not in (st.get("selected_bands") or []):
        return [], st.get("my_locator", "")
    # Das gewählte Gerät steht für die angeschlossene Antenne: Satelliten nur, wenn es der
    # RTL-SDR ist (auch wenn der Stick steckt, hängt bei gewählter PMSDR die KW-Antenne dran)
    try:
        from devices import detect_hardware, resolve_device
        choice = "rtlsdr" if st.get("active_device") == "rtlsdr" else "pmsdr"
        if resolve_device(choice, detect_hardware()) != "rtlsdr":
            if _sat.get("device_note") != "pmsdr":
                applog("INFO", "scheduler", "satellite_paused", reason="PMSDR gewählt – Antenne am Stick nicht angeschlossen")
            _sat["device_note"] = "pmsdr"
            return [], st.get("my_locator", "")
    except Exception:
        pass
    _sat["device_note"] = "rtlsdr"
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
    key = (tuple(ids), loc, json.dumps(_sat.get("prio") or {}, sort_keys=True), _sat.get("device_note"))
    if time.time() - _sat["checked"] < 600 and key == _sat["key"]:
        return _sat["passes"]
    _sat["checked"], _sat["key"] = time.time(), key
    if not ids:
        _sat["passes"] = []
        return []
    try:
        import satellites as SAT
        from devices import detect_hardware
        # past_s: laufende Überflüge behalten ihre echte Aufgangszeit (und damit ihren Schlüssel),
        # auch wenn der Plan mitten im Überflug neu gerechnet wird
        res = SAT.upcoming(ids, loc, hours=8, past_s=1800, prio=_sat.get("prio"))
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


def typical_scan_seconds():
    """Typische Dauer eines Scans: Median der letzten erfolgreichen Läufe (sonst 2 min)."""
    try:
        db = sqlite3.connect(DB, timeout=5)
        rows = [r[0] for r in db.execute("SELECT duration_s FROM run WHERE status='success' AND duration_s > 0 "
                                         "ORDER BY id DESC LIMIT 5")]
        db.close()
        return float(sorted(rows)[len(rows) // 2]) if rows else 120.0
    except Exception:
        return 120.0


def scan_starving(interval):
    """Kam seit langem kein Scan mehr dran (viele Bildsatelliten hintereinander)? Dann scannen,
    auch wenn ein Bildsatellit kurz danach kommt – er wird dann eben übersprungen."""
    last = last_scan_time()
    if last is None:
        return True
    limit = max(30 * 60, 3 * max(interval, 1) * 60)
    return (datetime.now(timezone.utc) - last[0]).total_seconds() > limit


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
    try:
        import housekeeping as HK
        if HK.disk_full():
            _sat["started"].add(p["key"])
            applog("WARN", "scheduler", "satellite_skipped", sat=p["name"], reason="Speicherkarte fast voll")
            for k in p.get("keys", [p["key"]]):
                SAT.write_result(k, state="skipped", error="Speicherkarte fast voll")
            return True
    except ImportError:
        pass
    if scan_running():
        if p["prio"] <= SAT.OPPORTUNISTIC_PRIO:
            return False                # Bildsatelliten: nur bei freiem Stick, Scan nicht abbrechen
        stop_running_scan(f"Überflug {p['name']}")
    if scan_running():
        return False
    _sat["started"].add(p["key"])
    applog("INFO", "scheduler", "satellite_start", sat=p["name"], channels=p["channels"], max_el=p["max_el"],
           plan=p["status"])
    # -w statt -n: die Sperre ist manchmal nur einen Augenblick belegt (Statusabfrage des Dashboards,
    # vorheriger Überflug gibt den Stick gerade frei) – dann warten statt den Überflug still zu verlieren
    cmd = ["/usr/bin/flock", "-w", "30", str(LOCK), "python3", str(BASE / "bin" / "sat_pass.py"),
           "--sats", ",".join(p["channels"]), "--until", str(p["rec_end"]), "--keys", ",".join(p.get("keys", [p["key"]]))]
    with open(BASE / "log" / "satellite.log", "a", encoding="utf-8") as out:
        pr = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    SAT_PID.write_text(str(pr.pid))
    _sat["procs"] = [x for x in _sat["procs"] if x.poll() is None] + [pr]   # beendete abholen
    _sat["pending"][p["key"]] = (pr, time.time(), p)
    return True


def check_started_passes():
    """Ist ein gestarteter Überflug gleich wieder beendet, ohne aufzunehmen (Sperre nicht bekommen,
    Stick belegt)? Dann erneut versuchen, solange das Fenster offen ist – sonst mit Grund vermerken."""
    import satellites as SAT
    for key, (pr, t, p) in list(_sat["pending"].items()):
        rc = pr.poll()
        res = (SAT.read_results() or {}).get(key) or {}
        if res.get("state") in ("running", "evaluating", "done", "error", "cancelled"):
            del _sat["pending"][key]                    # läuft bzw. lief – in Ordnung
            continue
        if rc is None:
            continue
        del _sat["pending"][key]
        if time.time() < p["rec_end"] - 60:
            _sat["started"].discard(key)                # noch Zeit: im nächsten Durchlauf neu starten
            applog("WARN", "scheduler", "satellite_retry", sat=p["name"], rc=rc)
        else:
            for k in p.get("keys", [key]):
                SAT.write_result(k, state="skipped", error="Stick war belegt – Aufnahme konnte nicht starten")


for _d in ("data", "log", "recordings", "reports"):              # frische Installation
    (BASE / _d).mkdir(parents=True, exist_ok=True)
_cfg = BASE / "config" / "scanner.json"                          # lokale Kopie des Bandplans anlegen
if not _cfg.exists() and (BASE / "config" / "scanner.default.json").exists():
    import shutil
    shutil.copy(BASE / "config" / "scanner.default.json", _cfg)
log("Scheduler gestartet")
try:
    import sysmon as SYSMON                                       # Verlauf für den Reiter „System“
    _sysmon = SYSMON.Sampler()
except Exception:
    _sysmon = None

while True:
    try:
        (BASE / "data" / "scheduler.heartbeat").touch()
        if _sysmon:
            try:
                _sysmon.tick()
            except Exception as exc:                              # Verlauf darf den Zeitplaner nie stören
                if not _sat.get("sysmon_err"):
                    applog("WARN", "scheduler", "sysmon_failed", error=f"{type(exc).__name__}: {exc}")
                _sat["sysmon_err"] = True
        # SDR-Sticks: neue erkennen und melden (alle 30 s), Tuner-Typ im Leerlauf abfragen
        if time.time() - _sat.get("sticks_t", 0) > 30:
            _sat["sticks_t"] = time.time()
            try:
                import sticks as STK
                STK.sync()
                if not sat_pass_active() and not live_active_now() and not scan_running():
                    STK.probe_tuners()
            except Exception as exc:
                if not _sat.get("sticks_err"):
                    applog("WARN", "scheduler", "sticks_failed", error=f"{type(exc).__name__}: {exc}")
                _sat["sticks_err"] = True

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
        check_started_passes()
        for key, (bp, why) in list(_sat["blocked"].items()):       # Fenster vorbei, nie gestartet
            if key in _sat["started"]:
                del _sat["blocked"][key]
            elif time.time() >= bp["rec_end"] - 60:
                del _sat["blocked"][key]
                _sat["started"].add(key)
                import satellites as SAT
                applog("WARN", "scheduler", "satellite_skipped", sat=bp["name"], reason=why)
                for k in bp.get("keys", [key]):
                    SAT.write_result(k, state="skipped", error=why)
        now_t = time.time()
        soon = None
        import satellites as SAT
        scan_s = typical_scan_seconds() + 30          # ein Scan, der jetzt startet, wäre bis dann fertig
        starving = scan_starving(interval)
        for p in next_passes():
            if p["key"] in _sat["started"]:
                continue
            if p["rec_start"] <= now_t < p["rec_end"] - 60:
                if not start_sat_pass(p):
                    # Stick noch belegt: Grund merken, damit „nicht aufgenommen“ nicht unerklärt bleibt
                    why = ("vorheriger Überflug nahm noch auf" if sat_pass_active()
                           else "Scan lief (Bildsatelliten nur bei freiem Stick)" if p["prio"] <= SAT.OPPORTUNISTIC_PRIO
                           else "Stick belegt")
                    _sat["blocked"][p["key"]] = (p, why)
                break
            if now_t < p["rec_start"]:
                wait = p["rec_start"] - now_t
                if p["prio"] > SAT.OPPORTUNISTIC_PRIO:
                    # wichtige Überflüge: keinen Scan mehr starten, der nicht vorher fertig wird
                    if wait < max(300, scan_s):
                        soon = p
                    break
                # Bildsatelliten (nur bei freiem Stick): ebenso, außer es kam schon lange kein Scan mehr dran
                if wait < scan_s and not starving:
                    soon = p
                    break
        if due and soon:
            try:
                (BASE / "data" / "scan_postponed.json").write_text(json.dumps(
                    {"name": soon["name"], "until": soon["rec_end"], "start": soon["rec_start"]}))
            except OSError:
                pass
            if _sat["postponed"] != soon["key"]:
                _sat["postponed"] = soon["key"]
                applog("INFO", "scheduler", "scan_postponed", reason=f"Überflug {soon['name']} in "
                       f"{max(0, int((soon['rec_start'] - now_t) // 60))} min")
            due = False
        if due and sat_pass_active():
            due = False
        # Speicherkarte fast voll (housekeeping im Webserver): keine neuen Scans
        try:
            import housekeeping as HK
            if due and HK.disk_full():
                if not _sat.get("disk_note"):
                    applog("ERROR", "scheduler", "scan_skipped_disk_full")
                _sat["disk_note"] = True
                due = False
            elif not HK.disk_full():
                _sat["disk_note"] = False
        except Exception:
            pass

        live_lock = BASE / "data" / "live.lock"
        live = live_lock.exists() and (time.time() - live_lock.stat().st_mtime) < 120
        if due and not scan_running() and not live:
            (BASE / "data" / "scan_postponed.json").unlink(missing_ok=True)
            applog("INFO", "scheduler", "scan_start", reason="interval", interval_min=interval)
            run_scan()

        time.sleep(10)

    except Exception as exc:
        import traceback
        applog("ERROR", "scheduler", "loop_exception", error=f"{type(exc).__name__}: {exc}",
               where=traceback.format_exc().strip().splitlines()[-2:])
        time.sleep(10)
