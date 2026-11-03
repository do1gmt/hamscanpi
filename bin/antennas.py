#!/usr/bin/env python3
"""Antennen-Profile: jede Antenne einmal mit Name, Frequenzbereich und (Richtantenne) Richtung, Öffnungswinkel,
Erhebung anlegen – beim Umschalten nur noch die Antenne wählen (System → Geräte oder im Scan-Kasten), wird sofort
gespeichert. Gespeichert in data/antennas.json; die Werte des gewählten Profils werden in das Geräteregister
(Haupt-Stick) bzw. die Beschriftung der PMSDR übernommen – dort lesen sie Scan, Überflugplanung und Treffer.

Beim ersten Aufruf werden die Profile aus den bisherigen Einträgen angelegt (Antennen der Sticks, PMSDR).
"""
import json
import re
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FILE = BASE / "data" / "antennas.json"
FIELDS = ("az", "bw", "el", "fmin", "fmax")


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or f"ant-{int(time.time())}"


def load():
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
        if isinstance(d.get("profiles"), list):
            return d
    except Exception:
        pass
    return _initial()


def save(d):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FILE)


def _initial():
    """Profile aus den bisherigen Angaben (Sticks, PMSDR) – einmalig."""
    profs, seen = [], set()
    try:
        import sticks as STK
        for r in STK.load()["sticks"].values():
            name = (r.get("antenna") or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            profs.append({"id": _slug(name), "name": name, "az": r.get("ant_az"), "bw": r.get("ant_bw"),
                          "el": r.get("ant_el"), "fmin": r.get("ant_fmin"), "fmax": r.get("ant_fmax")})
    except Exception:
        pass
    try:
        cfg = json.loads((BASE / "config" / "scanner.json").read_text(encoding="utf-8"))
        name = ((cfg.get("antennas") or {}).get("pmsdr") or "").strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            profs.append({"id": _slug(name), "name": name, "az": None, "bw": None, "el": None, "fmin": 0.1, "fmax": 30.0})
    except Exception:
        pass
    d = {"profiles": profs}
    save(d)
    return d


def _num(v, lo, hi, what):
    if v in (None, ""):
        return None
    v = float(str(v).replace(",", "."))
    if not lo <= v <= hi:
        raise ValueError(f"{what} zwischen {lo:g} und {hi:g}")
    return v


def set_profiles(items):
    out, ids = [], set()
    for it in items if isinstance(items, list) else []:
        name = str(it.get("name") or "").strip()[:60]
        if not name:
            continue
        pid = str(it.get("id") or _slug(name))[:40]
        while pid in ids:
            pid += "-2"
        ids.add(pid)
        p = {"id": pid, "name": name, "az": _num(it.get("az"), 0, 360, "Richtung (°)"),
             "bw": _num(it.get("bw"), 10, 360, "Öffnungswinkel (°)"), "el": _num(it.get("el"), 0, 90, "Erhebung (°)"),
             "fmin": _num(it.get("fmin"), 0.01, 6000, "Frequenz von (MHz)"), "fmax": _num(it.get("fmax"), 0.01, 6000, "Frequenz bis (MHz)")}
        if p["fmin"] is not None and p["fmax"] is not None and p["fmin"] >= p["fmax"]:
            raise ValueError(f"{name}: Frequenzbereich „von“ muss unter „bis“ liegen")
        out.append(p)
    d = {"profiles": out}
    save(d)
    return d


def current():
    """Welches Profil ist gerade gewählt (Haupt-Stick, PMSDR)?"""
    cur = {"rtlsdr": None, "pmsdr": None}
    try:
        import sticks as STK
        main = next((r for r in STK.load()["sticks"].values() if r.get("role") == "main"), None)
        if main:
            cur["rtlsdr"] = main.get("ant_profile") or None
    except Exception:
        pass
    try:
        cfg = json.loads((BASE / "config" / "scanner.json").read_text(encoding="utf-8"))
        cur["pmsdr"] = (cfg.get("antenna_profiles") or {}).get("pmsdr")
    except Exception:
        pass
    return cur


def select(target, pid, log=None):
    """Profil für den Haupt-Stick (rtlsdr) oder die PMSDR wählen – sofort wirksam."""
    prof = next((p for p in load()["profiles"] if p["id"] == pid), None)
    if prof is None:
        raise ValueError("Antennen-Profil nicht gefunden")
    old = None
    if target == "rtlsdr":
        import sticks as STK
        reg = STK.load()
        key = next((k for k, r in reg["sticks"].items() if r.get("role") == "main"), None)
        if not key:
            raise ValueError("Kein Haupt-Stick festgelegt (System → Geräte)")
        old = reg["sticks"][key].get("antenna")
        rng = f"{prof['fmin']:g}–{prof['fmax']:g}" if prof.get("fmin") is not None and prof.get("fmax") is not None else ""
        STK.update(key, antenna=prof["name"], ant_az=prof.get("az"), ant_bw=prof.get("bw"), ant_el=prof.get("el"),
                   ant_range=rng, ant_profile=prof["id"])
    elif target == "pmsdr":
        p = BASE / "config" / "scanner.json"
        cfg = json.loads(p.read_text(encoding="utf-8"))
        old = (cfg.get("antennas") or {}).get("pmsdr")
        cfg.setdefault("antennas", {})["pmsdr"] = prof["name"]
        cfg.setdefault("antenna_profiles", {})["pmsdr"] = prof["id"]
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    else:
        raise ValueError("Ziel: rtlsdr oder pmsdr")
    if log:
        log("INFO", "antennas", "antenna_changed", target=target, old=old, new=prof["name"])
    return prof
