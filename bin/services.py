#!/usr/bin/env python3
"""Dienste von HamScanPi anzeigen und neu starten, den Pi neu starten – für das Dashboard (System → Dienste)
und die Notfall-Seite (bin/rescue.py, eigener Dienst auf Port 8081).

Neu starten braucht root: das Update legt /etc/sudoers.d/hamscanpi an, das genau diese Befehle ohne Passwort
erlaubt (und sonst nichts):
  systemctl --no-block restart hamscanpi-web | hamscanpi-scheduler | hamscanpi-rescue
  systemctl reboot
„--no-block“: systemd übernimmt den Auftrag sofort – auch wenn der Webserver sich damit selbst neu startet.
"""
import shutil
import subprocess
import time

UNITS = {"hamscanpi-web": "Webserver", "hamscanpi-scheduler": "Zeitplaner", "hamscanpi-rescue": "Notfall-Seite"}
SYSTEMCTL = shutil.which("systemctl") or "/usr/bin/systemctl"


def _show_all():
    """Ein einziger Aufruf für alle Dienste (schnell, auch wenn systemd träge antwortet)."""
    try:
        r = subprocess.run([SYSTEMCTL, "show", *UNITS, "-p", "Id", "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts",
                            "-p", "ActiveEnterTimestamp", "-p", "MemoryCurrent", "-p", "MainPID", "-p", "LoadState"],
                           capture_output=True, text=True, timeout=4)
    except Exception:
        return {}
    out, cur = {}, {}
    for line in r.stdout.splitlines() + [""]:
        if not line.strip():
            if cur.get("Id"):
                out[cur["Id"].removesuffix(".service")] = cur
            cur = {}
            continue
        k, _, v = line.partition("=")
        cur[k] = v
    return out


def status():
    allu = _show_all()
    out = []
    for u, label in UNITS.items():
        s = allu.get(u, {})
        mem = s.get("MemoryCurrent", "")
        out.append({"unit": u, "label": label, "loaded": s.get("LoadState") == "loaded",
                    "active": s.get("ActiveState"), "sub": s.get("SubState"),
                    "restarts": int(s["NRestarts"]) if s.get("NRestarts", "").isdigit() else None,
                    "since": s.get("ActiveEnterTimestamp") or None,
                    "memory_mb": round(int(mem) / 1e6) if mem.isdigit() else None,
                    "pid": int(s["MainPID"]) if s.get("MainPID", "").isdigit() else None})
    return {"units": out, "sudo": sudo_ok()}


def sudo_ok():
    """Darf HamScanPi die Befehle ausführen (sudo-Regel vorhanden)?"""
    try:
        r = subprocess.run(["sudo", "-n", "-l", SYSTEMCTL, "--no-block", "restart", "hamscanpi-web"],
                           capture_output=True, text=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


def restart(unit, log=None):
    if unit not in UNITS:
        raise ValueError("unbekannter Dienst")
    if log:
        log("WARN", "services", "restart_requested", unit=unit)
    r = subprocess.run(["sudo", "-n", SYSTEMCTL, "--no-block", "restart", unit], capture_output=True, text=True,
                       timeout=10, start_new_session=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "sudo verweigert").strip()[:200]
                           + " – sudo-Regel fehlt? Update erneut einspielen.")
    return True


def reboot(log=None):
    if log:
        log("WARN", "services", "reboot_requested")
    time.sleep(0.5)                                  # damit die Antwort noch beim Browser ankommt
    r = subprocess.run(["sudo", "-n", SYSTEMCTL, "reboot"], capture_output=True, text=True, timeout=10,
                       start_new_session=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "sudo verweigert").strip()[:200]
                           + " – sudo-Regel fehlt? Update erneut einspielen.")
    return True
