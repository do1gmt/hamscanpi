"""Systemupdates von Raspberry Pi OS – prüfen, auf Knopfdruck einspielen, danach nachts neu starten.

Ablauf
  prüfen        einmal pro Woche (und mit „Jetzt prüfen“): Paketlisten holen, anstehende Updates zählen
                (Root-Helfer /usr/local/sbin/hesperosdx-sys check). Ergebnis in data/sysupdate.json; stehen
                Updates an, ein ruhiger Hinweis (ohne Mail/Push).
  einspielen    nur auf Knopfdruck. Der Wunsch landet in data/sysupgrade_request.json; der Zeitplaner startet das
                Update, sobald kein Scan läuft und kein Überflug in den nächsten 20 min ansteht
                (hesperosdx-sys upgrade – eigener systemd-Lauf, übersteht einen Neustart des Dashboards).
                Solange es läuft, startet der Zeitplaner keine neuen Scans (Überflüge laufen weiter).
  neu starten   braucht das Update einen Neustart (Kernel, Firmware, C-Bibliothek, systemd), merkt sich das
                data/reboot_pending.json. Nachts zur eingestellten Stunde (Vorgabe 4 Uhr Ortszeit) startet der Pi
                neu – aber nur, wenn kein Scan läuft, kein Überflug läuft oder in 20 min ansteht und niemand Live
                im Browser hört (eine Dauerwache läuft nach dem Neustart von selbst weiter). Sonst in der
                nächsten Nacht. Abschaltbar; „Jetzt neu starten“ geht immer.
"""
import json
import os
import subprocess
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
HELPER = "/usr/local/sbin/hesperosdx-sys"
STATE = BASE / "data" / "sysupdate.json"
REQUEST = BASE / "data" / "sysupgrade_request.json"
REBOOT = BASE / "data" / "reboot_pending.json"
RUN_STATE = Path("/run/hesperosdx-sysupgrade.json")
RUN_LOG = Path("/var/log/hesperosdx-sysupgrade.log")
CHECK_EVERY_S = 7 * 86400
_check_lock = threading.Lock()


_EMPTY = object()


def _read(p, default=_EMPTY):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is _EMPTY else default


def _write(p, d):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def cfg(settings=None):
    if settings is None:
        settings = _read(BASE / "config" / "web_settings.json")
    c = dict(settings.get("sysupdate") or {})
    return {"reboot_auto": bool(c.get("reboot_auto", True)),
            "reboot_hour": max(0, min(23, int(c.get("reboot_hour", 4) if str(c.get("reboot_hour", 4)).isdigit() else 4)))}


def helper_ok():
    return os.path.exists(HELPER)


def parse(text):
    out = []
    for line in (text or "").splitlines():
        p = line.split("\t")
        if len(p) == 3 and p[0]:
            out.append({"pkg": p[0], "old": None if p[1] == "-" else p[1], "new": p[2]})
    return out


REBOOT_PREFIX = ("linux-image", "raspberrypi-kernel", "raspberrypi-bootloader", "raspi-firmware", "rpi-eeprom",
                 "libc6", "systemd", "firmware-")


def check(log=None, runner=None):
    """Anstehende Updates ermitteln (dauert je nach Internet bis zu einigen Minuten)."""
    if not _check_lock.acquire(blocking=False):
        return state()
    try:
        st = state()
        run = runner or (lambda: subprocess.run(["sudo", "-n", HELPER, "check"], capture_output=True, text=True,
                                                timeout=900))
        try:
            r = run()
            if r.returncode != 0:
                raise RuntimeError((r.stderr or r.stdout or "Fehler").strip().splitlines()[-1][:200])
            pk = parse(r.stdout)
            st.update(checked=time.time(), packages=pk, error=None,
                      reboot_likely=any(x["pkg"].startswith(REBOOT_PREFIX) for x in pk))
        except Exception as e:
            st.update(checked=time.time(), error=f"{e}" if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}")
        _write(STATE, st)
        if log:
            log("INFO" if not st.get("error") else "WARN", "sysupdate", "sysupdate_checked",
                packages=len(st.get("packages") or []), error=st.get("error"))
        _notice(st)
        return st
    finally:
        _check_lock.release()


def _notice(st):
    try:
        import notices as NT
        n = len(st.get("packages") or [])
        if n and not st.get("error"):
            NT.raise_("sysupdate", "info", "system", f"Systemupdates verfügbar: {n} Pakete",
                      "Raspberry Pi OS hat Aktualisierungen" + (" – auch für Kernel/Firmware (danach Neustart)." if st.get("reboot_likely") else "."),
                      fix="System → Systemupdates: „System aktualisieren“ – startet, sobald kein Scan läuft.", notify=False)
        else:
            NT.clear("sysupdate", "keine Updates offen")
    except Exception:
        pass


def state():
    return _read(STATE)


def run_state():
    return _read(RUN_STATE)


def running():
    return run_state().get("state") in ("queued", "running")


def log_tail(n=60):
    try:
        with open(RUN_LOG, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-400:]
    except OSError:
        return ""
    # nur der letzte Lauf
    for i in range(len(lines) - 1, -1, -1):
        if "=== Systemupdate gestartet" in lines[i]:
            lines = lines[i:]
            break
    return "".join(lines[-n:])


def request():
    if not helper_ok():
        raise ValueError("Systemupdates brauchen einmal „Jetzt aktualisieren“ (Hesperos-Update) – danach geht es hier")
    if running():
        raise ValueError("Das Systemupdate läuft schon")
    _write(REQUEST, {"t": time.time()})


def cancel_request():
    REQUEST.unlink(missing_ok=True)


def requested():
    return REQUEST.exists()


def start(log=None, runner=None):
    """Vom Zeitplaner, wenn der Stick frei ist: Update anstoßen."""
    REQUEST.unlink(missing_ok=True)
    run = runner or (lambda: subprocess.run(["sudo", "-n", HELPER, "upgrade"], capture_output=True, text=True, timeout=60))
    r = run()
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "sudo verweigert").strip()[:200]
        if log:
            log("ERROR", "sysupdate", "sysupgrade_start_failed", error=msg)
        _write(STATE, dict(state(), last_error=msg))
        return False
    if log:
        log("INFO", "sysupdate", "sysupgrade_started")
    return True


def boot_time():
    try:
        return time.time() - float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        return 0.0


def follow(log=None):
    """Vom Zeitplaner (jede Runde): ein fertiges Update auswerten – Ergebnis merken, Neustart vormerken, neu prüfen.
    Nach einem Neustart ist der Neustart-Wunsch erledigt."""
    rb = _read(REBOOT, None)
    if rb and rb.get("since", 0) < boot_time():
        REBOOT.unlink(missing_ok=True)
        if log:
            log("INFO", "sysupdate", "reboot_done", reason=rb.get("reason"))
    rs = run_state()
    if rs.get("state") not in ("done", "failed"):
        return
    st = state()
    fin = rs.get("finished") or rs.get("t")
    if st.get("handled") == fin:
        return
    st.update(handled=fin, last_result=rs.get("state"), last_finished=fin, last_packages=rs.get("packages"),
              last_error=rs.get("error"))
    _write(STATE, st)
    if log:
        log("INFO" if rs.get("state") == "done" else "ERROR", "sysupdate", "sysupgrade_" + rs["state"],
            packages=rs.get("packages"), reboot=bool(rs.get("reboot")), error=rs.get("error"))
    if rs.get("state") == "done":
        if rs.get("reboot") and fin and fin > boot_time():
            _write(REBOOT, {"since": time.time(), "reason": "Systemupdate"})
        threading.Thread(target=check, kwargs={"log": log}, daemon=True).start()   # Liste auffrischen
    else:
        try:
            import notices as NT
            NT.event("sysupgrade_failed", "warn", "system", "Systemupdate fehlgeschlagen", str(rs.get("error") or ""),
                     fix="System → Systemupdates: Ablauf ansehen und erneut versuchen.", notify=False)
        except Exception:
            pass


def reboot_pending():
    return _read(REBOOT, None)


def reboot_due(now=None, settings=None):
    """Jetzt (nachts zur eingestellten Stunde) neu starten? Ob der Pi gerade frei ist, prüft der Zeitplaner."""
    if not reboot_pending():
        return False
    c = cfg(settings)
    if not c["reboot_auto"]:
        return False
    return time.localtime(now).tm_hour == c["reboot_hour"]


def check_due(now=None):
    if not helper_ok():
        return False
    st = state()
    every = 86400 if st.get("error") else CHECK_EVERY_S      # ging die Prüfung schief: morgen noch einmal
    return (now or time.time()) - float(st.get("checked") or 0) >= every
