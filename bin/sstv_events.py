#!/usr/bin/env python3
"""SSTV-Termine: wann die ISS (und andere Satelliten) Bilder senden.

Eine offizielle, maschinenlesbare Liste gibt es nicht. Darum einmal am Tag zwei öffentliche Quellen auswerten und
dazu die eigenen Einträge und den eigenen Empfang:

  - Libre-Space-Forum (Discourse, RSS der Kategorie „Satellites & Observations“): Themen heißen seit Jahren
    „ISS SSTV Event - from 2026-05-08 to 2026-05-12“ – verlässliche Tage, keine Uhrzeiten.
  - AMSAT-UK (WordPress-RSS): „An SSTV event on the ISS is scheduled to begin on Thursday October 22, 2026 around
    15:10 UTC and will end on Monday October 26, 2026 at 17:45 UTC.“ – mit Uhrzeiten.
  - eigene Einträge (Einstellungen „sstv_events_manual“), z. B. für Aktionen anderer Satelliten.
  - eigener Empfang: SSTV-Bilder der letzten 48 h je Satellitenkanal („läuft gerade“, auch ohne Ankündigung).

Termine derselben Quelle bzw. desselben Satelliten, die sich überschneiden, werden zusammengeführt: Uhrzeiten aus der
genauesten Quelle, alle Links zur Ankündigung bleiben. Fällt eine Quelle aus, bleiben ihre zuletzt geholten Termine.
Geschrieben wird data/sstv_events.json höchstens einmal je Abruf (Speicherkarte schonen).
"""
import html
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from calendar import timegm
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FILE = BASE / "data" / "sstv_events.json"
SETTINGS = BASE / "config" / "web_settings.json"
REFRESH_S = 86400
KEEP_ENDED_S = 3 * 86400            # beendete Termine noch 3 Tage zeigen (Bilder einreichen, Diplom)
SOURCES = {
    "libre": {"name": "Libre-Space-Forum", "url": "https://community.libre.space/c/satellites-observations/16.rss"},
    "amsatuk": {"name": "AMSAT-UK", "url": "https://amsat-uk.org/feed/"},
}
UA = "HesperosDX/1.0 (+https://github.com/do1gmt/hesperos-dx; SSTV event calendar)"
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
MONTHS.update({"mär": 3, "mai": 5, "okt": 10, "dez": 12})
FREQ_RX = re.compile(r"\b(14[45]\.\d{2,3}|43[5-8]\.\d{2,3})\s*MHz", re.I)
MODE_RX = re.compile(r"\b(PD[\s-]?(?:90|120|180)|Robot[\s-]?(?:36|72)|Scottie[\s-]?(?:1|2|DX)|Martin[\s-]?[12])\b", re.I)


def _log(level, event, **d):
    try:
        from applog import log
        log(level, "sstv_events", event, **d)
    except Exception:
        pass


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(3_000_000)


def _text(s):
    """HTML einer Feed-Beschreibung zu schlichtem Text."""
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t\xa0]+", " ", html.unescape(s)).strip()


def _items(xml_bytes):
    """RSS-Einträge: [{title, link, text}]."""
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.iter("item"):
        g = lambda tag: (it.findtext(tag) or "")
        body = it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or g("description")
        out.append({"title": html.unescape(g("title")).strip(), "link": g("link").strip(), "text": _text(body)})
    return out


def _day(y, m, d, hm=None):
    hh, mm = (int(x) for x in hm.split(":")) if hm else (0, 0)
    return timegm((int(y), int(m), int(d), hh, mm, 0, 0, 0, 0))


def _details(text):
    f = FREQ_RX.search(text or "")
    m = MODE_RX.search(text or "")
    return (f.group(1) if f else ""), (_mode_name(m.group(1)) if m else "")


def _mode_name(s):
    """„pd-120“ → „PD120“, „robot36“ → „Robot 36“, „scottie 1“ → „Scottie 1“."""
    k = re.sub(r"[\s-]+", "", s or "").lower()
    for pre, name in (("pd", "PD"), ("robot", "Robot "), ("scottie", "Scottie "), ("martin", "Martin ")):
        if k.startswith(pre):
            return (name + k[len(pre):].upper()).strip()
    return s


# ---------------------------------------------------------------- Quellen auswerten
TITLE_RX = re.compile(r"SSTV.*?(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}:\d{2}))?\s*(?:UTC)?\s*(?:to|bis|-|–)\s*"
                      r"(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}:\d{2}))?", re.I)


def parse_libre(items):
    """Forum: „ISS SSTV Event - from 2026-05-08 to 2026-05-12“ (auch mit Uhrzeiten, auch andere Satelliten)."""
    out = []
    for it in items:
        m = TITLE_RX.search(it["title"])
        if not m:
            continue
        y1, m1, d1, t1, y2, m2, d2, t2 = m.groups()
        sat = it["title"][:it["title"].upper().find("SSTV")].strip(" -–:") or "ISS"
        freq, mode = _details(it["text"])
        out.append({"sat": sat, "start": _day(y1, m1, d1, t1), "end": _day(y2, m2, d2, t2 or "23:59"),
                    "dates_only": not (t1 and t2), "freq": freq, "mode": mode, "title": it["title"],
                    "links": [it["link"]] if it["link"] else [], "source": "libre"})
    return out


_DATE = (r"(?:(?:mon|tue|wed|thu|fri|sat|sun)\w*,?\s+)?"
         r"(?:(?P<d1>\d{1,2})(?:st|nd|rd|th)?\s+(?P<m1>[a-zäöü]{3,})\.?,?|(?P<m2>[a-zäöü]{3,})\.?\s+(?P<d2>\d{1,2})(?:st|nd|rd|th)?,?)"
         r"\s+(?P<y>\d{4}),?\s+(?:at\s+|around\s+|approximately\s+|at\s+approximately\s+|about\s+|~\s*)*(?P<t>\d{1,2}:\d{2})\s*UTC")
DT_RX = re.compile(_DATE, re.I)


def _dt(m):
    mon = (m.group("m1") or m.group("m2") or "")[:3].lower()
    if mon not in MONTHS:
        return None
    try:
        return _day(m.group("y"), MONTHS[mon], m.group("d1") or m.group("d2"), m.group("t"))
    except (ValueError, OverflowError):
        return None


def parse_amsatuk(items):
    """AMSAT-UK: Satz mit Beginn und Ende samt Uhrzeit („begin on … 15:10 UTC and will end on … 17:45 UTC“)."""
    out = []
    for it in items:
        if not re.search(r"SSTV|slow scan", it["title"] + " " + it["text"], re.I):
            continue
        sents = re.split(r"(?<=[.!])\s+|\n", it["text"])
        # zuerst Sätze, die selbst SSTV nennen; sonst jeder Satz mit Beginn und Ende (der Beitrag handelt von SSTV)
        sents = [x for x in sents if re.search(r"SSTV|slow scan", x, re.I)] + [x for x in sents if not re.search(r"SSTV|slow scan", x, re.I)]
        for sent in sents:
            if not re.search(r"begin|began|start|kick", sent, re.I):
                continue
            ds = [x for x in (_dt(m) for m in DT_RX.finditer(sent)) if x]
            if len(ds) >= 2 and ds[1] > ds[0]:
                freq, mode = _details(it["text"])
                sat = "ISS" if re.search(r"\bISS\b|space station", it["title"] + " " + it["text"], re.I) else it["title"].split()[0]
                out.append({"sat": sat, "start": ds[0], "end": ds[1], "dates_only": False, "freq": freq, "mode": mode,
                            "title": it["title"], "links": [it["link"]] if it["link"] else [], "source": "amsatuk"})
                break
    return out


PARSERS = {"libre": parse_libre, "amsatuk": parse_amsatuk}


# ---------------------------------------------------------------- Zusammenführen
def _same(a, b):
    """Derselbe Termin: derselbe Satellit (ISS ~ ISS), Zeiträume überschneiden sich (Tagesgrenzen großzügig)."""
    na, nb = a["sat"].upper().split()[0] if a["sat"] else "", b["sat"].upper().split()[0] if b["sat"] else ""
    return na == nb and a["start"] <= b["end"] + 86400 and b["start"] <= a["end"] + 86400


def merge(events):
    out = []
    for e in sorted(events, key=lambda x: x["start"]):
        for o in out:
            if _same(o, e):
                if o["dates_only"] and not e["dates_only"]:          # genaue Uhrzeiten gewinnen
                    o.update(start=e["start"], end=e["end"], dates_only=False)
                o["links"] = o["links"] + [l for l in e["links"] if l not in o["links"]]
                o["sources"] = sorted(set(o.get("sources", [o.get("source")])) | {e.get("source")})
                o["freq"] = o["freq"] or e["freq"]
                o["mode"] = o["mode"] or e["mode"]
                break
        else:
            out.append(dict(e, sources=[e.get("source")]))
    for e in out:
        e["id"] = f"{e['sat'].split()[0].lower() if e['sat'] else 'x'}-{time.strftime('%Y%m%d', time.gmtime(e['start']))}"
    return out


# ---------------------------------------------------------------- Abrufen und Lesen
def load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"events": {}, "sources": {}}


def refresh(force=False, fetch=_get):
    """Quellen abrufen, wenn fällig (oder erzwungen). Fehler einer Quelle lassen deren letzte Termine stehen."""
    st = load()
    if not force and time.time() - float(st.get("fetched", 0)) < REFRESH_S:
        return st
    st.setdefault("events", {}); st.setdefault("sources", {})
    for key, src in SOURCES.items():
        info = st["sources"].setdefault(key, {})
        try:
            evs = PARSERS[key](_items(fetch(src["url"])))
            # nur Termine, die noch nicht lange vorbei sind (die Feeds reichen weit zurück)
            evs = [e for e in evs if e["end"] > time.time() - 30 * 86400]
            st["events"][key] = evs
            info.update(ok_at=time.time(), error=None, n=len(evs))
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:200]
            if info.get("error") != err:
                _log("WARN", "sstv_events_source_failed", source=src["name"], error=err)
            info.update(error=err, tried=time.time())
    st["fetched"] = time.time()
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    tmp.replace(FILE)
    _log("INFO", "sstv_events_fetched", **{k: (v.get("n") if not v.get("error") else "Fehler") for k, v in st["sources"].items()})
    return st


def manual(settings=None):
    if settings is None:
        try:
            settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    out = []
    for i, m in enumerate(settings.get("sstv_events_manual") or []):
        try:
            out.append({"sat": m.get("sat") or "ISS", "start": float(m["start"]), "end": float(m["end"]),
                        "dates_only": bool(m.get("dates_only")), "freq": m.get("freq", ""), "mode": m.get("mode", ""),
                        "title": m.get("title") or "", "links": [m["link"]] if m.get("link") else [],
                        "source": "manual", "manual_index": i})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def clean_manual(item):
    """Eintrag aus dem Dashboard prüfen: Satellit, Beginn/Ende (UTC, Datum oder Datum+Uhrzeit), Frequenz, Modus, Link."""
    sat = str(item.get("sat") or "").strip()[:40]
    if not sat:
        raise ValueError("Satellit fehlt")

    def ts(v, end=False):
        v = str(v or "").strip()
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}:\d{2}))?", v)
        if not m:
            raise ValueError("Zeit als JJJJ-MM-TT oder JJJJ-MM-TT HH:MM")
        return _day(*m.groups()[:3], m.group(4) or ("23:59" if end else None)), not m.group(4)
    s, s_day = ts(item.get("from"))
    e, e_day = ts(item.get("to"), end=True)
    if e <= s:
        raise ValueError("Ende liegt vor dem Beginn")
    link = str(item.get("link") or "").strip()
    if link and not re.fullmatch(r"https?://\S{3,300}", link):
        raise ValueError("Link muss mit http:// oder https:// beginnen")
    freq, mode = _details(f"{item.get('freq', '')} MHz {item.get('mode', '')}")
    return {"sat": sat, "start": s, "end": e, "dates_only": s_day and e_day, "freq": freq or "", "mode": mode or _mode_name(str(item.get("mode") or "")[:20]),
            "title": str(item.get("title") or "")[:80], "link": link}


def events(now=None, settings=None):
    """Alle Termine (Quellen + eigene), zusammengeführt, mit Zustand: läuft / kommt / vorbei – ohne lang Vergangenes."""
    now = now or time.time()
    st = load()
    allev = [e for evs in (st.get("events") or {}).values() for e in evs] + manual(settings)
    out = []
    for e in merge(allev):
        if e["end"] < now - KEEP_ENDED_S:
            continue
        e["state"] = "running" if e["start"] <= now <= e["end"] else ("upcoming" if e["start"] > now else "ended")
        out.append(e)
    out.sort(key=lambda e: ({"running": 0, "upcoming": 1, "ended": 2}[e["state"]], e["start"]))
    return out


def status():
    st = load()
    return {"fetched": st.get("fetched"),
            "sources": [{"key": k, "name": SOURCES[k]["name"], "url": SOURCES[k]["url"], **(st.get("sources") or {}).get(k, {})}
                        for k in SOURCES]}


def received(db, hours=48, now=None):
    """Eigener Empfang: SSTV-Bilder je Satellitenkanal in den letzten Stunden – [{id, label, last, n}]."""
    now = now or time.time()
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - hours * 3600))
    try:
        rows = db.execute("""SELECT p.segment_id, COUNT(*) n, MAX(s.time) last FROM peak p JOIN scan s ON s.id = p.scan_id
                             WHERE p.mode = 'sstv' AND s.band = 'Satelliten' AND s.time >= ? GROUP BY p.segment_id""",
                          (since,)).fetchall()
    except Exception:
        return []
    try:
        import satellites as SAT
        labels = {c["id"]: c.get("label", c["id"]) for c in SAT.SATELLITES}
    except Exception:
        labels = {}
    return sorted(({"id": r[0], "label": labels.get(r[0], r[0]), "n": r[1], "last": r[2]} for r in rows if r[0]),
                  key=lambda x: x["last"], reverse=True)


def received_in(db, ev):
    """Wie viele SSTV-Bilder habe ich während dieses Termins selbst empfangen (gleicher Satellit)?"""
    sat = (ev.get("sat") or "").upper().split()[0] if ev.get("sat") else ""
    try:
        import satellites as SAT
        ids = [c["id"] for c in SAT.SATELLITES if c.get("sstv") and (c["id"].upper().startswith(sat) or sat in c.get("label", "").upper())]
    except Exception:
        ids = []
    if not ids:
        return 0
    a = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ev["start"] - 3600))
    b = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ev["end"] + 3600))
    try:
        return db.execute(f"""SELECT COUNT(*) FROM peak p JOIN scan s ON s.id = p.scan_id WHERE p.mode = 'sstv'
                              AND s.time BETWEEN ? AND ? AND p.segment_id IN ({','.join('?' * len(ids))})""",
                          (a, b, *ids)).fetchone()[0]
    except Exception:
        return 0


if __name__ == "__main__":
    import sys
    st = refresh(force="--force" in sys.argv)
    for e in events():
        print(e["state"], time.strftime("%Y-%m-%d %H:%M", time.gmtime(e["start"])), "–",
              time.strftime("%Y-%m-%d %H:%M", time.gmtime(e["end"])), e["sat"], e["freq"], e["mode"], " ".join(e["links"]))
