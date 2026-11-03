#!/usr/bin/env python3
"""EiBi – Kurzwellen-Sendeplan von Eike Bierwirth (eibispace.de), frei nutzbar („free to download, use, copy,
or distribute … or to use them within third-party software“).

  sked-<Saison>.csv   ein Eintrag je Sendung: kHz; Zeit UTC; Tage; Land (ITU); Station; Sprache; Zielgebiet;
                      Senderstandort; Persistenz; Start; Ende
  README.TXT          Tabellen: Sprachen, Länder, Zielgebiete, Senderstandorte mit Koordinaten

Saison: A = Sommer (ab letztem Sonntag im März), B = Winter (ab letztem Sonntag im Oktober), z. B. B26 =
Winter 2026/27. Geholt wird sked-<aktuelle Saison>.csv, sonst sked-latest.csv, sonst die vorige Saison;
README.TXT dazu. Neu geholt bei Saisonwechsel und alle 14 Tage (EiBi pflegt die Liste laufend).
Ohne Internet bleibt die zuletzt geholte Liste in Gebrauch; eine Datei kann auch von Hand hochgeladen werden.

  on_air(khz, tol)      was laut Plan jetzt auf dieser Frequenz sendet (wahrscheinlichster zuerst)
  now_list(...)         alles, was jetzt sendet – Rundfunkbänder, filterbar
"""
import json
import math
import re
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DIR = BASE / "data" / "eibi"
CSV = DIR / "sked.csv"
README = DIR / "README.TXT"
STATE = DIR / "state.json"
URL = "http://eibispace.de/dx/"
REFRESH_S = 14 * 86400

# Kurzwellen-Rundfunkbänder (Meterbänder, kHz)
BC_BANDS = [("120m", 2300, 2495), ("90m", 3200, 3400), ("75m", 3900, 4000), ("60m", 4750, 5060), ("49m", 5900, 6200),
            ("41m", 7200, 7450), ("31m", 9400, 9900), ("25m", 11600, 12100), ("22m", 13570, 13870),
            ("19m", 15100, 15800), ("16m", 17480, 17900), ("15m", 18900, 19020), ("13m", 21450, 21850),
            ("11m", 25670, 26100)]
DAYS = {"Mo": 0, "Tu": 1, "We": 2, "Th": 3, "Fr": 4, "Sa": 5, "Su": 6}
MONTHS = {m: i + 1 for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def _log(level, event, **d):
    try:
        from applog import log
        log(level, "eibi", event, **d)
    except Exception:
        pass


# ---------------------------------------------------------------- Saison und Herunterladen
def _last_sunday(year, month):
    d = date(year, month, 31 if month in (3, 10) else 30)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def season(d=None):
    """„b26“ für den 3.11.2026, „a26“ für Juli 2026, „b25“ für Januar 2026."""
    d = d or datetime.now(timezone.utc).date()
    a_start, b_start = _last_sunday(d.year, 3), _last_sunday(d.year, 10)
    if a_start <= d < b_start:
        return f"a{d.year % 100:02d}"
    return f"b{(d.year if d >= b_start else d.year - 1) % 100:02d}"


def previous_season(s):
    return f"b{(int(s[1:]) - 1) % 100:02d}" if s[0] == "a" else f"a{s[1:]}"


def state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st):
    DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st), encoding="utf-8")


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "HamScanPi (EiBi schedule download)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def refresh(force=False):
    """Liste holen, wenn nötig (oder erzwungen). Rückgabe: Zustand. Fehler zerstören nie die vorhandene Liste."""
    st = state()
    cur = season()
    due = force or not CSV.exists() or st.get("season") != cur or time.time() - float(st.get("fetched", 0)) > REFRESH_S
    if not due:
        return st
    last_err = None
    for name in (f"sked-{cur}.csv", "sked-latest.csv", f"sked-{previous_season(cur)}.csv"):
        try:
            data = _get(URL + name)
            if b"kHz:" not in data[:200] or data.count(b"\n") < 500:
                raise ValueError("keine EiBi-Liste")
            DIR.mkdir(parents=True, exist_ok=True)
            tmp = CSV.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(CSV)
            try:
                rd = _get(URL + "README.TXT")
                if b"Transmitter site codes" in rd:
                    (README.with_suffix(".tmp")).write_bytes(rd)
                    README.with_suffix(".tmp").replace(README)
            except Exception as e:
                _log("WARN", "eibi_readme_failed", error=f"{type(e).__name__}: {e}")
            st = {"season": cur, "file": name, "fetched": time.time(), "error": None, "source": "download"}
            _save_state(st)
            _cache.clear()
            _log("INFO", "eibi_updated", file=name, lines=data.count(b"\n"))
            return st
        except Exception as e:
            last_err = f"{name}: {type(e).__name__}: {e}"
    if st.get("error") != last_err:
        _log("WARN", "eibi_download_failed", error=last_err,
             hint="die zuletzt geholte Liste bleibt in Gebrauch" if CSV.exists() else "noch keine Liste vorhanden")
    st.update(error=last_err, tried=time.time())
    _save_state(st)
    return st


def upload(data, readme=False):
    """Von Hand hochgeladene Datei (sked-*.csv oder README.TXT) übernehmen."""
    if readme:
        if b"Transmitter site codes" not in data:
            raise ValueError("Das ist keine EiBi-README.TXT")
        DIR.mkdir(parents=True, exist_ok=True)
        README.write_bytes(data)
    else:
        if b"kHz:" not in data[:200]:
            raise ValueError("Das ist keine EiBi-CSV-Liste (sked-….csv)")
        DIR.mkdir(parents=True, exist_ok=True)
        CSV.write_bytes(data)
        st = state()
        st.update(season=season(), fetched=time.time(), error=None, source="upload", file="hochgeladen")
        _save_state(st)
    _cache.clear()
    return load()["count"]


def refresh_async():
    threading.Thread(target=refresh, daemon=True).start()


# ---------------------------------------------------------------- README: Sprachen, Länder, Zielgebiete, Standorte
COORD = re.compile(r"(\d{1,2})([NS])(\d{2})(?:'(\d{1,2})\")?\s*-?\s*(\d{1,3})([EW])(\d{2})(?:'(\d{1,2})\")?")


def _coord(text):
    m = COORD.search(text or "")
    if not m:
        return None
    la = int(m.group(1)) + int(m.group(3)) / 60 + int(m.group(4) or 0) / 3600
    lo = int(m.group(5)) + int(m.group(7)) / 60 + int(m.group(8) or 0) / 3600
    return (round(-la if m.group(2) == "S" else la, 4), round(-lo if m.group(6) == "W" else lo, 4))


def _decode(b):
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("latin-1", "replace")


def parse_readme(text):
    lines = text.splitlines()

    def sect(rx):
        idx = [i for i, l in enumerate(lines) if re.match(rx, l)]
        return idx[-1] if idx else None
    i1 = sect(r"^\s+I\)\s+Language codes\.")
    i2 = sect(r"^\s+II\)\s+Country codes\.")
    i3 = sect(r"^\s+III\)\s+Target-area codes\.")
    i4 = sect(r"^\s+IV\)\s+Transmitter site codes\.")
    langs, countries, targets, sites = {}, {}, {}, {}
    if i1 is not None and i2:
        for l in lines[i1 + 1:i2]:
            m = re.match(r"^\s{2,4}(\S{1,4})\s{2,}(.+?)(\s+\[[^\]]*\])?\s*$", l)
            if m and not m.group(1).startswith("("):
                langs[m.group(1)] = re.split(r"[:(]", m.group(2))[0].strip()
    if i2 is not None and i3:
        for l in lines[i2 + 1:i3]:
            m = re.match(r"^\s{2,4}([A-Z0-9]{1,3})\s{2,}(.+?)\s*$", l)
            if m:
                countries[m.group(1)] = m.group(2).rstrip(" *")
    if i3 is not None and i4:
        for l in lines[i3 + 1:i4]:
            m = re.match(r"^\s{2,4}(\S{1,4})\s+-\s+(.+?)\s*$", l)
            if m:
                targets[m.group(1)] = m.group(2)
    if i4 is not None:
        cur = None
        for l in lines[i4 + 1:]:
            m = re.match(r"^\s{2,4}([A-Z0-9]{1,3}):\s*(.*)$", l)
            if m:
                cur, rest = m.group(1), m.group(2)
                sites.setdefault(cur, {})
            elif cur and l.startswith("        "):
                rest = l.strip()
            else:
                continue
            mm = re.match(r"^([A-Za-z0-9]{1,3})-(.+)$", rest)
            if mm:
                code, desc = mm.group(1), mm.group(2)
            else:
                code, desc = "", rest
            c = _coord(desc)
            name = COORD.split(desc)[0] if c else desc
            name = re.sub(r"\s+except:\s*$", "", name).strip(" ,;")
            if c:
                sites[cur].setdefault(code, {"name": name, "lat": c[0], "lon": c[1]})
    return {"langs": langs, "countries": countries, "targets": targets, "sites": sites}


# ---------------------------------------------------------------- CSV
def parse_csv(text):
    rows = []
    for line in text.splitlines()[1:]:
        f = line.split(";")
        if len(f) < 11:
            continue
        try:
            khz = float(f[0])
            t = f[1].strip()
            start, end = int(t[:4]), int(t[5:9])
            pers = int(f[8]) if f[8].strip().isdigit() else 1
        except (ValueError, IndexError):
            continue
        if pers in (8, 98):
            continue
        rows.append({"khz": khz, "start": start, "end": end, "days": f[2].strip(), "itu": f[3].strip(),
                     "station": f[4].strip(), "lang": f[5].strip(), "target": f[6].strip(), "site": f[7].strip(),
                     "pers": pers, "from": f[9].strip(), "to": re.sub(r"\[.*?\]", "", f[10]).strip()})
    return rows


_cache = {}


def load():
    """Liste und Tabellen laden (zwischengespeichert bis zur nächsten Änderung der Dateien)."""
    try:
        key = (CSV.stat().st_mtime, README.stat().st_mtime if README.exists() else 0)
    except OSError:
        return {"rows": [], "count": 0, "ref": {"langs": {}, "countries": {}, "targets": {}, "sites": {}}}
    if _cache.get("key") == key:
        return _cache["data"]
    rows = parse_csv(_decode(CSV.read_bytes()))
    ref = parse_readme(_decode(README.read_bytes())) if README.exists() else {"langs": {}, "countries": {}, "targets": {}, "sites": {}}
    data = {"rows": rows, "count": len(rows), "ref": ref}
    _cache.update(key=key, data=data)
    return data


# ---------------------------------------------------------------- Wann sendet ein Eintrag?
def _ddmm(s, year):
    try:
        return date(year, int(s[2:4]), int(s[:2]))
    except (ValueError, IndexError):
        return None


def day_ok(days, d):
    """(True/False, unsicher) – passt der Tag? Leer = täglich. Unbekannte Angaben: möglich, aber unsicher."""
    s = days.strip()
    if not s:
        return True, False
    wd = d.weekday()
    if s.isdigit():
        return str(wd + 1) in s, False
    m = re.fullmatch(r"(\d{1,2})([A-Z][a-z]{2})", s)                     # „15Sep“: nur an diesem Tag
    if m and m.group(2) in MONTHS:
        return d.day == int(m.group(1)) and d.month == MONTHS[m.group(2)], False
    if re.fullmatch(r"([A-Z][a-z](-[A-Z][a-z])?,?)+", s.replace(",", ",")):
        ok = False
        for part in s.split(","):
            for r in re.findall(r"[A-Z][a-z](?:-[A-Z][a-z])?", part):
                if "-" in r:
                    a, b = DAYS.get(r[:2]), DAYS.get(r[3:5])
                    if a is not None and b is not None and (a <= wd <= b if a <= b else (wd >= a or wd <= b)):
                        ok = True
                elif DAYS.get(r) == wd:
                    ok = True
        return ok, False
    if s in ("irr", "alt", "tent", "test") or s.startswith("alt"):
        return True, True
    return True, True                                                    # 1.Sa, Last7, Ram, Haj …: nicht sicher


def active(row, now):
    t = now.hour * 100 + now.minute
    s, e = row["start"], row["end"] if row["end"] else 2400
    in_time = (s <= t < e) if s < e else (t >= s or t < e) if s != e else True
    if not in_time:
        return False, False
    d = now.date()
    if s > e and t < e:                                                  # über Mitternacht: Tag des Beginns zählt
        d = d - timedelta(days=1)
    ok, unsure = day_ok(row["days"], d)
    if not ok:
        return False, False
    if row["pers"] % 90 == 6 and row["from"] and row["to"]:
        a, b = _ddmm(row["from"], now.year), _ddmm(row["to"], now.year)
        if a and b:
            if b < a:                                                    # über den Jahreswechsel
                if not (now.date() >= a or now.date() <= b):
                    return False, False
            elif not (a <= now.date() <= b):
                return False, False
    return True, unsure


def site_of(row, ref):
    """Senderstandort: „k“ im Heimatland, „/D-k“ in einem anderen Land, „/D“ dessen Hauptstandort, leer = Heimatland."""
    code = row["site"]
    host = row["itu"]
    m = re.match(r"^/([A-Z0-9]{1,3})(?:-(\w{1,3}))?", code)
    if m:
        host, code = m.group(1), m.group(2) or ""
    s = ref["sites"].get(host) or {}
    hit = s.get(code) or (s.get("") if not code else None) or (next(iter(s.values())) if len(s) == 1 else None)
    return host, hit


def _dist(lat1, lon1, lat2, lon2):
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    d = 6371.0 * math.acos(max(-1.0, min(1.0, math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl))))
    b = (math.degrees(math.atan2(math.sin(dl) * math.cos(p2), math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) + 360) % 360
    return round(d), round(b)


def _home():
    try:
        st = json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
        import satellites as SAT
        return SAT.locator_to_latlon(st.get("my_locator", "")) if st.get("my_locator") else None
    except Exception:
        return None


def band_of(khz):
    for name, a, b in BC_BANDS:
        if a <= khz <= b:
            return name
    return None


def _target_score(target, home_itu=None):
    t = target or ""
    if t in ("Eu", "CEu", "WEu", "Glo") or "Eu" in t or (home_itu and t == home_itu):
        return 0
    if t in ("ME", "NAf", "Af", "Am", "As"):
        return 2
    return 1


PREFIX = {"C": "Central", "E": "East", "N": "North", "S": "South", "W": "West"}


def target_name(code, ref):
    """Zielgebiet lesbar: „Eu“ Europe, „CEu“ Central Europe (C.. + Eu), „SAs“ South Asia, ITU-Land als Name."""
    t = ref["targets"]
    clean = lambda x: re.sub(r"\s*\(.*?\)", "", x).strip()
    if code in t:
        return clean(t[code])
    if len(code) >= 3 and code[0] in PREFIX and code[1:] in t:
        return f"{PREFIX[code[0]]} {clean(t[code[1:]])}"
    if code in ref["countries"]:
        return ref["countries"][code]
    return code


def _describe(row, ref, home, now):
    host, site = site_of(row, ref)
    d = dict(row)
    d["lang_name"] = ref["langs"].get(row["lang"], row["lang"])
    d["country"] = ref["countries"].get(row["itu"], row["itu"])
    d["target_name"] = target_name(row["target"], ref)
    d["site_name"] = site["name"] if site else None
    d["site_country"] = ref["countries"].get(host, host)
    if site and home:
        d["distance_km"], d["bearing"] = _dist(home[0], home[1], site["lat"], site["lon"])
        d["lat"], d["lon"] = site["lat"], site["lon"]
    else:
        d["distance_km"] = d["bearing"] = None
    d["band"] = band_of(row["khz"])
    d["until"] = f"{row['end'] // 100:02d}:{row['end'] % 100:02d}"
    return d


def on_air(khz, tol=5.0, now=None):
    """Was laut EiBi jetzt auf/nahe khz sendet. Sortierung: Frequenzabstand, dann Zielgebiet (Europa zuerst), dann
    Entfernung des Senders – der oberste Eintrag ist die wahrscheinlichste Erklärung für das, was man hört."""
    now = now or datetime.now(timezone.utc)
    data = load()
    ref, home = data["ref"], _home()
    out = []
    for r in data["rows"]:
        if abs(r["khz"] - khz) > tol:
            continue
        ok, unsure = active(r, now)
        if not ok:
            continue
        d = _describe(r, ref, home, now)
        d["unsure"] = unsure
        out.append(d)
    out.sort(key=lambda d: (round(abs(d["khz"] - khz), 1), _target_score(d["target"]), d["unsure"],
                            d["distance_km"] if d["distance_km"] is not None else 99999))
    return out


def now_list(band="", lang="", country="", q="", all_freqs=False, now=None, limit=2000):
    now = now or datetime.now(timezone.utc)
    data = load()
    ref, home = data["ref"], _home()
    qq = q.strip().lower()
    out = []
    for r in data["rows"]:
        b = band_of(r["khz"])
        if not all_freqs and not b:
            continue
        if band and b != band:
            continue
        if lang and r["lang"] != lang:
            continue
        if country and r["itu"] != country:
            continue
        if qq and qq not in r["station"].lower():
            continue
        ok, unsure = active(r, now)
        if not ok:
            continue
        d = _describe(r, ref, home, now)
        d["unsure"] = unsure
        out.append(d)
        if len(out) >= limit:
            break
    out.sort(key=lambda d: d["khz"])
    langs = sorted({(d["lang"], d["lang_name"]) for d in out if d["lang"]}, key=lambda x: x[1])
    ctrs = sorted({(d["itu"], d["country"]) for d in out if d["itu"]}, key=lambda x: x[1])
    return {"items": out, "langs": langs, "countries": ctrs, "bands": [b[0] for b in BC_BANDS]}


def status():
    st = state()
    data = load()
    return {"season": st.get("season"), "current_season": season(), "file": st.get("file"), "fetched": st.get("fetched"),
            "error": st.get("error"), "count": data["count"], "sites": sum(len(v) for v in data["ref"]["sites"].values()),
            "source": st.get("source")}


# ---------------------------------------------------------------- EiBi im Scan (Chip „EiBi“ unter Bänder & Betriebsarten)
SCAN_DEFAULTS = {"max_windows": 20, "europe_only": False, "seconds": 3.0, "heard_db": 15.0}
WINDOW_HZ = 30_000                                # Sendungen je Scanfenster höchstens 30 kHz auseinander (48 kHz Abtastung)


def scan_cfg(settings=None):
    if settings is None:
        try:
            settings = json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    c = dict(SCAN_DEFAULTS)
    c.update({k: v for k, v in (settings.get("eibi_scan") or {}).items() if k in SCAN_DEFAULTS})
    return c


def pseudo_band():
    """Eintrag für „Bänder & Betriebsarten“: ein Chip „EiBi“, die Meterbänder als Segmente."""
    return {"name": "EiBi", "device": "pmsdr", "legal": "public", "start_hz": BC_BANDS[0][1] * 1000,
            "stop_hz": BC_BANDS[-1][2] * 1000, "step_hz": WINDOW_HZ, "eibi": True,
            "segments": [{"id": n, "label": f"{n} {a}–{b} kHz", "mode": "am", "mode_label": "AM-Rundfunk",
                          "sideband": None, "ranges": [[a * 1000, b * 1000]], "frequencies_hz": [], "offset_hz": None,
                          "note": "Sendungen laut EiBi", "legal": "public"} for n, a, b in BC_BANDS]}


def scan_targets(segments=None, settings=None, now=None):
    """Was ein Scan jetzt prüfen soll: laut EiBi laufende Sendungen in den gewählten Meterbändern, zu Fenstern
    (40 kHz) zusammengefasst. Bei zu vielen Fenstern die mit den meisten Sendungen (und den nahen Sendern)."""
    c = scan_cfg(settings)
    segs = set(segments or ["all"])
    items = [d for d in now_list(now=now)["items"]
             if ("all" in segs or d["band"] in segs)
             and (not c["europe_only"] or _target_score(d["target"]) == 0)]
    windows = []
    for d in sorted(items, key=lambda x: x["khz"]):
        f = int(round(d["khz"] * 1000))
        if windows and f - windows[-1]["lo"] <= WINDOW_HZ:
            windows[-1]["items"].append(d)
            windows[-1]["hi"] = f
        else:
            windows.append({"lo": f, "hi": f, "items": [d]})
    for w in windows:
        w["center_hz"] = int((w["lo"] + w["hi"]) / 2)
        # nicht genau auf einen Träger abstimmen: in der Fenstermitte liegt der Gleichspannungsrest des Empfängers
        if any(abs(x["khz"] * 1000 - w["center_hz"]) < 1000 for x in w["items"]):
            w["center_hz"] += 2500
        near = [x["distance_km"] for x in w["items"] if x.get("distance_km") is not None]
        w["score"] = len(w["items"]) + (2 if near and min(near) < 2500 else 0)
    keep = sorted(windows, key=lambda w: -w["score"])[: int(c["max_windows"])]
    return sorted(keep, key=lambda w: w["center_hz"])


def ensure_log(db):
    db.execute("""CREATE TABLE IF NOT EXISTS eibi_log (id INTEGER PRIMARY KEY, time TEXT, khz REAL, station TEXT,
                  itu TEXT, lang TEXT, target TEXT, site TEXT, distance_km REAL, snr_db REAL, heard INTEGER,
                  band TEXT, antenna TEXT)""")
    db.execute("CREATE INDEX IF NOT EXISTS eibi_log_time ON eibi_log(time)")


def measure(i, q, rate, center_hz, items, conj=False):
    """Trägerpegel jeder Sendung im Fenster: stärkste Linie ±300 Hz um die Sollfrequenz gegen das Grundrauschen
    (Median des Spektrums) – AM-Rundfunk hat einen kräftigen Träger, der den Empfang verlässlich anzeigt."""
    from scipy.signal import welch
    z = (np.asarray(i) + 1j * np.asarray(q)).astype(np.complex64)
    if conj:
        z = np.conj(z)
    # detrend=False: der Träger kann genau in der Fenstermitte liegen (0 Hz) – „constant“ würde ihn entfernen
    f, p = welch(z, fs=rate, nperseg=min(len(z), 16384), return_onesided=False, scaling="density", detrend=False)
    order = np.argsort(f)
    f, p = f[order], p[order]
    floor = float(np.median(p)) or 1e-20
    out = []
    for d in items:
        off = d["khz"] * 1000 - center_hz
        m = (f >= off - 300) & (f <= off + 300)
        snr = 10 * np.log10(float(p[m].max()) / floor) if m.any() else None
        out.append((d, round(snr, 1) if snr is not None else None))
    return out


import numpy as np  # noqa: E402  (nur für den Scan gebraucht)
