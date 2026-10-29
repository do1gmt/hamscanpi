#!/usr/bin/env python3
"""Bandplan je Land / IARU-Region.

Grundlage ist der Bandplan in config/scanner.json (Region 1, Deutschland). Für andere Länder liegt unter
bandplans/<land>.json eine Überlagerung: Bänder entfernen (remove), ersetzen (replace, gleicher Name),
hinzufügen (add) und die rechtliche Einordnung ändern (legal). Gewählt wird das Land unter
Einstellungen → Station („country“ in config/web_settings.json); ohne Angabe gilt DE = unverändert.

Alle Programmteile, die Bänder lesen, holen sie über apply(cfg) – die Datei config/scanner.json selbst bleibt
unverändert (eigene Anpassungen dort gelten für Region 1 und bleiben beim Wechsel erhalten).
   python3 bin/bandplan.py [land]     zeigt den wirksamen Bandplan (Bandnamen, Grenzen)
"""
import copy
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PROFILES = BASE / "bandplans"
SETTINGS = BASE / "config" / "web_settings.json"
COUNTRIES = {"DE": {"name": "Deutschland", "region": 1}, "US": {"name": "USA", "region": 2}}
_cache = {}


def country(settings=None):
    if settings is None:
        try:
            settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    c = str(settings.get("country") or "DE").upper()
    return c if c in COUNTRIES else "DE"


def profile(code):
    """Überlagerung eines Landes (zwischengespeichert, neu bei geänderter Datei); None für DE."""
    if code == "DE":
        return None
    f = PROFILES / f"{code.lower()}.json"
    try:
        mt = f.stat().st_mtime
    except OSError:
        return None
    hit = _cache.get(code)
    if hit and hit[0] == mt:
        return hit[1]
    p = json.loads(f.read_text(encoding="utf-8"))
    _cache[code] = (mt, p)
    return p


def apply(cfg, settings=None, code=None):
    """Wirksamer Bandplan: cfg (aus config/scanner.json) mit der Überlagerung des gewählten Landes.
    Gibt eine Kopie zurück; ohne Überlagerung das unveränderte cfg."""
    code = code or country(settings)
    p = profile(code)
    if not p or not isinstance(cfg, dict):
        return cfg
    out = dict(cfg)
    rng = [r for r in cfg.get("ranges", []) if r.get("name") not in set(p.get("remove", []))]
    rep = {b["name"]: b for b in p.get("replace", [])}
    names = set()
    merged = []
    for r in rng:
        b = copy.deepcopy(rep.get(r["name"], r))
        if r["name"] in rep and "overrides" in r and "overrides" not in b:
            b["overrides"] = r["overrides"]                  # eigene Band-Anpassungen behalten
        merged.append(b)
        names.add(b["name"])
    for b in list(p.get("replace", [])) + list(p.get("add", [])):
        if b["name"] not in names:
            merged.append(copy.deepcopy(b))
            names.add(b["name"])
    for name, legal in (p.get("legal") or {}).items():       # z. B. Flugfunk in den USA frei hörbar
        for b in merged:
            if b["name"] == name:
                b["legal"] = legal
                for s in b.get("segments", []):
                    s["legal"] = legal
    out["ranges"] = merged
    out["bandplan_country"] = code
    out["bandplan_source"] = p.get("source", "")
    return out


def load(path=None, settings=None):
    """config/scanner.json lesen und den Bandplan des Landes anwenden."""
    path = Path(path or BASE / "config" / "scanner.json")
    return apply(json.loads(path.read_text(encoding="utf-8")), settings)


def suggest(locator):
    """Land zum Locator vorschlagen (grob: Großfelder der USA ohne Alaska/Hawaii) – nur ein Vorschlag."""
    loc = str(locator or "").upper()[:2]
    us = {"CM", "CN", "DM", "DN", "DL", "EL", "EM", "EN", "FM", "FN", "DO", "EO", "CO"}
    return "US" if loc in us else ("DE" if loc in {"JO", "JN"} else None)


if __name__ == "__main__":
    code = (sys.argv[1] if len(sys.argv) > 1 else country()).upper()
    cfg = apply(json.loads((BASE / "config" / "scanner.json").read_text(encoding="utf-8")), code=code)
    print(f"Bandplan {code}: {cfg.get('bandplan_source') or 'config/scanner.json (Region 1)'}")
    for b in cfg["ranges"]:
        print(f"  {b['name']:<10} {b['start_hz'] / 1e6:10.4f} – {b['stop_hz'] / 1e6:10.4f} MHz  "
              f"{b.get('device', 'pmsdr'):<7} {b.get('legal', '')}")
