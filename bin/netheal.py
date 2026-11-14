#!/usr/bin/env python3
"""WLAN, das sich selbst repariert – und Ausweich-WLANs.

Jede Minute (aus health.run_all): Hängt der Pi im WLAN, und antwortet der Router? Bleibt die Verbindung weg, wird in
Stufen nachgeholfen, mit Pausen dazwischen:
    ab 2 min   neu verbinden
    ab 5 min   WLAN aus und wieder an
    ab 9 min   Netzwerkdienst (NetworkManager) neu starten
    ab 14 min  WLAN-Treiber neu laden (der brcmfmac des Pi hängt sich bei schwankendem Empfang gern auf)
    danach alle 30 min wieder von vorn
Ausweich-WLANs (z. B. ein Handy-Hotspot) legt das Dashboard als NetworkManager-Verbindungen „hsp-<SSID>“ mit
niedrigerer Priorität an; ist das Haupt-WLAN weg, wählt NetworkManager selbst ein anderes bekanntes Netz. Ist man
auf einem Ausweich-WLAN und das Haupt-WLAN wieder in Reichweite, wird alle 10 min zurückgewechselt.
Hängt ein Netzwerkkabel mit Verbindung dran, bleibt das WLAN unangetastet.

Root-Handgriffe laufen über /usr/local/sbin/hamscanpi-net (sudo-Regel nur für diese Datei).
Verlauf der Ausfälle: data/wifi_log.json (die letzten 50).
"""
import json
import re
import subprocess
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
HELPER = "/usr/local/sbin/hamscanpi-net"
LOG = BASE / "data" / "wifi_log.json"
SYS_NET = Path("/sys/class/net")
STEPS = ((2, "reconnect", "neu verbunden"), (5, "radio", "WLAN aus- und wieder eingeschaltet"),
         (9, "restart-nm", "Netzwerkdienst neu gestartet"), (14, "reload-driver", "WLAN-Treiber neu geladen"))
CYCLE_MIN = 30                    # danach wieder mit Stufe 1 beginnen
WEAK_DBM, OK_DBM = -75, -70       # schwacher Empfang: Median der letzten 15 min darunter / Entwarnung darüber
BACK_TO_MAIN_S = 600


def _run(cmd, timeout=20, input=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 99, f"{type(e).__name__}: {e}"


def helper(*args, input=None, timeout=60):
    """Root-Handgriff über die sudo-Regel. Rückgabe (ok, Ausgabe)."""
    rc, out = _run(["sudo", "-n", HELPER, *[str(a) for a in args]], timeout=timeout, input=input)
    return rc == 0, out.strip()


def wifi_iface():
    try:
        return next((p.name for p in sorted(SYS_NET.iterdir()) if p.name.startswith("wl")), None)
    except OSError:
        return None


def cable_ok():
    """Netzwerkkabel mit Verbindung und Adresse (dann wird das WLAN nicht angefasst)."""
    try:
        for p in SYS_NET.iterdir():
            if p.name.startswith(("eth", "en")) and (p / "carrier").read_text().strip() == "1":
                rc, out = _run(["ip", "-4", "-o", "addr", "show", "dev", p.name], timeout=5)
                if "inet " in out:
                    return True
    except OSError:
        pass
    return False


def link(iface):
    """Verbunden? SSID und Empfangsstärke (dBm) – aus „iw dev … link“."""
    rc, out = _run(["iw", "dev", iface, "link"], timeout=5)
    if rc != 0 or "Not connected" in out or "Connected to" not in out:
        return {"connected": False, "ssid": None, "dbm": None}
    ssid = re.search(r"SSID: (.+)", out)
    sig = re.search(r"signal: (-?\d+)", out)
    return {"connected": True, "ssid": ssid.group(1).strip() if ssid else None, "dbm": int(sig.group(1)) if sig else None}


def gateway_ok(iface):
    rc, out = _run(["ip", "-4", "route", "show", "default", "dev", iface], timeout=5)
    m = re.search(r"default via (\S+)", out)
    if not m:
        return False, None
    rc, _ = _run(["ping", "-c", "1", "-W", "2", "-I", iface, m.group(1)], timeout=6)
    if rc != 0:                                     # ein verlorenes Paket ist noch kein Ausfall
        rc, _ = _run(["ping", "-c", "2", "-W", "2", "-I", iface, m.group(1)], timeout=8)
    return rc == 0, m.group(1)


def networks():
    """Bekannte WLAN-Verbindungen: [{name, ssid, prio, active, fallback}] – Ausweich-WLANs heißen „hsp-…“."""
    ok, out = helper("list", timeout=20)
    res = []
    if not ok:
        return res
    for line in out.splitlines():
        p = line.split("\t")
        if len(p) < 4:
            continue
        try:
            prio = int(p[2])
        except ValueError:
            prio = 0
        res.append({"name": p[0], "ssid": p[1], "prio": prio, "active": p[3] == "yes", "fallback": p[0].startswith("hsp-")})
    return res


def scan(iface=None):
    iface = iface or wifi_iface()
    if not iface:
        return []
    ok, out = helper("scan", iface, timeout=30)
    res = []
    for line in out.splitlines() if ok else []:
        p = line.split("\t")
        if len(p) >= 2 and p[0]:
            try:
                sig = int(p[1])
            except ValueError:
                sig = None
            res.append({"ssid": p[0], "signal": sig, "security": p[2] if len(p) > 2 else ""})
    res.sort(key=lambda x: -(x["signal"] or 0))
    return res


def add_fallback(ssid, psk=""):
    """Ausweich-WLAN anlegen; Reihenfolge = Priorität (zuerst angelegtes zuerst), immer unter dem Haupt-WLAN."""
    ssid = str(ssid or "").strip()
    if not 1 <= len(ssid) <= 32:
        raise ValueError("SSID: 1–32 Zeichen")
    if psk and not 8 <= len(psk) <= 63:
        raise ValueError("WLAN-Passwort: 8–63 Zeichen (leer lassen bei offenem Netz)")
    fb = [n for n in networks() if n["fallback"] and n["ssid"] != ssid]
    prio = min([n["prio"] for n in fb], default=-9) - 1
    ok, out = helper("add", ssid, prio, input=(psk or "") + "\n")
    if not ok:
        raise ValueError("Anlegen fehlgeschlagen: " + (out.splitlines() or ["?"])[-1])
    return prio


def remove_fallback(name):
    if not str(name).startswith("hsp-"):
        raise ValueError("Nur Ausweich-WLANs lassen sich hier löschen")
    ok, out = helper("remove", name)
    if not ok:
        raise ValueError("Löschen fehlgeschlagen: " + (out.splitlines() or ["?"])[-1])


def read_log():
    try:
        return json.loads(LOG.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write_log(entry):
    d = read_log() + [entry]
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        LOG.write_text(json.dumps(d[-50:]), encoding="utf-8")
    except OSError:
        pass


def _applog(level, event, **d):
    try:
        from applog import log
        log(level, "wifi", event, **d)
    except Exception:
        pass


def check(st, now=None, nt=None):
    """Einmal je Minute. st: Zustand aus health (wird verändert), nt: notices-Modul."""
    now = now or time.time()
    if nt is None:
        import notices as nt
    iface = wifi_iface()
    w = st.setdefault("wifi", {})
    if not iface or cable_ok():
        w.clear()
        nt.clear("wifi_down"); nt.clear("wifi_weak")
        return
    ln = link(iface)
    good = False
    if ln["connected"]:
        good, _ = gateway_ok(iface)
    # Empfangsstärke: die letzten 15 Messungen
    if ln["dbm"] is not None:
        w["dbm"] = (w.get("dbm") or [])[-14:] + [ln["dbm"]]
        med = sorted(w["dbm"])[len(w["dbm"]) // 2]
        if len(w["dbm"]) >= 15 and med < WEAK_DBM:
            nt.raise_("wifi_weak", "warn", "system", f"WLAN-Empfang schwach ({med} dBm)",
                      f"Der Pi empfängt das WLAN „{ln['ssid'] or '?'}“ seit 15 min nur mit etwa {med} dBm – unter "
                      f"{WEAK_DBM} dBm reißt die Verbindung leicht ab.",
                      fix="Pi oder Router umstellen, einen WLAN-Repeater näher an den Pi bringen, oder ein Netzwerkkabel.",
                      notify=False)
        elif med >= OK_DBM:
            nt.clear("wifi_weak")
    if good:
        if w.get("down_since"):
            mins = max(1, round((now - w["down_since"]) / 60))
            how = w.get("last_step") or "von selbst wieder verbunden"
            entry = {"from": w["down_since"], "to": now, "minutes": mins, "fixed_by": how, "ssid": ln["ssid"],
                     "steps": w.get("steps", [])}
            _write_log(entry)
            _applog("INFO", "wifi_restored", minutes=mins, fixed_by=how, ssid=ln["ssid"])
            nt.clear("wifi_down")
            nt.event("network", "info", "system", f"WLAN war {mins} min weg – wieder da",
                     f"Wiederhergestellt: {how}. Verbunden mit „{ln['ssid'] or '?'}“.")
        for k in ("down_since", "steps", "last_step", "cycle_t0"):
            w.pop(k, None)
        _back_to_main(w, ln, now)
        return
    # keine Verbindung (oder Router antwortet nicht)
    if not w.get("down_since"):
        w["down_since"] = now
        w["cycle_t0"] = now
        w["steps"] = []
        _applog("WARN", "wifi_lost", ssid=ln["ssid"], connected=ln["connected"])
        return
    if now - w["cycle_t0"] >= CYCLE_MIN * 60:                  # neue Runde
        w["cycle_t0"], w["steps"] = now, []
    mins = (now - w["cycle_t0"]) / 60
    for at, step, text in STEPS:
        if mins >= at and step not in w["steps"]:
            w["steps"].append(step)
            ok, out = helper(step, iface) if step == "reconnect" else helper(step)
            w["last_step"] = text
            _applog("WARN" if ok else "ERROR", "wifi_heal", step=step, ok=ok, output=out[-200:],
                    down_min=round((now - w["down_since"]) / 60))
            nt.raise_("wifi_down", "warn", "system", "WLAN-Verbindung weg",
                      f"Seit {__import__('timefmt').hm(w['down_since'])} keine Verbindung zum Router. "
                      f"Zuletzt versucht: {text}. Hesperos DX versucht es weiter von selbst.", notify=False)
            break


def _back_to_main(w, ln, now):
    """Auf einem Ausweich-WLAN und das Haupt-WLAN wieder in Reichweite: zurückwechseln (höchstens alle 10 min)."""
    if not (ln.get("ssid") and now - w.get("main_try", 0) >= BACK_TO_MAIN_S):
        return
    nets = networks()
    cur = next((n for n in nets if n["active"]), None)
    if not cur or not cur["fallback"]:
        return
    w["main_try"] = now
    mains = sorted((n for n in nets if not n["fallback"]), key=lambda n: -n["prio"])
    visible = {x["ssid"] for x in scan()}
    for m in mains:
        if m["ssid"] in visible:
            ok, out = helper("switch", m["name"])
            _applog("INFO", "wifi_back_to_main", ssid=m["ssid"], ok=ok)
            return


def status():
    iface = wifi_iface()
    ln = link(iface) if iface else {"connected": False, "ssid": None, "dbm": None}
    return {"iface": iface, "cable": cable_ok(), **ln, "log": read_log()[-10:][::-1],
            "helper": Path(HELPER).exists()}
