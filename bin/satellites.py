#!/usr/bin/env python3
"""
Satelliten für den RTL-SDR-Stick: Katalog, Bahndaten, Überflüge, Doppler und der Aufnahmeplan.

Katalog
  Ein Satellit (NORAD-Nummer) kann mehrere Kanäle haben (ISS: 145,800 / 145,825 APRS /
  437,550 SSTV / 437,800 Relais). Gewählt werden Kanäle, aufgenommen wird pro Überflug EINE
  Abstimmung des Sticks. Alle gewählten Kanäle, die zusammen in 600 kHz passen, laufen dabei
  gleichzeitig mit (437,550 und 437,800 zusammen, 145,800 und 145,825 zusammen).

Bahndaten
  Eine Abfrage für alle (CelesTrak-Gruppe „amateur“), fehlende einzeln, ersatzweise AMSAT.
  Höchstens alle 12 h; nach einem Fehler frühestens nach 30 min wieder. Die Datei wird
  atomar geschrieben, Webapp und Scheduler holen nie gleichzeitig (Dateisperre).

Aufnahmeplan
  Überschneiden sich Überflüge, entscheidet die Priorität (ISS-SSTV vor allem anderen),
  bei Gleichstand der höhere Überflug. Der Unterlegene wird gekürzt, wenn vom Überflug noch
  mindestens 2 min übrig bleiben, sonst übersprungen – mit Begründung in der Liste.

Bahnrechnung mit sgp4 (Paket python3-sgp4). Beobachterort aus dem Locator.
"""
import fcntl
import json
import math
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TLE_FILE = BASE / "data" / "tle.json"
TLE_LOCK = BASE / "data" / "tle.lock"
RESULTS_FILE = BASE / "data" / "sat_results.json"
C = 299792458.0

TLE_MAX_AGE_H = 12.0        # danach neu holen
TLE_RETRY_S = 1800          # nach einem Fehlschlag frühestens wieder
TLE_WARN_DAYS = 10          # ältere Bahndaten werden als ungenau gemeldet
GROUP_SPAN_HZ = 600000      # so weit dürfen gleichzeitig aufgenommene Kanäle auseinander liegen
PRE_ROLL_S = 15             # Aufnahme beginnt vor dem Aufgang
MIN_PART_S = 120            # kürzere Reste eines Überflugs lohnen nicht
MAX_CHANNELS = 3            # Kanäle je Aufnahme – mehr schafft ein Raspberry Pi nicht sicher in Echtzeit

USER_AGENT = "HamScanPi/1.0 (+https://github.com/do1gmt/hamscanpi; amateur radio satellite tracker)"

# Objekte (NORAD -> Name). Nur Satelliten mit Downlink im Bereich des RTL-SDR.
OBJECTS = {
    25544: "ISS",
    7530: "AO-7",
    44881: "CAS-6 (TO-108)",
    39444: "AO-73 (FUNcube-1)",
    24278: "FO-29",
    44909: "RS-44",
    60209: "MO-122 (MESAT1)",
    27607: "SO-50",
    43678: "PO-101 (Diwata-2)",
    43017: "AO-91 (Fox-1B)",
    61781: "AO-123 (ASRTU-1)",
    40908: "CAS-3H (LilacSat-2)",
    # Bildsatelliten (SSTV), Stand AMSAT-Liste 2026 und SatNOGS. Nanozond-1 (RS49S) ist am 20.04.2026 verglüht.
    57172: "UmKA-1 (RS40S)",
    57189: "VIZARD-meteo (RS38S)",
    57203: "UTMN-2 (RS27S)",
    67284: "Lobachevsky (RS83S)",
    67291: "QMR-KWT-2 (RS95S)",
    59112: "SONATE-2",
    -1: "SakhaCube (RS18S)",
    -2: "HC1PX (Galapagos)",
    # Prüfsatelliten: senden ununterbrochen und kräftig – zeigen, ob die Satellitenaufnahme
    # von der Antenne bis zur Auswertung funktioniert
    57166: "Meteor-M N2-3",
    59051: "Meteor-M N2-4",
}
# Namen im Bahndatenkatalog: für Satelliten ohne sichere NORAD-Nummer (negative Schlüssel) und als
# Rückfall, falls eine Nummer nicht (mehr) passt
NAMES = {
    57172: ["UMKA-1", "UMKA"],
    57189: ["VIZARD-METEO", "VIZARD"],
    57203: ["UTMN-2", "UTMN2"],
    67284: ["LOBACHEVSKY"],
    67291: ["QMR-KWT-2", "QMR-KWT 2"],
    59112: ["SONATE-2", "SONATE 2"],
    -1: ["SAKHACUBE-CHOLBON", "SAKHACUBE"],
    -2: ["GALAPAGOS-UTE-SWSU", "HC1PX"],
}

# Kanäle. prio: wer bei Überschneidungen und bei der ISS-Abstimmung gewinnt.
# kind: fm (FM-Kanal, Sprachdurchgänge, ggf. SSTV/APRS), linear (SSB-Transponder) oder
# probe (Prüfsatellit: nur Pegel und Wasserfall, keine Dekodierung)
SATELLITES = [
    {"id": "iss_sstv70", "norad": 25544, "center_hz": 437550000, "kind": "fm", "sstv": True, "prio": 10,
     "label": "ISS 437.550 FM (SSTV-Aktionen)",
     "note": "ARISS-SSTV seit 2026 meist hier, Robot 36"},
    {"id": "iss", "norad": 25544, "center_hz": 145800000, "kind": "fm", "sstv": True, "prio": 8,
     "label": "ISS 145.800 FM (Sprache, SSTV)"},
    {"id": "iss_aprs", "norad": 25544, "center_hz": 145825000, "kind": "fm", "aprs": True, "prio": 4,
     "label": "ISS 145.825 APRS-Digipeater"},
    {"id": "iss_rpt", "norad": 25544, "center_hz": 437800000, "kind": "fm", "prio": 3,
     "label": "ISS-Relais 437.800 FM"},
    {"id": "ao7", "norad": 7530, "center_hz": 145950000, "kind": "linear", "prio": 5,
     "label": "AO-7 Mode B 145.925–145.975 USB", "note": "nur bei Sonne aktiv", "beacons": [145977500]},
    {"id": "to108", "norad": 44881, "center_hz": 145925000, "kind": "linear", "prio": 5,
     "label": "CAS-6/TO-108 145.915–145.935 USB, Bake 145.910", "beacons": [145910000]},
    {"id": "ao73", "norad": 39444, "center_hz": 145958000, "kind": "linear", "prio": 5,
     "label": "AO-73 145.950–145.970 USB, BPSK 145.935", "beacons": [145935000]},
    {"id": "fo29", "norad": 24278, "center_hz": 435850000, "kind": "linear", "prio": 5,
     "label": "FO-29 435.800–435.900 USB (Mitte)", "note": "Transponder nach Fahrplan", "beacons": [435795000]},
    {"id": "rs44", "norad": 44909, "center_hz": 435640000, "kind": "linear", "prio": 5,
     "label": "RS-44 435.610–435.670 USB", "beacons": [435605000]},
    {"id": "mo122", "norad": 60209, "center_hz": 435825000, "kind": "linear", "prio": 5,
     "label": "MO-122 435.810–435.840 USB, BPSK 435.800", "beacons": [435800000]},
    {"id": "so50", "norad": 27607, "center_hz": 436795000, "kind": "fm", "prio": 5,
     "label": "SO-50 436.795 FM", "note": "nur aktiv, wenn jemand den 10-min-Zeitschalter auslöst"},
    {"id": "po101", "norad": 43678, "center_hz": 145900000, "kind": "fm", "prio": 4,
     "label": "PO-101 145.900 FM", "note": "nach Fahrplan aktiv, schwächelt"},
    {"id": "ao91", "norad": 43017, "center_hz": 145960000, "kind": "fm", "prio": 4,
     "label": "AO-91 145.960 FM", "note": "Akku schwach – nur bei Sonne"},
    {"id": "ao123", "norad": 61781, "center_hz": 435400000, "kind": "fm", "prio": 5,
     "label": "AO-123 435.400 FM"},
    {"id": "cas3h", "norad": 40908, "center_hz": 437200000, "kind": "fm", "prio": 3,
     "label": "CAS-3H 437.200 FM/Bake", "note": "FM-Relais ohne festen Fahrplan, sonst Telemetrie"},
    # SSTV-Bildsatelliten: senden nur gelegentlich (Aktionen, meist ohne Fahrplan). Niedrigste Priorität:
    # aufgenommen wird nur, wenn der Stick frei ist – ein laufender Scan wird dafür nicht abgebrochen.
    {"id": "umka1", "norad": 57172, "center_hz": 437625000, "kind": "fm", "sstv": True, "prio": 2, "group": "sstv",
     "label": "SSTV · UmKA-1 437.625", "note": "Robot 36, häufigster SSTV-Cubesat"},
    {"id": "vizard", "norad": 57189, "center_hz": 437825000, "kind": "fm", "sstv": True, "prio": 2, "group": "sstv",
     "label": "SSTV · VIZARD-meteo 437.825"},
    {"id": "utmn2", "norad": 57203, "center_hz": 436125000, "kind": "fm", "sstv": True, "prio": 2, "group": "sstv",
     "label": "SSTV · UTMN-2 436.125"},
    {"id": "lobachevsky", "norad": 67284, "center_hz": 436320000, "kind": "fm", "sstv": True, "prio": 2, "group": "sstv",
     "label": "SSTV · Lobachevsky 436.320", "note": "auch SSDV (wird nicht dekodiert)"},
    {"id": "qmrkwt2", "norad": 67291, "center_hz": 436950000, "kind": "fm", "sstv": True, "prio": 2, "group": "sstv",
     "label": "SSTV · QMR-KWT-2 436.950"},
    {"id": "sakhacube", "norad": -1, "center_hz": 437350000, "kind": "fm", "sstv": True, "prio": 2, "group": "sstv",
     "label": "SSTV · SakhaCube 437.350"},
    {"id": "hc1px", "norad": -2, "center_hz": 437050000, "kind": "fm", "sstv": True, "prio": 2, "group": "sstv",
     "label": "SSTV · HC1PX 437.050"},
    {"id": "sonate2", "norad": 59112, "center_hz": 145880000, "kind": "fm", "sstv": True, "prio": 2, "group": "sstv",
     "label": "SSTV · SONATE-2 145.880", "note": "Martin 1, nach Fahrplan der Uni Würzburg; Bahn sinkt"},
    # alt_hz: LRPT wechselt je nach Betrieb zwischen 137,100 und 137,900 MHz – gemessen werden beide
    {"id": "meteor23", "norad": 57166, "center_hz": 137900000, "alt_hz": [137100000], "kind": "probe", "prio": 3,
     "label": "Prüfsatellit Meteor-M N2-3 137.900/137.100",
     "note": "sendet ununterbrochen (LRPT, ~120 kHz breit) – zeigt, ob die Satellitenaufnahme funktioniert"},
    {"id": "meteor24", "norad": 59051, "center_hz": 137900000, "alt_hz": [137100000], "kind": "probe", "prio": 3,
     "label": "Prüfsatellit Meteor-M N2-4 137.900/137.100",
     "note": "sendet ununterbrochen (LRPT) – Gegenprobe zu N2-3"}
]
OPPORTUNISTIC_PRIO = 2      # bis zu dieser Priorität: nur bei freiem Stick, kein Scanabbruch
for _s in SATELLITES:
    _s["name"] = OBJECTS[_s["norad"]]
BY_ID = {s["id"]: s for s in SATELLITES}


def mhz(hz):
    return f"{hz / 1e6:.3f}".replace(".", ",")


def segments_for_bandplan():
    """Die Chips unter Scan → Satelliten (scanner.json folgt dem Katalog)."""
    return [{"id": s["id"], "label": s["label"], "mode": "sat", "frequencies_hz": [s["center_hz"]],
             "legal": "amateur", **({"note": s["note"]} if s.get("note") else {})} for s in SATELLITES]


# ---------------------------------------------------------------- Ort

def locator_to_latlon(loc):
    """Maidenhead-Locator (4/6/8 Stellen) -> Breite, Länge (Feldmitte)."""
    loc = (loc or "").strip().upper()
    if len(loc) < 4 or len(loc) % 2:
        raise ValueError("Locator fehlt oder ungültig")
    lon = (ord(loc[0]) - 65) * 20 - 180
    lat = (ord(loc[1]) - 65) * 10 - 90
    lon += int(loc[2]) * 2
    lat += int(loc[3])
    w_lon, w_lat = 2.0, 1.0
    if len(loc) >= 6:
        lon += (ord(loc[4]) - 65) * (5 / 60)
        lat += (ord(loc[5]) - 65) * (2.5 / 60)
        w_lon, w_lat = 5 / 60, 2.5 / 60
    if len(loc) >= 8:
        lon += int(loc[6]) * (0.5 / 60)
        lat += int(loc[7]) * (0.25 / 60)
        w_lon, w_lat = 0.5 / 60, 0.25 / 60
    return lat + w_lat / 2, lon + w_lon / 2


def _observer_ecef(lat, lon, alt_m=0.0):
    a, f = 6378.137, 1 / 298.257223563
    e2 = f * (2 - f)
    la, lo = math.radians(lat), math.radians(lon)
    n = a / math.sqrt(1 - e2 * math.sin(la) ** 2)
    h = alt_m / 1000.0
    return ((n + h) * math.cos(la) * math.cos(lo), (n + h) * math.cos(la) * math.sin(lo),
            (n * (1 - e2) + h) * math.sin(la))


# ---------------------------------------------------------------- Bahndaten

def _checksum_ok(line):
    if len(line) < 69:
        return False
    s = sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in line[:68])
    return line[68].isdigit() and s % 10 == int(line[68])


def parse_tle_text(text):
    """Beliebiger TLE-Text (2- oder 3-zeilig) -> {norad: [name, l1, l2]}."""
    out = {}
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    for i in range(len(lines) - 1):
        l1, l2 = lines[i], lines[i + 1]
        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            continue
        if not (_checksum_ok(l1) and _checksum_ok(l2)):
            continue
        try:
            norad = int(l1[2:7])
        except ValueError:
            continue
        name = lines[i - 1].strip() if i > 0 and not lines[i - 1].startswith(("1 ", "2 ")) else ""
        out[norad] = [name, l1, l2]
    return out


def tle_epoch(l1):
    """Epoche einer TLE als Unix-Zeit."""
    yy = int(l1[18:20])
    year = 2000 + yy if yy < 57 else 1900 + yy
    day = float(l1[20:32])
    return datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() + (day - 1) * 86400


def _http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("ascii", "replace")


def _read_tle_file():
    try:
        d = json.loads(TLE_FILE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    tle = d.get("tle") if isinstance(d.get("tle"), dict) else {}
    # nur das neue Format (Schlüssel = NORAD-Nummer) übernehmen
    tle = {k: v for k, v in tle.items() if k.lstrip("-").isdigit() and isinstance(v, list) and len(v) == 3}
    return {"fetched": float(d.get("fetched", 0) or 0), "tried": float(d.get("tried", 0) or 0),
            "tle": tle, "errors": d.get("errors") or {}, "source": d.get("source", ""),
            "not_found": d.get("not_found") or []}


def _write_tle_file(d):
    TLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TLE_FILE.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, TLE_FILE)                         # nie eine halbe Datei für den anderen Prozess


def _by_name(entries, patterns):
    """Eintrag, dessen Katalogname zu einem der Namen passt (Großschreibung egal)."""
    pats = [p.upper() for p in patterns]
    for n, v in entries.items():
        nm = (v[0] or "").upper().replace("_", "-")
        if any(nm == p or nm.startswith(p + " ") or nm.startswith(p + "(") or p in nm for p in pats):
            return v
    return None


def _fetch_all(need, have):
    """need: set Objekt-Schlüssel (NORAD bzw. negativ = nur über den Namen).
    Rückgabe (gefunden {schlüssel: [...]}, Fehler {quelle: text}, nicht_gefunden)."""
    got, errors, not_found = {}, {}, []
    group = {}
    try:
        group = parse_tle_text(_http_get("https://celestrak.org/NORAD/elements/gp.php?GROUP=amateur&FORMAT=tle"))
        got.update({k: v for k, v in group.items() if k in need})
    except Exception as e:
        errors["celestrak_gruppe"] = f"{type(e).__name__}: {e}"[:200]

    def by_names(pool):
        for k in sorted(need - set(got)):
            if k in NAMES:
                v = _by_name(pool, NAMES[k])
                if v:
                    got[k] = v
    by_names(group)
    if "celestrak_gruppe" not in errors:
        import urllib.error
        # einzeln nach Nummer: CelesTrak antwortet bei unbekannter Nummer mit 404 (früher mit Text) –
        # das heißt „nicht im Katalog“, kein Fehler, und die übrigen werden trotzdem abgefragt
        for n in [k for k in sorted(need - set(got)) if k > 0][:12]:
            try:
                txt = _http_get(f"https://celestrak.org/NORAD/elements/gp.php?CATNR={n}&FORMAT=TLE")
                p = parse_tle_text(txt)
                if n in p:
                    got[n] = p[n]
                else:
                    not_found.append(n)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    not_found.append(n)
                else:
                    errors["celestrak_einzeln"] = f"HTTP {e.code}"
                    break
            except Exception as e:
                errors["celestrak_einzeln"] = f"{type(e).__name__}: {e}"[:200]
                break
            time.sleep(1)
        # nach Namen (alle bekannten Schreibweisen)
        for k in [k for k in sorted(need - set(got)) if k in NAMES][:12]:
            hit = None
            for nm in NAMES[k]:
                try:
                    txt = _http_get("https://celestrak.org/NORAD/elements/gp.php?NAME="
                                    + urllib.request.quote(nm) + "&FORMAT=TLE")
                    hit = _by_name(parse_tle_text(txt), NAMES[k])
                except urllib.error.HTTPError as e:
                    if e.code != 404:
                        errors["celestrak_name"] = f"HTTP {e.code}"
                except Exception as e:
                    errors["celestrak_name"] = f"{type(e).__name__}: {e}"[:200]
                time.sleep(1)
                if hit:
                    break
            if hit:
                got[k] = hit
                if k in not_found:
                    not_found.remove(k)
            elif k not in not_found:
                not_found.append(k)
    if need - set(got):
        try:
            p = parse_tle_text(_http_get("https://www.amsat.org/tle/current/nasabare.txt"))
            got.update({k: v for k, v in p.items() if k in need and k not in got})
            by_names(p)
        except Exception as e:
            errors["amsat"] = f"{type(e).__name__}: {e}"[:200]
    return got, errors, [n for n in not_found if n not in got]


def load_tles(fetch=True, force=False):
    """Bahndaten {norad(int): [name, l1, l2]} und Zustand. Holt nur wenn nötig, nie parallel."""
    need = {s["norad"] for s in SATELLITES}
    d = _read_tle_file()
    now = time.time()

    def due(dd):
        have = {int(k) for k in dd["tle"]}
        stale = now - dd["fetched"] > TLE_MAX_AGE_H * 3600
        missing = need - have - set(dd["not_found"])
        return (force or stale or missing) and (force or now - dd["tried"] > TLE_RETRY_S)

    if fetch and due(d):
        TLE_LOCK.parent.mkdir(parents=True, exist_ok=True)
        with open(TLE_LOCK, "w") as lk:
            try:
                fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                d = _read_tle_file()                    # vielleicht hat der andere Prozess gerade geholt
                if due(d):
                    d["tried"] = now
                    _write_tle_file(d)                  # Versuch sofort vermerken (kein Dauerfeuer)
                    got, errors, not_found = _fetch_all(need, d["tle"])
                    for n, v in got.items():
                        d["tle"][str(n)] = v
                    d["errors"] = errors
                    d["not_found"] = not_found
                    if got:
                        d["fetched"] = now
                        d["source"] = "CelesTrak" if "celestrak_gruppe" not in errors else "AMSAT/CelesTrak einzeln"
                    _write_tle_file(d)
            except BlockingIOError:
                pass                                    # der andere Prozess holt gerade – alten Stand nehmen
    tles = {int(k): v for k, v in d["tle"].items()}
    return tles, d


class Tracker:
    """Blickrichtung, Entfernung und Radialgeschwindigkeit eines Satelliten vom Beobachter."""

    def __init__(self, tle, lat, lon, alt_m=0.0):
        from sgp4.api import Satrec
        if len(tle) == 3:
            tle = tle[1:]
        self.sat = Satrec.twoline2rv(tle[0], tle[1])
        self.lat, self.lon = lat, lon
        self.obs = _observer_ecef(lat, lon, alt_m)

    def look(self, t):
        """t: Unix-Zeit. Rückgabe (Elevation°, Azimut°, Entfernung km, Radialgeschw. km/s)."""
        from sgp4.api import jday
        d = datetime.fromtimestamp(t, timezone.utc)
        jd, fr = jday(d.year, d.month, d.day, d.hour, d.minute, d.second + d.microsecond / 1e6)
        err, r, v = self.sat.sgp4(jd, fr)
        if err:
            raise RuntimeError(f"SGP4-Fehler {err}")
        T = (jd + fr - 2451545.0) / 36525.0
        gmst = math.radians((280.46061837 + 360.98564736629 * (jd + fr - 2451545.0)
                             + 0.000387933 * T * T - T ** 3 / 38710000.0) % 360.0)
        cg, sg = math.cos(gmst), math.sin(gmst)
        x, y, z = cg * r[0] + sg * r[1], -sg * r[0] + cg * r[1], r[2]
        we = 7.292115e-5
        vx = cg * v[0] + sg * v[1] + we * y
        vy = -sg * v[0] + cg * v[1] - we * x
        vz = v[2]
        dx, dy, dz = x - self.obs[0], y - self.obs[1], z - self.obs[2]
        rng = math.sqrt(dx * dx + dy * dy + dz * dz)
        rr = (dx * vx + dy * vy + dz * vz) / rng
        la, lo = math.radians(self.lat), math.radians(self.lon)
        e = -math.sin(lo) * dx + math.cos(lo) * dy
        n = -math.sin(la) * math.cos(lo) * dx - math.sin(la) * math.sin(lo) * dy + math.cos(la) * dz
        u = math.cos(la) * math.cos(lo) * dx + math.cos(la) * math.sin(lo) * dy + math.sin(la) * dz
        el = math.degrees(math.asin(u / rng))
        az = (math.degrees(math.atan2(e, n)) + 360.0) % 360.0
        return el, az, rng, rr

    def doppler_hz(self, f_hz, t):
        return -f_hz * self.look(t)[3] * 1000.0 / C


def passes(tracker, t_start, hours=24.0, min_el=10.0, step=30.0):
    """Überflüge mit Höchstelevation >= min_el. Rückgabe Liste dict(aos, los, max_el, max_t, aos_az, los_az)."""
    out = []
    t, t_end = t_start, t_start + hours * 3600
    up = tracker.look(t)[0] > 0
    aos = t if up else None
    while t < t_end:
        t2 = t + step
        el = tracker.look(t2)[0]
        if not up and el > 0:
            lo, hi = t, t2
            for _ in range(12):
                m = (lo + hi) / 2
                lo, hi = (lo, m) if tracker.look(m)[0] > 0 else (m, hi)
            aos, up = hi, True
        elif up and el <= 0:
            lo, hi = t, t2
            for _ in range(12):
                m = (lo + hi) / 2
                lo, hi = (m, hi) if tracker.look(m)[0] > 0 else (lo, m)
            los = lo
            best_t, best_el = aos, -90.0
            tt = aos
            while tt <= los:
                e = tracker.look(tt)[0]
                if e > best_el:
                    best_el, best_t = e, tt
                tt += 10.0
            if best_el >= min_el:
                out.append({"aos": aos, "los": los, "max_el": round(best_el, 1), "max_t": best_t,
                            "aos_az": round(tracker.look(aos)[1]), "los_az": round(tracker.look(los)[1]),
                            # kürzeste Entfernung = Entfernung am höchsten Punkt (Schrägentfernung)
                            "min_km": round(tracker.look(best_t)[2])})
            up = False
        t = t2
    return out


# ---------------------------------------------------------------- Kanalwahl und Plan

def eff_prio(sid, prio=None):
    """Grundpriorität eines Kanals: Einstellung des Benutzers (1–10 oder "pin") vor dem Katalog."""
    v = (prio or {}).get(sid)
    if v == "pin":
        return 20
    try:
        v = int(v)
        if 0 <= v <= 10:
            return v
    except (TypeError, ValueError):
        pass
    return BY_ID[sid]["prio"]


def channel_group(ids, prio=None):
    """Aus den gewählten Kanälen eines Satelliten die Gruppe, die in eine Abstimmung passt
    und die höchste Summe der Prioritäten hat. Rückgabe (gewählte ids, nicht möglich ids)."""
    chans = sorted((BY_ID[i] for i in ids), key=lambda s: s["center_hz"])
    best, best_score = [], (-1, -1)
    for a in chans:
        grp = [s for s in chans if 0 <= s["center_hz"] - a["center_hz"] <= GROUP_SPAN_HZ]
        score = (sum(eff_prio(s["id"], prio) for s in grp), max(eff_prio(s["id"], prio) for s in grp))
        if score > best_score:
            best, best_score = grp, score
    best_ids = [s["id"] for s in sorted(best, key=lambda s: -eff_prio(s["id"], prio))]
    return best_ids, [s["id"] for s in chans if s["id"] not in best_ids]


def tuning_for(channels):
    """Mittenfrequenz des Sticks für eine Kanalgruppe: alle Kanäle im Fenster, keiner nahe
    der Mitte (Gleichanteil des RTL) – Doppler bis ±11 kHz eingerechnet."""
    fs = [BY_ID[c]["center_hz"] for c in channels]
    lo, hi = min(fs), max(fs)
    mid = (lo + hi) / 2
    for t in (mid, lo - 200000, hi + 200000, mid + 120000, mid - 120000, lo - 120000, hi + 120000,
              mid + 60000, mid - 60000):
        if all(60000 <= abs(f - t) <= 400000 for f in fs):   # Ränder des RTL-Fensters meiden
            return int(t)
    raise ValueError("Kanäle passen nicht in eine Abstimmung")


def fits_together(channels):
    try:
        tuning_for(channels)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- Statistik je Satellit

STATS_FILE = BASE / "data" / "sat_stats.json"
STATS_DAYS = 14             # Aktivität: nur die letzten zwei Wochen zählen


def read_stats():
    try:
        return json.loads(STATS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def record_stats(norad, t, hits, seconds):
    """Nach jeder Aufnahme: wann, wie lange, wie viele Treffer (für Wartezeit und Aktivität)."""
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATS_FILE.with_suffix(".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        d = read_stats()
        e = d.setdefault(str(norad), {"passes": []})
        e["passes"].append([round(t), int(hits), int(seconds)])
        cutoff = time.time() - 60 * 86400
        e["passes"] = [x for x in e["passes"] if x[0] > cutoff][-200:]
        if hits:
            e["last_hit"] = round(t)
        tmp = STATS_FILE.with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        os.replace(tmp, STATS_FILE)


BEAM_DEFAULT_BW = 70.0        # Öffnungswinkel, wenn bei der Richtantenne keiner eingetragen ist


def antenna_beam():
    """Richtantenne des Haupt-Sticks (System → Geräte): dict(az, bw, fmin, fmax, prefer) oder None
    (keine Richtung eingetragen = Rundstrahler). prefer aus den Einstellungen (sat_beam_prefer)."""
    try:
        import sticks as STK
        main = next((r for r in STK.load()["sticks"].values() if r.get("role") == "main"), None)
    except Exception:
        return None
    if not main or main.get("ant_az") is None:
        return None
    prefer = True
    try:
        prefer = bool(json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
                      .get("sat_beam_prefer", True))
    except Exception:
        pass
    return {"az": float(main["ant_az"]) % 360, "bw": float(main.get("ant_bw") or BEAM_DEFAULT_BW),
            "el": float(main.get("ant_el") or 0.0),
            "fmin": main.get("ant_fmin"), "fmax": main.get("ant_fmax"), "prefer": prefer,
            "name": main.get("antenna") or ""}


def beam_window(tr, p, beam, center_hz, step=10.0):
    """Welcher Teil des Überflugs liegt in der Keule der Richtantenne? Alle step Sekunden Azimut
    prüfen (Abweichung höchstens halber Öffnungswinkel, in der Höhe ebenso um die Erhebung der Antenne –
    eine waagrechte Yagi hört über ~35° kaum noch). Rückgabe dict(start, end, max_el, secs, band)."""
    f = center_hz / 1e6
    band_ok = not ((beam.get("fmin") and f < beam["fmin"]) or (beam.get("fmax") and f > beam["fmax"]))
    first = last = None
    best = 0.0
    n = 0
    t = p["aos"]
    while t <= p["los"]:
        el, az = tr.look(t)[:2]
        if (el >= 0 and abs((az - beam["az"] + 180) % 360 - 180) <= beam["bw"] / 2
                and abs(el - beam.get("el", 0.0)) <= beam["bw"] / 2):
            first = t if first is None else first
            last = t
            best = max(best, el)
            n += 1
        t += step
    return {"start": first, "end": last, "max_el": round(best, 1), "secs": int(n * step), "band": band_ok}


def score_pass(p, stats, now):
    """Punkte eines Überflugs und ihre Herleitung.
      Grundpriorität × 10  – entscheidet weiterhin über große Unterschiede (ISS-SSTV bleibt oben)
      + Höhe               – 0 Punkte bei 10°, 15 bei 90°
      + Wartezeit          – 1 Punkt je 4 Stunden ohne Aufnahme, höchstens 15
      − Ruhe               – wenn in 14 Tagen mehrere Aufnahmen ohne jedes Signal: 1,5 je Aufnahme,
                             höchstens 15; ein einziger Treffer hebt das sofort auf"""
    base = p["prio"] * 10
    quality = round(max(0.0, min(15.0, (p["max_el"] - 10) / 80 * 15)), 1)
    st = stats.get(str(p["norad"])) or {}
    recent = [x for x in st.get("passes", []) if x[0] > now - STATS_DAYS * 86400 and x[2] >= 60]
    last_rec = max((x[0] for x in st.get("passes", []) if x[2] >= 60), default=None)
    if last_rec:
        wait_days = (p["aos"] - last_rec) / 86400
    else:
        wait_days = 2.0                                   # nie aufgenommen: wie 2 Tage Wartezeit
    waiting = round(max(0.0, min(15.0, 6 * wait_days)), 1)
    hits_recent = sum(x[1] for x in recent)
    silent = len(recent) if hits_recent == 0 else 0
    quiet = round(min(15.0, 1.5 * silent), 1) if silent >= 3 else 0.0
    # Richtantenne (System → Geräte): Überflüge durch die Keule bevorzugen, andere zurückstellen
    beam_pts = 0.0
    b = p.get("beam")
    if b and b.get("prefer"):
        if not b["band"]:
            beam_pts = -20.0
        elif b["secs"] >= 60:
            beam_pts = round(min(15.0, 8 + b["max_el"] / 6), 1)
        else:
            beam_pts = -20.0
    total = round(base + quality + waiting - quiet + beam_pts, 1)
    why = []
    if b:
        if not b["band"]:
            why.append("Antenne nicht für diese Frequenz")
        elif b["secs"] >= 60:
            why.append(f"im Strahl der Antenne {b['secs'] // 60} min, bis {b['max_el']:.0f}°")
        else:
            why.append("nicht im Strahl der Antenne")
    if waiting >= 6:
        why.append("noch nie aufgenommen" if not last_rec else
                   (f"{wait_days:.0f} Tage nicht aufgenommen" if wait_days >= 1.5 else f"{wait_days * 24:.0f} h nicht aufgenommen"))
    if quiet:
        why.append(f"herabgestuft: {silent} Aufnahmen ohne Signal")
    if hits_recent:
        why.append(f"aktiv: {hits_recent} Treffer in 14 Tagen")
    parts = {"Grund": base, "Höhe": quality, "Wartezeit": waiting, "Ruhe": -quiet}
    if beam_pts:
        parts["Antenne"] = beam_pts
    return total, parts, why


def make_plan(raw_passes, now=None, stats=None):
    """Aufnahmen planen. Ein Überflug ist ein Eintrag je Satellit; eine Aufnahme kann mehrere
    Überflüge enthalten, wenn die Satelliten gleichzeitig oben sind und ihre Frequenzen in ein
    Empfangsfenster passen („mitgeschnitten“). Höhere Punktzahl zuerst.
    Setzt je Überflug rec_start/rec_end/status/reason/score/rec (Schlüssel der Aufnahme).
    Rückgabe (Überflüge nach Zeit, Aufnahmen nach Zeit)."""
    now = now or time.time()
    stats = stats if stats is not None else read_stats()
    for p in raw_passes:
        p["score"], p["score_parts"], p["why"] = score_pass(p, stats, now)
    recs = []                                             # dict(start, end, members, channels, key)
    for p in sorted(raw_passes, key=lambda p: (-p["score"], p["aos"])):
        start, end = max(p["aos"] - PRE_ROLL_S, 0), p["los"]
        overlapping = [r for r in recs if r["start"] < end and r["end"] > start]
        # 1. mitschneiden: genau eine überlappende Aufnahme und die Frequenzen passen zusammen
        if (len(overlapping) == 1 and len(overlapping[0]["channels"]) + len(p["channels"]) <= MAX_CHANNELS
                and fits_together(overlapping[0]["channels"] + p["channels"])):
            r = overlapping[0]
            others = [x for x in recs if x is not r]
            ns, ne = min(r["start"], start), max(r["end"], end)
            for x in others:                              # Erweiterung nur in freie Zeit
                if x["end"] <= r["start"]:
                    ns = max(ns, x["end"] + 5)
                if x["start"] >= r["end"]:
                    ne = min(ne, x["start"] - 5)
            r["start"], r["end"] = ns, ne
            r["members"].append(p)
            r["channels"] += [c for c in p["channels"] if c not in r["channels"]]
            p.update(rec=r["key"], status="joined",
                     reason=f"gemeinsame Aufnahme mit {r['members'][0]['name']}")
            continue
        # 2. sonst: eigene Aufnahme in der freien Zeit (gekürzt oder übersprungen)
        pieces, blockers = [(start, end)], []
        for r in sorted(recs, key=lambda r: r["start"]):
            nxt = []
            for (a, b) in pieces:
                if r["end"] <= a or r["start"] >= b:
                    nxt.append((a, b))
                    continue
                blockers.append(r)
                if r["start"] > a:
                    nxt.append((a, r["start"] - 5))
                if r["end"] < b:
                    nxt.append((r["end"] + 5, b))
            pieces = nxt
        pieces = [x for x in pieces if x[1] - x[0] >= MIN_PART_S]
        names = ", ".join(dict.fromkeys(m["name"] for r in blockers for m in r["members"][:1]))
        if not pieces:
            host = blockers[0]["members"][0] if blockers else None
            why_not = ""
            if len(overlapping) == 1:
                full = len(overlapping[0]["channels"]) + len(p["channels"]) > MAX_CHANNELS
                why_not = (f"gemeinsame Aufnahme mit {names} ist schon voll ({MAX_CHANNELS} Kanäle); " if full else
                           f"gleichzeitig mit {names}, Frequenz passt nicht ins gemeinsame Empfangsfenster; ")
            p.update(rec=None, rec_start=None, rec_end=None, status="skipped",
                     reason=why_not
                     + f"{names} hat Vorrang" + (f" ({host['score']:.0f} gegen {p['score']:.0f} Punkte)" if host else ""))
            continue
        a, b = max(pieces, key=lambda x: x[1] - x[0])
        r = {"key": p["key"], "start": a, "end": b, "members": [p], "channels": list(p["channels"])}
        recs.append(r)
        p.update(rec=r["key"], status="partial" if blockers else "planned",
                 reason=f"gekürzt wegen {names}" if blockers else "")
    for r in recs:
        for m in r["members"]:
            m["rec_start"], m["rec_end"] = r["start"], r["end"]
        r["keys"] = [m["key"] for m in r["members"]]
        r["name"] = " + ".join(m["name"] for m in r["members"])
        r["prio"] = max(m["prio"] for m in r["members"])
        r["max_el"] = max(m["max_el"] for m in r["members"])
        r["members"] = [m["key"] for m in r["members"]]
    return sorted(raw_passes, key=lambda p: p["aos"]), sorted(recs, key=lambda r: r["start"])


def read_results():
    try:
        return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_result(key, **info):
    """Ergebnis eines Überflugs (läuft / fertig / Fehler) für die Liste im Dashboard."""
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = RESULTS_FILE.with_suffix(".lock")
    with open(lock, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        d = read_results()
        d.setdefault(key, {}).update(info, updated=time.time())
        cutoff = time.time() - 3 * 86400
        d = {k: v for k, v in d.items() if v.get("updated", 0) > cutoff}
        tmp = RESULTS_FILE.with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, RESULTS_FILE)


def upcoming(selected_ids, locator, hours=24.0, min_el=10.0, fetch=True, past_s=3 * 3600, prio=None):
    """Überflüge der gewählten Kanäle mit Aufnahmeplan. Ein Eintrag je Satellit und Überflug,
    dazu die geplanten Aufnahmen ('recordings'). prio: Prioritäten aus den Einstellungen.
    Probleme stehen als Text in 'problem' bzw. 'warnings' – nie eine Ausnahme nach außen."""
    try:
        lat, lon = locator_to_latlon(locator)
    except ValueError as e:
        return {"passes": [], "recordings": [], "problem": f"{e} – unter Einstellungen → Station eintragen"}
    try:
        import sgp4  # noqa: F401
    except ImportError:
        return {"passes": [], "recordings": [], "problem": "Bahnrechnung fehlt (Paket python3-sgp4) – Update einspielen"}
    tles, st = load_tles(fetch=fetch)
    now = time.time()
    by_norad = {}
    for sid in selected_ids:
        if sid in BY_ID and eff_prio(sid, prio) > 0:      # Priorität 0 = nie aufnehmen
            by_norad.setdefault(BY_ID[sid]["norad"], []).append(sid)
    raw, warnings, no_data = [], [], []
    beam = antenna_beam()
    for norad, ids in by_norad.items():
        name = OBJECTS[norad]
        if norad not in tles:
            no_data.append(name + (" (nicht mehr im Katalog – verglüht?)" if norad in st["not_found"] else ""))
            continue
        age_d = (now - tle_epoch(tles[norad][1])) / 86400
        if age_d > TLE_WARN_DAYS:
            warnings.append(f"Bahndaten {name} {age_d:.0f} Tage alt – Zeiten ungenau")
        chosen, dropped = channel_group(ids, prio)
        tr = Tracker(tles[norad], lat, lon)
        for p in passes(tr, now - past_s, hours + past_s / 3600, min_el):
            raw.append(dict(p, key=f"{norad}_{int(p['aos']) // 60}", norad=norad, name=name,
                            channels=chosen, dropped=dropped, prio=max(eff_prio(c, prio) for c in chosen),
                            base_prio=max(BY_ID[c]["prio"] for c in chosen),
                            labels=[BY_ID[c]["label"] for c in chosen], kind=BY_ID[chosen[0]]["kind"],
                            center_hz=BY_ID[chosen[0]]["center_hz"], id=chosen[0]))
            if beam:
                raw[-1]["beam"] = dict(beam_window(tr, p, beam, BY_ID[chosen[0]]["center_hz"]),
                                       prefer=beam["prefer"], az=beam["az"], bw=beam["bw"])
    plan, recs = make_plan(raw, now)
    problem = None
    if no_data:
        why = "; ".join(f"{k}: {v}" for k, v in (st.get("errors") or {}).items())
        problem = "keine Bahndaten für " + ", ".join(no_data) + (f" – {why}" if why else "")
    return {"passes": plan, "recordings": recs, "problem": problem, "warnings": warnings,
            "tle_age_h": round((now - st["fetched"]) / 3600, 1) if st["fetched"] else None,
            "tle_source": st.get("source", ""), "tle_errors": st.get("errors") or {},
            "lat": round(lat, 3), "lon": round(lon, 3)}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "fetch":
        t, st = load_tles(force=True)
        miss = [OBJECTS[o] for o in OBJECTS if o not in t]
        print(f"{len(t)} von {len(OBJECTS)} Satelliten mit Bahndaten" + (f" (Quelle: {st.get('source')})" if st.get('source') else ""))
        if miss:
            print("  ohne Bahndaten: " + ", ".join(miss))
        nf = [OBJECTS[o] for o in st.get("not_found") or [] if o in OBJECTS]
        if nf:
            print("  davon nicht im CelesTrak-Katalog (verglüht oder neue Nummer): " + ", ".join(nf))
        if st.get("errors"):
            print("  Abruffehler: " + "; ".join(f"{k}: {v}" for k, v in st["errors"].items()))
