#!/usr/bin/env python3
"""
Gehörte Stationen und Beobachtungsliste.

Gehörte Stationen
  Aus den Dekodierungen der Treffer werden die Rufzeichen der *sendenden* Stationen gezogen:
    FT8/FT4  „CQ DL1ABC JO31“ → DL1ABC (Locator JO31); „OK1XYZ DL1ABC -12“ → DL1ABC (der zweite sendet)
    APRS     „DL1ABC → APRS über ARISS: !5030.00N/01135.00E…“ → DL1ABC, Position → Locator
    CW/RTTY  Rufzeichen nach „DE“, sonst Rufzeichen, die mindestens zweimal vorkommen
  Tabelle heard in der Datenbank; Entfernung und Richtung rechnet die Webapp aus dem eigenen Locator.
  Export als ADIF 3.1 (SWL-Log: CALL, QSO_DATE, TIME_ON, BAND, MODE, FREQ, GRIDSQUARE, RST_RCVD).

Beobachtungsliste (config/web_settings.json → watchlist)
  {"id": "...", "type": "freq"|"call"|"event"|"dx", "value": "...", "label": "...", "enabled": true,
   "tolerance_khz": 5, "mail": true, "quiet_min": 30}
    freq   Treffer innerhalb ±tolerance_khz um value (MHz)
    call   Rufzeichen gehört (value, * als Platzhalter, z. B. DL1ABC oder DL*)
    event  iss_sstv | sstv | aprs_iss | satellite
    dx     Station weiter als value km entfernt (FT8/APRS mit Locator)
  Jeder Treffer einer Regel steht im Protokoll (watch_hit), auf der Trefferkarte (🔔) und – wenn
  Mail eingerichtet und bei der Regel erlaubt – als Mail (höchstens eine je Regel und quiet_min).
"""
import fnmatch
import json
import math
import re
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "scanner.db"
REPORTS = BASE / "reports"

CALL_RX = re.compile(r"\b((?:[A-Z]{1,2}|[0-9][A-Z]|[A-Z][0-9])[0-9][A-Z0-9]{0,3}[A-Z](?:/[A-Z0-9]{1,4})?)\b")
GRID_RX = re.compile(r"^[A-R]{2}[0-9]{2}([A-X]{2})?$")
NOT_CALLS = {"RR73", "RRR", "CQ", "DX", "QRZ", "TEST", "DE", "TNX", "73"}
APRS_POS = re.compile(r"[!=/@](?:\d{6}[hz/])?(\d{2})(\d{2}\.\d{2})([NS]).(\d{3})(\d{2}\.\d{2})([EW])")
# Regeltyp „traffic“: TMC-Verkehrsmeldungen (Scan des Bands Rundfunk und Live-Empfang)
EVENTS = {
    "iss_sstv": "SSTV-Bild von der ISS",
    "sstv": "SSTV-Bild (alle)",
    "aprs_iss": "APRS über die ISS",
    "satellite": "Satellit gehört (alle Treffer im Band Satelliten)",
}


def ensure_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS heard (
        id INTEGER PRIMARY KEY, peak_id INTEGER, time TEXT, call TEXT, band TEXT, freq_hz REAL,
        mode TEXT, grid TEXT, snr REAL, info TEXT, UNIQUE(peak_id, call))""")
    db.execute("CREATE INDEX IF NOT EXISTS heard_call ON heard(call)")
    db.execute("CREATE INDEX IF NOT EXISTS heard_time ON heard(time)")
    db.execute("""CREATE TABLE IF NOT EXISTS watch_hit (
        id INTEGER PRIMARY KEY, peak_id INTEGER, rule_id TEXT, label TEXT, time TEXT, detail TEXT,
        UNIQUE(peak_id, rule_id))""")
    db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    db.execute("""CREATE TABLE IF NOT EXISTS fm_station (
        freq_khz INTEGER, pi TEXT, ps TEXT, pty TEXT, rt TEXT, now_playing TEXT, ecc TEXT, af TEXT,
        first TEXT, last TEXT, n INTEGER, best_snr REAL, peak_id INTEGER, PRIMARY KEY (freq_khz, pi))""")
    row = db.execute("SELECT v FROM meta WHERE k='heard_parser'").fetchone()
    if not row or int(row[0]) < PARSER_VERSION:
        # Erkennung verbessert: alle Treffer noch einmal auswerten, ohne die Beobachtungsliste auszulösen
        mx = db.execute("SELECT COALESCE(MAX(id), 0) FROM peak").fetchone()[0] if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='peak'").fetchone() else 0
        db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('heard_last_peak', '0')")
        db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('watch_skip_until', ?)", (str(mx),))
        db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('heard_parser', ?)", (str(PARSER_VERSION),))
        db.commit()


def is_call(tok):
    tok = tok.strip("<>").upper()
    if tok in NOT_CALLS or GRID_RX.match(tok) or not CALL_RX.fullmatch(tok):
        return False
    return any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok)


def latlon_to_grid(lat, lon):
    lon += 180.0
    lat += 90.0
    g = chr(65 + int(lon // 20)) + chr(65 + int(lat // 10))
    g += str(int((lon % 20) // 2)) + str(int(lat % 10))
    g += chr(97 + int((lon % 2) * 12)).upper() + chr(97 + int((lat % 1) * 24)).upper()
    return g


def ft8_senders(messages):
    """[(call, grid, snr, text)] der sendenden Stationen einer FT8/FT4-Dekodierung."""
    out = []
    for m in messages or []:
        toks = str(m.get("text", "")).upper().split()
        if not toks:
            continue
        call = grid = None
        if toks[0] == "CQ":
            rest = toks[1:]
            if rest and not is_call(rest[0]) and len(rest) > 1:      # „CQ DX DL1ABC JO31“
                rest = rest[1:]
            if rest and is_call(rest[0]):
                call = rest[0].strip("<>")
                if len(rest) > 1 and GRID_RX.match(rest[1]) and rest[1] != "RR73":
                    grid = rest[1]
        elif len(toks) >= 2 and is_call(toks[1]):
            call = toks[1].strip("<>")
            if len(toks) > 2 and GRID_RX.match(toks[2]) and toks[2] != "RR73":
                grid = toks[2]
        if call:
            out.append((call, grid, m.get("snr"), m.get("text", ""), m.get("audio_hz")))
    return out


PARSER_VERSION = 3               # bei Änderungen an der Erkennung werden alte Treffer neu ausgewertet


def aprs_senders(text):
    """APRS-Absender. Formate: „DL1ABC → APRS über …: …“ (Satelliten), roh von multimon-ng
    „AFSK1200: fm DL1ABC-1 to APDW18-0 via …“ und Drittverkehr „}DB0BH>APGE01,…:!4926.10N/…“."""
    out = []
    lines = str(text or "").splitlines()
    for idx, line in enumerate(lines):
        up = line.upper()
        m = (re.match(r"\s*(?:AFSK1200:\s*)?FM\s+([A-Z0-9]{1,6}(?:-\d{1,2})?)\s+TO\s", up)
             or re.match(r"\s*\}?([A-Z0-9]{1,6}(?:-\d{1,2})?)\s*(?:→|>)", up))
        if not m:
            continue
        call = m.group(1)
        if up.lstrip().startswith(("AFSK1200", "FM ")) and idx + 1 < len(lines) \
                and not lines[idx + 1].lstrip().startswith("}"):     # „}“ = Drittverkehr, eigene Position
            line = line + " " + lines[idx + 1]                 # Position steht bei multimon-ng in der nächsten Zeile
        base = call.split("-")[0]
        if not is_call(base):
            continue
        grid = None
        p = APRS_POS.search(line)
        if p:
            lat = int(p.group(1)) + float(p.group(2)) / 60
            lat = lat if p.group(3) == "N" else -lat
            lon = int(p.group(4)) + float(p.group(5)) / 60
            lon = lon if p.group(6) == "E" else -lon
            if -90 < lat < 90 and -180 < lon < 180:
                grid = latlon_to_grid(lat, lon)
        out.append((call, grid, None, line.strip()))
    return out


def text_senders(text):
    """CW/RTTY: Rufzeichen nach „DE“, sonst solche, die mindestens zweimal vorkommen."""
    t = str(text or "").upper()
    de = [m.group(1) for m in re.finditer(r"\bDE\s+([A-Z0-9/]{3,12})", t) if is_call(m.group(1))]
    if de:
        return [(c, None, None, "") for c in dict.fromkeys(de)]
    calls = [c for c in CALL_RX.findall(t) if is_call(c)]
    return [(c, None, None, "") for c in dict.fromkeys(calls) if calls.count(c) >= 2]


def senders_for(decode):
    """[(call, grid, snr, text, mode, freq_hz|None)]"""
    out = []
    for d in decode.get("decoders", []):
        typ = str(d.get("type", "")).upper()
        if typ in ("FT8", "FT4"):
            m = re.search(r"Dial\s+([\d.]+)\s*MHz", str(d.get("detail", "")))
            dial = float(m.group(1)) * 1e6 if m else None
            out += [(c, g, s, t, typ, (dial + a) if dial and a is not None else None)
                    for c, g, s, t, a in ft8_senders(d.get("messages"))]
            continue
        if typ == "APRS":
            out += [(c, g, s, t, "APRS", None) for c, g, s, t in aprs_senders(d.get("text"))]
        elif typ in ("CW", "RTTY") and d.get("validated", True):
            out += [(c, g, s, t, typ, None) for c, g, s, t in text_senders(d.get("text"))]
    return out


def decode_of(recording_path):
    try:
        return json.loads((REPORTS / f"{Path(recording_path).stem}_decode.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def ingest(db, limit=2000):
    """Neue Treffer seit dem letzten Lauf auswerten. Rückgabe Liste dict(peak…, calls=[...])."""
    ensure_tables(db)
    row = db.execute("SELECT v FROM meta WHERE k='heard_last_peak'").fetchone()
    last = int(row[0]) if row else 0
    rows = db.execute("""SELECT p.id, p.rf_hz, p.mode, p.segment_id, p.recording_path, p.snr_db, s.time, s.band
                         FROM peak p JOIN scan s ON s.id = p.scan_id WHERE p.id > ? ORDER BY p.id LIMIT ?""",
                      (last, limit)).fetchall()
    out = []
    for r in rows:
        pid, rf, mode, seg, rec, snr, t, band = r
        dec = decode_of(rec) if rec else None
        calls = []
        if dec:
            for call, grid, s, text, typ, f in senders_for(dec):
                db.execute("""INSERT OR IGNORE INTO heard (peak_id, time, call, band, freq_hz, mode, grid, snr, info)
                              VALUES (?,?,?,?,?,?,?,?,?)""", (pid, t, call, band, f or rf, typ, grid, s, text[:200]))
                calls.append({"call": call, "grid": grid, "mode": typ})
        for d in (dec or {}).get("decoders", []):
            if d.get("type") == "RDS" and d.get("rds"):
                upsert_fm(db, pid, rf, t, snr, d["rds"])
        img = REPORTS / f"{Path(rec).stem}_sstv.png" if rec else None
        out.append({"id": pid, "rf_hz": rf, "mode": mode, "segment_id": seg, "time": t, "band": band,
                    "decode": dec, "calls": calls, "image": str(img) if img and img.exists() else None})
        last = pid
    db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('heard_last_peak', ?)", (str(last),))
    db.commit()
    return out


def upsert_fm(db, pid, rf, t, snr, info):
    """UKW-Senderliste: je Frequenz (100 kHz) und PI ein Eintrag, Namen/Text vom letzten Empfang."""
    fk = int(round(float(rf) / 100000.0)) * 100
    pi = info.get("pi") or "?"
    rp = info.get("rtplus") or {}
    now = " – ".join(x for x in (rp.get("artist"), rp.get("title")) if x) or None
    ps = (info.get("ps") or "").strip() or None
    row = db.execute("SELECT n, ps, rt, best_snr FROM fm_station WHERE freq_khz=? AND pi=?", (fk, pi)).fetchone()
    if row:
        db.execute("""UPDATE fm_station SET ps=?, pty=?, rt=?, now_playing=?, ecc=?, af=?, last=?, n=?, best_snr=?, peak_id=?
                      WHERE freq_khz=? AND pi=?""",
                   (ps or row[1], info.get("pty_name"), info.get("rt") or row[2], now, info.get("ecc"),
                    ",".join(str(x) for x in info.get("af") or []), t, row[0] + 1,
                    max(float(row[3] or -99), float(snr or -99)), pid, fk, pi))
    else:
        db.execute("""INSERT INTO fm_station (freq_khz, pi, ps, pty, rt, now_playing, ecc, af, first, last, n, best_snr, peak_id)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (fk, pi, ps, info.get("pty_name"), info.get("rt"), now, info.get("ecc"),
                    ",".join(str(x) for x in info.get("af") or []), t, t, 1, snr, pid))


# ------------------------------------------------------------------ Entfernung, ADIF

def distance_bearing(grid, my_locator):
    import satellites as SAT
    try:
        la1, lo1 = SAT.locator_to_latlon(my_locator)
        la2, lo2 = SAT.locator_to_latlon(grid)
    except Exception:
        return None, None
    p1, p2, dl = math.radians(la1), math.radians(la2), math.radians(lo2 - lo1)
    d = 6371.0 * math.acos(max(-1.0, min(1.0, math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl))))
    b = (math.degrees(math.atan2(math.sin(dl) * math.cos(p2),
                                 math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) + 360) % 360
    return round(d), round(b)


ADIF_BAND = [(1.8e6, 2.0e6, "160m"), (3.5e6, 4.0e6, "80m"), (5.06e6, 5.45e6, "60m"), (7.0e6, 7.3e6, "40m"),
             (10.1e6, 10.15e6, "30m"), (14.0e6, 14.35e6, "20m"), (18.068e6, 18.168e6, "17m"),
             (21.0e6, 21.45e6, "15m"), (24.89e6, 24.99e6, "12m"), (28.0e6, 29.7e6, "10m"), (50e6, 54e6, "6m"),
             (70e6, 71e6, "4m"), (144e6, 148e6, "2m"), (430e6, 440e6, "70cm"), (1240e6, 1300e6, "23cm")]


def adif_band(f):
    for a, b, n in ADIF_BAND:
        if a <= f <= b:
            return n
    return ""


def _f(name, value):
    v = str(value)
    return f"<{name}:{len(v.encode('utf-8'))}>{v} " if v != "" else ""


def adif(rows, my_call="", my_grid=""):
    """rows: dicts aus heard. SWL-Log: jede gehörte Aussendung ein Eintrag."""
    from datetime import datetime, timezone
    head = ("HamScanPi SWL log\n" + _f("ADIF_VER", "3.1.4") + _f("PROGRAMID", "HamScanPi")
            + _f("CREATED_TIMESTAMP", datetime.now(timezone.utc).strftime("%Y%m%d %H%M%S")) + "<EOH>\n")
    out = [head]
    for r in rows:
        t = str(r["time"])
        rec = (_f("CALL", r["call"]) + _f("QSO_DATE", t[0:10].replace("-", "")) + _f("TIME_ON", t[11:19].replace(":", ""))
               + _f("BAND", adif_band(float(r["freq_hz"] or 0))) + _f("MODE", r["mode"] or "")
               + _f("FREQ", f"{float(r['freq_hz'] or 0) / 1e6:.6f}") + _f("GRIDSQUARE", r["grid"] or "")
               + (_f("RST_RCVD", f"{int(round(r['snr'])):+d}") if r["snr"] is not None else "")
               + _f("STATION_CALLSIGN", my_call) + _f("MY_GRIDSQUARE", my_grid) + _f("SWL", "Y")
               + _f("COMMENT", "SWL HamScanPi") + "<EOR>\n")
        out.append(rec)
    return "".join(out)


# ------------------------------------------------------------------ Beobachtungsliste

def traffic_match(val, m):
    """Passt eine Verkehrsmeldung zur Regel? Straßennummer genau (A9 ≠ A93), sonst Text enthält Wert."""
    v = val.strip().upper()
    if not v:
        return False
    road = (m.get("road") or "").upper()
    if road and road == v:
        return True
    return v.lower() in (m.get("text") or "").lower() and not (v[:1] in "ABLSK" and v[1:].isdigit())


def watch_skip_until(db):
    row = db.execute("SELECT v FROM meta WHERE k='watch_skip_until'").fetchone()
    return int(row[0]) if row else 0


def check_rules(new_peaks, rules, my_locator=""):
    """Rückgabe Liste (rule, peak, detail) für jede passende Regel."""
    out = []
    for rule in rules or []:
        if not rule.get("enabled", True):
            continue
        typ, val = rule.get("type"), str(rule.get("value", "")).strip()
        for p in new_peaks:
            detail = None
            if typ == "freq":
                try:
                    f = float(val.replace(",", ".")) * 1e6
                    tol = float(rule.get("tolerance_khz", 5)) * 1e3
                    if abs(float(p["rf_hz"]) - f) <= tol:
                        detail = f"{float(p['rf_hz'])/1e6:.4f} MHz, {p['band']}"
                except ValueError:
                    pass
            elif typ == "call":
                pat = val.upper()
                for c in p["calls"]:
                    if fnmatch.fnmatch(c["call"], pat) or fnmatch.fnmatch(c["call"].split("-")[0].split("/")[0], pat):
                        detail = f"{c['call']} ({c['mode']}, {p['band']})"
                        break
            elif typ == "dx":
                try:
                    km = float(val)
                    for c in p["calls"]:
                        if c.get("grid"):
                            d, b = distance_bearing(c["grid"], my_locator)
                            if d and d >= km:
                                detail = f"{c['call']} {c['grid']}, {d} km ({c['mode']}, {p['band']})"
                                break
                except ValueError:
                    pass
            elif typ == "traffic":                           # TMC-Verkehrsmeldung zu Straße oder Ort
                for d in (p.get("decode") or {}).get("decoders", []):
                    for m in ((d.get("rds") or {}).get("tmc") or {}).get("messages", []):
                        if traffic_match(val, m):
                            detail = m.get("text") or f"Ereignis {m.get('event')}"
                            break
                    if detail:
                        break
            elif typ == "event":
                dec = p.get("decode") or {}
                types = {str(d.get("type", "")).upper() for d in dec.get("decoders", [])}
                seg = str(p.get("segment_id") or "")
                if val == "sstv" and "SSTV" in types:
                    detail = "SSTV-Bild"
                elif val == "iss_sstv" and "SSTV" in types and seg.startswith("iss"):
                    detail = "SSTV-Bild von der ISS"
                elif val == "aprs_iss" and "APRS" in types and seg.startswith("iss"):
                    detail = "APRS über die ISS: " + ", ".join(c["call"] for c in p["calls"][:5])
                elif val == "satellite" and p["band"] == "Satelliten":
                    detail = f"{seg} {float(p['rf_hz'])/1e6:.3f} MHz"
            if detail:
                out.append((rule, p, detail))
    return out
