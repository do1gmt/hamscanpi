#!/usr/bin/env python3
"""
Systemwerte für den Reiter „System“ – nur aus /proc und /sys gelesen, ohne Zusatzpakete.

  * live():    Prozessor (gesamt, je Kern, Takt), Arbeitsspeicher und Swap, Temperatur, Last,
               was HamScanPi gerade tut, eigene Prozesse mit RAM/CPU, Netzwerk. Für die 5-s-Anzeige:
               startet keine Prozesse (anders als housekeeping.system_status, die bleibt bei 60 s).
  * Sampler:   läuft im Scheduler (Schleife alle 10 s) und schreibt einmal pro Minute eine Zeile nach
               data/sysmon.db: CPU-Mittel und -Spitze, freier Arbeitsspeicher, Swap, Temperatur,
               Drosselung, Tätigkeit (Leerlauf/Scan/Live/Überflug). Aufbewahrt werden 7 Tage.
  * history(): Verlauf für die Kurven, auf höchstens ~720 Punkte zusammengefasst.

Eigene Datenbankdatei, damit die minütliche Zeile nie mit den Schreibzugriffen eines Scans auf
scanner.db um die Sperre konkurriert.
"""
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "sysmon.db"
SCAN_LOCK = BASE / "data" / "scan.lock"
LIVE_LOCK = BASE / "data" / "live.lock"
SAT_PID = BASE / "data" / "sat_pass.pid"
KEEP_S = 7 * 86400
ACT = {0: "idle", 1: "scan", 2: "live", 3: "sat"}
CLK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
# Hilfsprogramme, die HamScanPi startet (Prozessname = erstes Wort der Befehlszeile)
HELPERS = {"rtl_sdr", "rtl_fm", "rtl_power", "rtl_tcp", "rtl_433", "arecord", "aplay", "sox", "multimon-ng",
           "jt9", "wsprd", "ffmpeg", "csdr", "satdump", "direwolf", "redsea", "pmsdr-control", "pmsdr_ctl"}
LABELS = {"webapp.py": "Weboberfläche", "scheduler.py": "Zeitplaner", "scanner.py": "Scan", "run_cycle.sh": "Scan-Ablauf",
          "sat_pass.py": "Überflug", "live.py": "Live", "sstv_watch.py": "SSTV-Wache", "ft8_watch.py": "FT8-Wache",
          "channel_watch.py": "Kanalwache", "notify.py": "Benachrichtigung", "pskreporter.py": "PSK Reporter"}


# ---------------------------------------------------------------- Einzelwerte
def _read(path):
    try:
        return Path(path).read_text().strip()
    except Exception:
        return None


def cpu_times():
    """[(busy, total)] – Index 0 gesamt, danach je Kern."""
    out = []
    try:
        for line in Path("/proc/stat").read_text().splitlines():
            if not line.startswith("cpu"):
                break
            v = [int(x) for x in line.split()[1:]]
            idle = v[3] + (v[4] if len(v) > 4 else 0)                 # idle + iowait
            total = sum(v[:8])                                          # guest steckt schon in user
            out.append((total - idle, total))
    except Exception:
        pass
    return out


def cpu_usage(prev, cur):
    """Prozent je Eintrag aus zwei cpu_times()-Messungen."""
    res = []
    for (b0, t0), (b1, t1) in zip(prev, cur):
        dt = t1 - t0
        res.append(round(100.0 * (b1 - b0) / dt, 1) if dt > 0 else 0.0)
    return res


def cpu_freq():
    cur, mx = [], []
    for d in sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq")):
        c, m = _read(d / "scaling_cur_freq"), _read(d / "cpuinfo_max_freq")
        if c and c.isdigit():
            cur.append(int(c) // 1000)
        if m and m.isdigit():
            mx.append(int(m) // 1000)
    return (max(cur) if cur else None), (max(mx) if mx else None)


def meminfo():
    """MB: total, used (Programme), cache, free, available, swap_total, swap_used."""
    m = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            m[k] = int(v.split()[0]) * 1024
    except Exception:
        return None
    mb = lambda x: round(x / 1048576)
    cache = m.get("Buffers", 0) + m.get("Cached", 0) + m.get("SReclaimable", 0) - m.get("Shmem", 0)
    used = m["MemTotal"] - m.get("MemFree", 0) - cache
    return {"total": mb(m["MemTotal"]), "used": mb(max(0, used)), "cache": mb(max(0, cache)), "free": mb(m.get("MemFree", 0)),
            "available": mb(m.get("MemAvailable", m.get("MemFree", 0))),
            "swap_total": mb(m.get("SwapTotal", 0)), "swap_used": mb(m.get("SwapTotal", 0) - m.get("SwapFree", 0))}


def cpu_temp():
    t = _read("/sys/class/thermal/thermal_zone0/temp")
    return round(int(t) / 1000, 1) if t and t.lstrip("-").isdigit() else None


def throttled_now():
    """True/False aus vcgencmd (nur Pi), None wenn nicht abfragbar. Startet einen Prozess – nur minütlich."""
    if not shutil.which("vcgencmd"):
        return None
    try:
        r = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3)
        return bool(int(r.stdout.strip().split("=")[1], 16) & 0xF)
    except Exception:
        return None


def _lock_held(path):
    """Hält irgendein Prozess eine flock-Sperre auf path? Aus /proc/locks gelesen, ohne die Sperre selbst
    anzufassen – ein kurzes Probe-flock könnte sonst genau den Start eines Scans (flock -n) vereiteln."""
    try:
        ino = os.stat(path).st_ino
    except OSError:
        return False
    try:
        for line in Path("/proc/locks").read_text().splitlines():
            p = line.split()
            if len(p) > 5 and p[1] == "FLOCK" and p[5].rsplit(":", 1)[-1] == str(ino):
                return True
    except Exception:
        pass
    return False


def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(") ", 1)[1][0] not in "ZX"
    except Exception:
        return False


def activity():
    """3 Überflug, 2 Live, 1 Scan, 0 Leerlauf (die Satellitenaufnahme hält dieselbe Sperre wie der Scan)."""
    try:
        if _alive(int(SAT_PID.read_text())):
            return 3
    except Exception:
        pass
    try:
        if LIVE_LOCK.exists() and time.time() - LIVE_LOCK.stat().st_mtime < 120:
            return 2
    except OSError:
        pass
    return 1 if _lock_held(SCAN_LOCK) else 0


_OTHER_TOP = [None, 0.0]       # größter fremder Prozess beim letzten _proc_list(): [Name, MB]


def _proc_list():
    """{pid: (name, label, rss_mb, jiffies)} für HamScanPi-Prozesse, dazu RSS aller übrigen in MB."""
    own, other = {}, 0
    _OTHER_TOP[0], _OTHER_TOP[1] = None, 0.0
    base = str(BASE)
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            raw = Path(f"/proc/{d}/cmdline").read_bytes()
            if not raw:
                continue                                                # Kernel-Thread
            args = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
            st = Path(f"/proc/{d}/stat").read_text()
            f = st.rsplit(") ", 1)[1].split()
            jiff = int(f[11]) + int(f[12])
            rss = int(Path(f"/proc/{d}/statm").read_text().split()[1]) * PAGE / 1048576
        except Exception:
            continue
        exe = os.path.basename(args[0])
        if exe == "flock":                                              # Wrapper, winzig – das Kind zählt
            continue
        # eigenes Skript: ein Argument (ohne Leerzeichen, also kein „bash -c '…'“) ist eine .py/.sh-Datei
        # im HamScanPi-Verzeichnis, absolut oder relativ zum Arbeitsverzeichnis des Prozesses
        script = None
        for a in args[:3]:
            if a.endswith((".py", ".sh")) and not any(c.isspace() for c in a):
                try:
                    full = a if a.startswith("/") else os.path.join(os.readlink(f"/proc/{d}/cwd"), a)
                except OSError:
                    continue
                if os.path.normpath(full).startswith(base + "/"):
                    script = os.path.basename(a)
                    break
        mine = exe in HELPERS or script is not None
        if mine:
            name = script or exe
            label = LABELS.get(name) or ("Auswertung" if name.startswith(("analyze_", "decode_")) else "")
            own[int(d)] = (name, label, rss, jiff)
        else:
            other += rss
            if rss > _OTHER_TOP[1]:
                _OTHER_TOP[0], _OTHER_TOP[1] = exe, rss
    return own, round(other)


def top_process():
    """Größter Prozess gerade (Name, MB) – HamScanPi-Skripte mit Skriptnamen, andere mit Programmnamen."""
    own, _ = _proc_list()
    best = max(own.values(), key=lambda v: v[2], default=None)
    cands = [(best[0], best[2])] if best else []
    if _OTHER_TOP[0]:
        cands.append((_OTHER_TOP[0], _OTHER_TOP[1]))
    return max(cands, key=lambda c: c[1], default=(None, 0.0))


def network():
    """Eigene IP (Weg ins Netz, ohne ein Paket zu senden), WLAN-Pegel falls vorhanden."""
    ip = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))                                     # TEST-NET, verbindungslos
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    wlan = None
    w = _read("/proc/net/wireless")
    if w:
        for line in w.splitlines()[2:]:
            p = line.split()
            if len(p) >= 4:
                try:
                    wlan = {"iface": p[0].rstrip(":"), "dbm": int(float(p[3].rstrip(".")))}
                except ValueError:
                    pass
                break
    return {"ip": ip, "hostname": socket.gethostname(), "wlan": wlan}


_STATIC = {}


def pi_info():
    """Modell, Betriebssystem, Kernel, Python – ändert sich nicht, einmal gelesen."""
    if not _STATIC:
        model = _read("/proc/device-tree/model")
        osr = _read("/etc/os-release") or ""
        m = re.search(r'^PRETTY_NAME="?([^"\n]+)', osr, re.M)
        _STATIC.update(model=(model or "").rstrip("\0") or platform.machine(), os=m.group(1) if m else platform.system(),
                       kernel=platform.release(), python=platform.python_version(), cores=os.cpu_count())
    return dict(_STATIC)


# ---------------------------------------------------------------- Live-Anzeige (Webserver)
_live = {"t": 0.0, "cpu": None, "procs": {}}
_live_lock = threading.Lock()


def live():
    with _live_lock:
        now, cur = time.time(), cpu_times()
        if not _live["cpu"] or now - _live["t"] > 30:                   # erste Abfrage: kurz messen
            _live.update(t=now, cpu=cur, procs={p: v[3] for p, v in _proc_list()[0].items()})
            time.sleep(0.4)
            now, cur = time.time(), cpu_times()
        usage = cpu_usage(_live["cpu"], cur)
        own, other = _proc_list()
        dt = max(0.1, now - _live["t"])
        ncpu = max(1, len(cur) - 1)
        procs = []
        for pid, (name, label, rss, jiff) in own.items():
            prev = _live["procs"].get(pid)
            cpu = None if prev is None else round(100.0 * (jiff - prev) / CLK / dt / ncpu, 1)
            procs.append({"pid": pid, "name": name, "label": label, "rss_mb": round(rss, 1), "cpu": cpu})
        procs.sort(key=lambda p: -p["rss_mb"])
        _live.update(t=now, cpu=cur, procs={p: v[3] for p, v in own.items()})
    fcur, fmax = cpu_freq()
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        load = None
    up = _read("/proc/uptime")
    return {"t": now, "cpu": {"total": usage[0] if usage else None, "cores": usage[1:], "freq_mhz": fcur, "freq_max_mhz": fmax},
            "load": load, "temp_c": cpu_temp(), "mem": meminfo(), "activity": ACT[activity()],
            "procs": procs, "other_rss_mb": other, "net": network(), "pi": pi_info(),
            "uptime_h": round(float(up.split()[0]) / 3600, 1) if up else None}


# ---------------------------------------------------------------- Verlauf
def _db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=5)
    db.execute("CREATE TABLE IF NOT EXISTS samples (t INTEGER PRIMARY KEY, cpu REAL, cpu_peak REAL, mem_avail INTEGER, "
               "swap INTEGER, temp REAL, throttled INTEGER, act INTEGER)")
    cols = {r[1] for r in db.execute("PRAGMA table_info(samples)")}
    if "top_name" not in cols:                                          # ab 1.7.15: größter Prozess je Minute
        db.execute("ALTER TABLE samples ADD COLUMN top_name TEXT")
        db.execute("ALTER TABLE samples ADD COLUMN top_mb INTEGER")
    return db


class Sampler:
    """Im Scheduler bei jedem Schleifendurchlauf tick() aufrufen (alle ~10 s); schreibt einmal pro Minute."""

    def __init__(self, period=60):
        self.period = period
        self.reset(time.time())
        self.pruned = 0.0

    def reset(self, now):
        self.t0, self.cpu0 = now, cpu_times()
        self.last = self.cpu0
        self.peak, self.act = 0.0, 0
        self.top = (None, 0.0)                                          # größter Prozess in dieser Minute

    def tick(self, now=None):
        now = now or time.time()
        cur = cpu_times()
        u = cpu_usage(self.last, cur)
        if u:
            self.peak = max(self.peak, u[0])
        self.last = cur
        self.act = max(self.act, activity())
        try:                                                            # bei jedem Durchlauf (~10 s) nachsehen
            tp = top_process()
            if tp[0] and tp[1] > self.top[1]:
                self.top = tp
        except Exception:
            pass
        if now - self.t0 < self.period:
            return None
        if now - self.t0 > 5 * self.period:                             # Schleife hing oder Uhr sprang
            self.reset(now)
            return None
        avg = cpu_usage(self.cpu0, cur)
        mem = meminfo() or {}
        thr = throttled_now()
        row = (int(now // 60 * 60), avg[0] if avg else None, self.peak, mem.get("available"), mem.get("swap_used"),
               cpu_temp(), None if thr is None else int(thr), self.act,
               self.top[0], int(round(self.top[1])) if self.top[0] else None)
        db = _db()
        try:
            db.execute("INSERT OR REPLACE INTO samples (t, cpu, cpu_peak, mem_avail, swap, temp, throttled, act, "
                       "top_name, top_mb) VALUES (?,?,?,?,?,?,?,?,?,?)", row)
            if now - self.pruned > 3600:
                db.execute("DELETE FROM samples WHERE t < ?", (int(now - KEEP_S),))
                self.pruned = now
            db.commit()
        finally:
            db.close()
        self.reset(now)
        return row


def history(hours=24, now=None):
    hours = max(1, min(int(hours), KEEP_S // 3600))
    now = now or time.time()
    b = max(60, -(-hours * 3600 // 720) // 60 * 60)                    # Rastergröße, auf Minuten gerundet
    since = int(now - hours * 3600)
    if not DB.exists():
        return {"bucket_s": b, "from": since, "to": int(now), "points": []}
    db = _db()
    try:
        rows = db.execute("SELECT (t / ?) * ? AS tb, ROUND(AVG(cpu), 1), ROUND(MAX(cpu_peak), 1), MIN(mem_avail), MAX(swap), "
                          "MAX(temp), MAX(throttled), MAX(act) FROM samples WHERE t >= ? GROUP BY tb ORDER BY tb",
                          (b, b, since)).fetchall()
        total = db.execute("SELECT COUNT(*), MIN(t) FROM samples").fetchone()
        # größter Prozess je Raster: der Eintrag mit dem meisten Speicher
        tops = {}
        for tb, name, mb in db.execute("SELECT (t / ?) * ?, top_name, top_mb FROM samples WHERE t >= ? AND top_mb IS NOT NULL",
                                       (b, b, since)):
            if mb > tops.get(tb, (None, -1))[1]:
                tops[tb] = (name, mb)
    finally:
        db.close()
    keys = ("t", "cpu", "cpu_peak", "mem_avail", "swap", "temp", "throttled", "act")
    pts = []
    for r in rows:
        p = dict(zip(keys, r))
        if r[0] in tops:
            p["top_name"], p["top_mb"] = tops[r[0]]
        pts.append(p)
    return {"bucket_s": b, "from": since, "to": int(now), "oldest": total[1], "samples": total[0], "points": pts}


if __name__ == "__main__":
    import json
    print(json.dumps(live(), indent=1, ensure_ascii=False))
