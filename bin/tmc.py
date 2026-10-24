#!/usr/bin/env python3
"""
TMC-Verkehrsmeldungen (RDS-Gruppe 8A) in Klartext.

Daten: Bundesanstalt für Straßenwesen (BASt), Location Code List LCL 22.0 (Deutschland, LTN 1) und
Event Code List ECL 4.01, Lizenz CC BY 4.0 – umgewandelt nach share/tmc/ (devtools/build_tmc_data.py).

describe(msg, ltn) -> {"text", "road", "direction", "from", "to", "event", "lat", "lon", ...}
  msg: dict aus rds.py (event, location, direction, extent, duration, diversion, extra)
Ohne passende Ortstabelle (anderes Land, verschlüsselt) bleibt der Ort als Nummer stehen.
"""
import gzip
import json
from pathlib import Path

SHARE = Path(__file__).resolve().parent.parent / "share" / "tmc"
_cache = {}
DURATION = {0: None, 1: "bis 15 min", 2: "bis 30 min", 3: "bis 1 h", 4: "bis 2 h", 5: "bis 3 h", 6: "bis 4 h",
            7: "länger anhaltend"}


def _load(name):
    if name not in _cache:
        try:
            with gzip.open(SHARE / name, "rt", encoding="utf-8") as f:
                _cache[name] = json.load(f)
        except Exception:
            _cache[name] = None
    return _cache[name]


def lcl():
    return _load("lcl_de.json.gz")


def events():
    return _load("events_de.json.gz")


def event_text(code, quantity=None, length_km=None, speed=None, lang="de"):
    ev = (events() or {}).get("events", {}).get(str(code))
    if not ev:
        return f"Ereignis {code}" if lang == "de" else f"event {code}"
    if lang == "en" and ev.get("en"):
        t = ev["en"].replace("(Q sets of) ", "").replace("(Q) ", f"{quantity} " if quantity else "")
        t = t.replace("(Q)", str(quantity) if quantity else "").replace("(s)", "s" if (quantity or 0) > 1 else "")
        if length_km:
            t += f" for {length_km} km"
        if speed:
            t += f", max. {speed} km/h"
        return " ".join(t.split()).strip(" ,")
    t = ev["de"]
    if quantity is not None:
        t = (ev["q1"] if quantity == 1 and ev.get("q1") else ev.get("qn") or t)
        t = t.replace("(Q)", str(quantity))
    t = t.replace("(Q) ", "").replace("(Q)", "")
    had_l = "(L)" in t
    t = t.replace("(L) ", f"{length_km} km " if length_km else "").replace("(L)", f"{length_km} km" if length_km else "")
    if length_km and not had_l:
        t += f" auf {length_km} km"
    if speed:
        t += f", höchstens {speed} km/h"
    return " ".join(t.split()).strip(" ,")


def _point_name(p):
    name = p[2] or p[3] or ""
    typ = p[5] or ""
    short = {"Autobahnanschlussstelle": "AS", "Autobahnkreuz": "AK", "Autobahndreieck": "AD", "Anschlussstelle": "AS",
             "Raststätte": "Rasthof", "Rastplatz": "Rastplatz", "Tunnel": "Tunnel", "Brücke": "Brücke"}.get(typ)
    jn = f" ({p[4]})" if p[4] else ""
    return f"{short} {name}{jn}" if short else f"{name}{jn}"


def describe(msg, ltn=1):
    """Klartext einer Meldung. msg: event, location, direction (0 = positiv), extent, duration, diversion, extra."""
    ev = msg.get("event")
    extra = msg.get("extra") or {}
    out = {"event_code": ev, "location": msg.get("location")}
    out["event"] = event_text(ev, extra.get("quantity"), extra.get("length_km"), extra.get("speed"))
    more = [event_text(e) for e in extra.get("events", [])]
    sup = (events() or {}).get("supplementary", {})
    more += [sup.get(str(s), f"Zusatz {s}") for s in extra.get("supplementary", [])]
    data = lcl() if (ltn in (None, 1) and lcl() and lcl().get("ltn") == 1) else None
    loc = str(msg.get("location"))
    place = road = direction = None
    if data and loc in data["points"]:
        p = data["points"][loc]
        r = data["roads"].get(str(p[0])) if p[0] else None
        s = data["segments"].get(str(p[1])) if p[1] else None
        road = (r or s or [""])[0] or None
        # Ausdehnung: von der Ortsangabe aus gegen die Fahrtrichtung zählen (zweiter Ort)
        q, steps = p, int(msg.get("extent") or 0)
        neg = msg.get("direction", 0) == 0
        for _ in range(steps):
            nxt = q[6] if neg else q[7]
            if not nxt or str(nxt) not in data["points"]:
                break
            q = data["points"][str(nxt)]
        a, b = _point_name(q), _point_name(p)
        place = f"zwischen {a} und {b}" if q is not p and a != b else b
        ends = r or s
        if ends and ends[1] and ends[2]:
            direction = f"{ends[1]} → {ends[2]}" if msg.get("direction", 0) == 0 else f"{ends[2]} → {ends[1]}"
        out.update(lat=p[8], lon=p[9])
    elif data and loc in data["segments"]:
        s = data["segments"][loc]
        road, place = s[0], f"{s[1]} – {s[2]}"
    elif data and loc in data["areas"]:
        place = data["areas"][loc]
    else:
        place = f"Ort {loc}" + ("" if data else " (keine Ortstabelle für dieses Land)")
    out.update(road=road, direction=direction, place=place, more=more,
               duration=DURATION.get(msg.get("duration")), diversion=bool(msg.get("diversion")))
    head = " ".join(x for x in (road, direction and f"({direction})") if x)
    txt = (head + ": " if head else "") + (place + ": " if place else "") + out["event"]
    if more:
        txt += " · " + " · ".join(more)
    if out["duration"]:
        txt += f" · Dauer {out['duration']}"
    if out["diversion"]:
        txt += " · Umleitung empfohlen"
    out["text"] = txt
    # englische Fassung (Orte bleiben deutsch)
    en_ev = event_text(ev, extra.get("quantity"), extra.get("length_km"), extra.get("speed"), "en")
    en_more = [event_text(e, lang="en") for e in extra.get("events", [])]
    en_place = (place or "").replace("zwischen ", "between ").replace(" und ", " and ").replace("Ort ", "location ") \
        .replace(" (keine Ortstabelle für dieses Land)", " (no location table for this country)")
    en = (head + ": " if head else "") + (en_place + ": " if en_place else "") + en_ev
    if en_more:
        en += " · " + " · ".join(en_more)
    if out["duration"]:
        en += " · duration " + {"bis 15 min": "up to 15 min", "bis 30 min": "up to 30 min", "bis 1 h": "up to 1 h",
                                 "bis 2 h": "up to 2 h", "bis 3 h": "up to 3 h", "bis 4 h": "up to 4 h",
                                 "länger anhaltend": "longer lasting"}.get(out["duration"], out["duration"])
    if out["diversion"]:
        en += " · diversion advised"
    out["text_en"] = en
    return out
