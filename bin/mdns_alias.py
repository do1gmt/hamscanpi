#!/usr/bin/env python3
"""Hesperos DX unter http://hesperosdx.local:8080 erreichbar machen – unabhängig davon, wie der Pi heißt.

Der eigentliche Rechnername gehört dem Benutzer und wird nicht angefasst. Heißt der Pi ohnehin „hesperosdx“
(Groß-/Kleinschreibung egal), meldet avahi den Namen schon selbst an und hier ist nichts zu tun. Sonst meldet
dieser Dienst zusätzlich den Namen hesperosdx.local mit der aktuellen IPv4-Adresse an (avahi-publish -a) und zieht
ihn nach, wenn sich die Adresse ändert. Belegt schon ein anderes Gerät den Namen (zweiter Hesperos DX im Netz),
bleibt dieser Pi unter seinem eigenen Namen erreichbar; das steht im Zustand und als Hinweis.

Zustand: data/mdns_state.json {"name", "ip", "mode": "hostname"|"alias"|"conflict"|"missing", "url"}
Läuft als Dienst hesperosdx-mdns (systemd), prüft alle 30 s.
"""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "bin"))
STATE = BASE / "data" / "mdns_state.json"
NAME = "hesperosdx"


def _ip():
    try:
        import health as H
        return H.net_state().get("ip")
    except Exception:
        return None


def _save(st):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st), encoding="utf-8")
    except OSError:
        pass


def _log(level, event, **d):
    try:
        from applog import log
        log(level, "mdns", event, **d)
    except Exception:
        pass


def _notice(mode, host):
    try:
        import notices as NT
        if mode == "conflict":
            NT.raise_("mdns_conflict", "info", "system", "Name hesperosdx.local schon vergeben",
                      f"Ein anderes Gerät im Netz meldet sich schon als hesperosdx.local (zweiter Hesperos DX?). Dieser Pi "
                      f"bleibt unter http://{host}.local:8080 und seiner IP-Adresse erreichbar.")
        else:
            NT.clear("mdns_conflict")
    except Exception:
        pass


def _custom_name():
    try:
        import station as STN
        return STN.custom()
    except Exception:
        return False


def run(port=8080, rounds=None, every=30, host=None, custom=None):
    fixed_host = host
    proc, cur_ip, mode = None, None, None
    n = 0
    while rounds is None or n < rounds:
        n += 1
        host = fixed_host or socket.gethostname().split(".")[0]   # ändert sich mit dem Stationsnamen
        ip = _ip()
        own = _custom_name() if custom is None else custom
        if host.lower() == NAME or own:
            # eigener Stationsname: der Rechnername ist schon daraus abgeleitet (hesperos-afgn.local) – kein
            # zusätzliches hesperosdx.local, sonst streiten sich zwei Pis darum
            if proc and proc.poll() is None:
                proc.terminate()
            proc = None
            new_mode = "hostname"
        elif not ip:
            new_mode = "no_ip"
        elif not shutil_which("avahi-publish"):
            new_mode = "missing"
        else:
            if proc is None or proc.poll() is not None or ip != cur_ip:
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                proc = subprocess.Popen(["avahi-publish", "-a", "-R", f"{NAME}.local", ip],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                cur_ip = ip
                time.sleep(3)
                if proc.poll() is not None:            # sofort beendet: Name belegt oder avahi aus
                    err = (proc.stderr.read() or "").strip()
                    new_mode = "conflict" if "collision" in err.lower() or "exists" in err.lower() else "error"
                    if new_mode != mode:
                        _log("WARN", "mdns_alias_failed", error=err[:200], ip=ip)
                    proc = None
                else:
                    new_mode = "alias"
                    if mode != "alias":
                        _log("INFO", "mdns_alias", name=f"{NAME}.local", ip=ip)
            else:
                new_mode = "alias"
        if new_mode != mode:
            _notice(new_mode, host)
        mode = new_mode
        name = f"{NAME}.local" if mode == "alias" else f"{host}.local"
        _save({"name": name, "host": host, "ip": ip, "mode": mode, "url": f"http://{name}:{port}",
               "updated": time.time()})
        if rounds is None or n < rounds:
            time.sleep(every)
    if proc and proc.poll() is None:
        proc.terminate()


def shutil_which(cmd):
    import shutil
    return shutil.which(cmd)


def state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 8080)
