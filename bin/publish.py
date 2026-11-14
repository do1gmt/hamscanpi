#!/usr/bin/env python3
"""SSTV-Bilder auf die eigene Webseite hochladen (WordPress mit dem Plugin „HamScanPi SSTV-Galerie“,
liegt bei unter share/wordpress/).

Einstellungen → Webseite: Adresse, WordPress-Benutzer, Anwendungspasswort (WordPress → Benutzer → Profil →
Anwendungspasswörter; jederzeit widerrufbar, das eigentliche Passwort braucht der Pi nicht), Entwurf oder sofort
veröffentlichen, Mindest-Vollständigkeit.

Jedes neue SSTV-Bild (Satellit, Kurzwelle, Live) kommt mit allen Angaben in eine Warteschlange
(data/publish_queue.json). Der Zeitplaner schickt sie jede Minute los; ohne Internet bleiben sie liegen und gehen
später raus (Wartezeit wächst bis 1 h, nach 14 Tagen aufgegeben). Live-Bilder wachsen beim Empfang – sie gehen
erst, wenn sie 90 s unverändert sind. Jedes Bild hat eine feste Kennung: ein wiederholter Versuch legt auf der
Seite kein zweites an.

Die Angaben kommen als fertig beschriftete Abschnitte (Bild, Satellit, Empfang, Station, Raspberry Pi) – neue
Angaben erscheinen auf der Seite ohne Plugin-Update. Nie übertragen: IP- und MAC-Adressen, WLAN, Zugangsdaten.

Von Hand nachreichen: Knopf „Auf Webseite“ in jeder SSTV-Trefferkarte (manual()). Geht auch, wenn das automatische
Hochladen aus ist oder das Bild unter der Mindest-Vollständigkeit lag; gleiche Kennung wie automatisch, also nie
doppelt auf der Seite. Die Angaben kommen aus der Datenbank, beim Satelliten mit Überflug aus den Bahndaten.
"""
import base64
import calendar
import hashlib
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
QUEUE = BASE / "data" / "publish_queue.json"
LOG = BASE / "data" / "publish_log.json"
ROUTE = "/wp-json/hsp-sstv/v1"
MAX_AGE_S = 14 * 86400
PER_TICK = 4
UA = "HamScanPi-publish"


# ------------------------------------------------------------------ Einstellungen
def cfg(settings=None):
    if settings is None:
        try:
            settings = json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    c = dict(settings.get("publish") or {})
    c.setdefault("enabled", False)
    c.setdefault("url", "")
    c.setdefault("user", "")
    c.setdefault("app_password", "")
    c.setdefault("status", "draft")             # draft | publish
    c.setdefault("min_complete", 60)            # Prozent der Bildzeilen
    return c


def public_cfg(settings=None):
    c = cfg(settings)
    c["has_password"] = bool(c.pop("app_password", ""))
    return c


def validate(c):
    url = str(c.get("url") or "").strip().rstrip("/")
    if url and not url.startswith(("https://", "http://")):
        url = "https://" + url
    if url.startswith("http://") and not any(h in url for h in ("localhost", "127.0.0.1", ".local", "192.168.", "10.")):
        raise ValueError("Bitte https:// – das Anwendungspasswort darf nicht unverschlüsselt durchs Internet")
    c["url"] = url
    c["user"] = str(c.get("user") or "").strip()[:60]
    c["status"] = "publish" if c.get("status") == "publish" else "draft"
    c["min_complete"] = max(0, min(100, int(c.get("min_complete") or 0)))
    c["enabled"] = bool(c.get("enabled"))
    return c


# ------------------------------------------------------------------ Warteschlange
def _read(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(p, d):
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def queue():
    return _read(QUEUE, [])


def read_log():
    return _read(LOG, [])


def _log(entry):
    _write(LOG, (read_log() + [entry])[-300:])


def key_for(png):
    """Feste Kennung je Bild – automatisch und von Hand gleich (die Seite legt es nur einmal an)."""
    return f"{socket.gethostname()}-{Path(png).name}"


def states():
    """{Kennung: Zustand} aus Warteschlange und Protokoll – für die Anzeige in den Trefferkarten."""
    out = {}
    for e in read_log():
        out[e["key"]] = {"state": e.get("state"), "link": e.get("link"), "edit": e.get("edit"), "t": e.get("t"),
                         "error": e.get("error")}
    for e in queue():
        out[e["key"]] = {"state": "wartet", "error": e.get("error"), "t": e.get("added")}
    return out


def enqueue(png, meta, key=None, settle_s=0, manual=False):
    """Bild zum Hochladen vormerken. key: gleiche Kennung = Eintrag auffrischen (wachsendes Live-Bild).
    manual: von Hand nachgereicht – auch bei ausgeschaltetem automatischem Hochladen, ohne Mindest-Vollständigkeit."""
    c = cfg()
    if not ((c["enabled"] or manual) and c["url"]):
        return None
    png = str(png)
    key = key or key_for(png)
    q = queue()
    now = time.time()
    e = next((x for x in q if x["key"] == key), None)
    if e is None:
        e = {"key": key, "uid": hashlib.sha1(key.encode()).hexdigest()[:20], "added": now, "tries": 0, "next": 0}
        q.append(e)
    e.update(png=png, meta=meta, ready=now + settle_s)
    if manual:
        e.update(manual=True, tries=0, next=0, error=None)
    _write(QUEUE, q[-500:])
    return e["uid"]


# ------------------------------------------------------------------ Übertragung
def _auth(c):
    tok = base64.b64encode(f"{c['user']}:{c['app_password']}".encode()).decode()
    return {"Authorization": "Basic " + tok, "User-Agent": UA}


def _request(c, path, data=None, headers=None, timeout=60):
    req = urllib.request.Request(c["url"] + ROUTE + path, data=data, headers={**_auth(c), **(headers or {})},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("message")
        except Exception:
            msg = None
        hint = {401: "Benutzer oder Anwendungspasswort falsch", 403: "dieser Benutzer darf keine Dateien hochladen",
                404: "Plugin „HamScanPi SSTV-Galerie“ nicht gefunden – installiert und aktiviert?"}.get(e.code)
        raise RuntimeError(f"HTTP {e.code}: {hint or msg or e.reason}")
    except urllib.error.URLError as e:
        raise ConnectionError(f"Webseite nicht erreichbar ({e.reason})")
    except (TimeoutError, socket.timeout):
        raise ConnectionError("Webseite antwortet nicht (Zeitüberschreitung)")


def _multipart(fields, files):
    b = uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += (f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n").encode() + str(v).encode() + b"\r\n"
    for k, (name, data, ctype) in files.items():
        out += (f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + data + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={b}"


def ping(c=None):
    c = c or cfg()
    if not (c["url"] and c["user"] and c["app_password"]):
        raise ValueError("Adresse, Benutzer und Anwendungspasswort eintragen")
    return _request(c, "/ping", timeout=20)


def upload(c, png, meta, uid, status, manual=False):
    data = Path(png).read_bytes()
    ctype = "image/png" if str(png).endswith(".png") else "image/jpeg"
    f = {"meta": json.dumps(meta, ensure_ascii=False), "uid": uid, "status": status}
    if manual:
        f["manual"] = 1                               # Plugin: „Hand-Uploads immer als Entwurf“ möglich
    body, ct = _multipart(f,
                          {"image": (Path(png).name, data, ctype)})
    return _request(c, "/images", data=body, headers={"Content-Type": ct}, timeout=120)


def _complete(meta):
    try:
        return float(meta.get("image", {}).get("complete") or 0) * 100
    except (TypeError, ValueError):
        return 0.0


def tick(now=None, log=None):
    """Aus dem Zeitplaner (jede Minute): fällige Bilder hochladen."""
    c = cfg()
    q = queue()
    if not q:
        return 0
    now = now or time.time()
    if not (c["url"] and c["user"] and c["app_password"]):
        return 0
    keep, sent, changed = [], 0, False
    for i, e in enumerate(q):
        if not c["enabled"] and not e.get("manual"):     # automatisches Hochladen aus: nur Nachgereichtes geht
            keep.append(e)
            continue
        if now - e["added"] > MAX_AGE_S:
            _log({"t": now, "key": e["key"], "state": "aufgegeben", "error": e.get("error")}); changed = True
            continue
        if sent >= PER_TICK or e["ready"] > now or e["next"] > now:
            keep.append(e)
            continue
        if not Path(e["png"]).exists():
            _log({"t": now, "key": e["key"], "state": "Bild fehlt", "error": "Datei gelöscht, bevor sie hochging"}); changed = True
            continue
        if not e.get("manual") and _complete(e["meta"]) < c["min_complete"]:
            _log({"t": now, "key": e["key"], "state": "zu unvollständig",
                  "error": f"{_complete(e['meta']):.0f} % < {c['min_complete']} %", "sat": _sat(e)}); changed = True
            continue
        try:
            r = upload(c, e["png"], e["meta"], e["uid"], c["status"], manual=bool(e.get("manual")))
            _log({"t": now, "key": e["key"], "state": "veröffentlicht" if r.get("status") == "publish" else "Entwurf",
                  "link": r.get("link"), "edit": r.get("edit"), "sat": _sat(e), "dup": r.get("duplicate", False)})
            sent += 1; changed = True
            if log:
                log("INFO", "publish", "sstv_uploaded", key=e["key"], status=r.get("status"), id=r.get("id"))
        except Exception as ex:
            e["tries"] += 1
            e["next"] = now + min(3600, 60 * 2 ** min(e["tries"], 6))
            e["error"] = str(ex)
            keep.append(e); changed = True
            if log and e["tries"] in (1, 5):
                log("WARN", "publish", "sstv_upload_failed", key=e["key"], tries=e["tries"], error=str(ex))
            if isinstance(ex, ConnectionError):          # Seite nicht erreichbar: die anderen gar nicht erst versuchen
                keep += q[i + 1:]
                break
    if changed:
        _write(QUEUE, keep)
    return sent


def _sat(e):
    return ((e.get("meta") or {}).get("summary") or {}).get("source")


# ------------------------------------------------------------------ Angaben zusammenstellen
def utc(t, date=True):
    """Zeit für die Angaben auf der Webseite – in der eingestellten Zeit (UTC oder Ortszeit), mit Kennung."""
    import timefmt as TF
    return TF.fmt(t, "%Y-%m-%d %H:%M:%S" if date else "%H:%M:%S")


def station_section():
    try:
        s = json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
    except Exception:
        s = {}
    try:
        import station as STN
        name = STN.name(s)
    except Exception:
        name = "HamScanPi"
    try:
        ver = (BASE / "VERSION").read_text().strip()
    except OSError:
        ver = "?"
    return {"call": s.get("my_call") or "", "locator": s.get("my_locator") or "", "name": name, "version": ver}, \
        {"title": "Station", "rows": [["Rufzeichen", s.get("my_call") or "–"], ["Locator", s.get("my_locator") or "–"],
                                      ["Gemeldet von", name], ["Software", f"HamScanPi {ver}"]]}


def pi_section():
    rows = []
    try:
        import sysmon as SM
        pi = SM.pi_info()
        rows.append(["Modell", pi.get("model")])
        rows.append(["System", pi.get("os")])
        t = SM.cpu_temp()
        if t is not None:
            rows.append(["CPU-Temperatur", f"{t:.1f} °C"])
        la = os.getloadavg()
        rows.append(["Last (1/5/15 min)", f"{la[0]:.2f} / {la[1]:.2f} / {la[2]:.2f}"])
        m = SM.meminfo() or {}
        if m:
            rows.append(["Arbeitsspeicher frei", f"{m.get('available')} von {m.get('total')} MB"])
        th = SM.throttled_now()
        if th is not None:
            rows.append(["Unterspannung/Drosselung", "ja" if th else "nein"])
        up = float(Path("/proc/uptime").read_text().split()[0])
        rows.append(["Läuft seit", f"{up / 86400:.1f} Tagen" if up > 172800 else f"{up / 3600:.1f} h"])
    except Exception:
        pass
    return {"title": "Raspberry Pi beim Empfang", "rows": [r for r in rows if r[1] is not None]}


def _tz():
    """Zeitzone für die Anzeige auf der Webseite (Tag der Gruppe, Uhrzeit): UTC oder die des Pi (IANA-Name)."""
    import timefmt as TF
    return "UTC" if TF.mode() == "utc" else (TF.tzname() or "UTC")


def build(source, t_img, image, sections, summary_extra=None, pi=True):
    """Gemeinsamer Rahmen: summary (zum Filtern/Gruppieren auf der Seite) + beschriftete Abschnitte.
    pi=False: von Hand nachgereicht – der Zustand des Pi von jetzt sagt über den Empfang nichts."""
    st, st_sec = station_section()
    img_rows = [["Modus", image.get("mode")], ["Vollständigkeit", f"{image.get('complete', 0) * 100:.0f} % der Zeilen"],
                ["Empfangen", utc(t_img)]]
    if image.get("headless"):
        img_rows.append(["Hinweis", "ohne Kopf – Aufnahme begann mitten im Bild"])
    if image.get("offset_hz"):
        img_rows.append(["Gegenstation verstimmt", f"{image['offset_hz']:+d} Hz"])
    summary = {"source": source, "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_img)), "mode": image.get("mode"),
               "complete": round(float(image.get("complete") or 0), 3), "call": st["call"], "locator": st["locator"],
               "station": st["name"], "tz": _tz(), **(summary_extra or {})}
    return {"v": 1, "summary": summary, "image": {"mode": image.get("mode"), "complete": image.get("complete")},
            "sections": [{"title": "Bild", "rows": img_rows}, *sections, st_sec, *([pi_section()] if pi else [])]}


# ------------------------------------------------------------------ Satellit
def _sat_name(sat):
    """„ISS“ bzw. „UmKA-1 (UMKA-1 (RS40S))“ – den Objektnamen nur, wenn er etwas hinzufügt."""
    import satellites as SAT
    obj = SAT.OBJECTS.get(sat["norad"]) or ""
    return sat.get("name") if not obj or obj == sat.get("name") else f"{sat.get('name')} ({obj})"


def sat_rows(tr, sat, f0, t):
    """Satellit und Überflug zum Zeitpunkt t (automatisch nach dem Überflug und beim Nachreichen gleich)."""
    import satellites as SAT
    el, az, rng, rr = tr.look(t)
    dop = tr.doppler_hz(f0, t)
    aos, top, los = SAT.pass_geometry(tr, t)
    try:
        ep = (tr.sat.jdsatepoch + tr.sat.jdsatepochF - 2440587.5) * 86400.0
        tle_age = f"{abs(t - ep) / 86400:.1f} Tage"
    except Exception:
        tle_age = None
    d = SAT.compass
    rows = [["Satellit", _sat_name(sat)],
            ["NORAD-Nummer", str(sat["norad"]) if str(sat["norad"]).isdigit() else None],
            ["Frequenz (Soll)", f"{f0 / 1e6:.4f} MHz"],
            ["Empfangen auf", f"{(f0 + dop) / 1e6:.4f} MHz"],
            ["Doppler beim Bild", f"{dop / 1e3:+.2f} kHz"],
            ["Aufgang", f"{utc(aos[0], False)} · {d(aos[2])}"],
            ["Höchster Punkt", f"{utc(top[0], False)} · {top[1]:.0f}° Elevation · {d(top[2])}"],
            ["Untergang", f"{utc(los[0], False)} · {d(los[2])}"],
            ["Beim Bild: Elevation", f"{el:.1f}°"], ["Beim Bild: Richtung", d(az)],
            ["Beim Bild: Entfernung", f"{rng:,.0f} km".replace(",", ".")],
            ["Beim Bild: Bewegung", f"{'nähert sich' if rr < 0 else 'entfernt sich'} mit {abs(rr):.2f} km/s"],
            ["Alter der Bahndaten", tle_age]]
    return [r for r in rows if r[1]], {"max_el": round(top[1], 1), "el": round(el, 1)}


# ------------------------------------------------------------------ von Hand nachreichen
_NOTE = re.compile(r"SSTV\s+([^,(]+?)\s*(?:[,(]|$)")              # „SSTV Robot 36 (ISS), …“ → Robot 36
_PCT = re.compile(r"(\d+)\s*%\s*der Zeilen")


def _image_info(rec_path, note):
    """Modus und Vollständigkeit aus Notiz bzw. Dekoder-Ergebnis des Treffers."""
    texts = [note or ""]
    try:
        d = json.loads((BASE / "reports" / f"{Path(rec_path).stem}_decode.json").read_text(encoding="utf-8"))
        texts += [x.get("text") or "" for x in d.get("decoders", []) if x.get("type") == "SSTV"]
    except Exception:
        pass
    mode = complete = None
    for t in texts:
        m = _NOTE.search(t)
        mode = mode or (m.group(1) if m else None)
        m = _PCT.search(t)
        complete = complete if complete is not None else (int(m.group(1)) / 100 if m else None)
    return {"mode": mode or "unbekannt", "complete": complete if complete is not None else 1.0}


def _t_from_stem(stem, fallback):
    try:
        return calendar.timegm(time.strptime(stem[:15], "%Y%m%d_%H%M%S"))
    except ValueError:
        return fallback


def manual(row, png):
    """SSTV-Treffer aus der Datenbank von Hand hochladen. row: band, segment_id, rf_hz, snr_db, note, antenna, time,
    sideband, recording_path. Rückgabe Kennung (oder ValueError mit Hinweis für das Dashboard)."""
    c = cfg()
    if not (c["url"] and c["user"] and c["app_password"]):
        raise ValueError("Webseite noch nicht eingerichtet – Einstellungen → Webseite")
    if not Path(png).exists():
        raise ValueError("Bild nicht mehr vorhanden")
    rec = row.get("recording_path") or str(png)
    try:
        t_scan = calendar.timegm(time.strptime(str(row.get("time"))[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        t_scan = os.path.getmtime(png)
    t = _t_from_stem(Path(rec).stem, t_scan)
    img = _image_info(rec, row.get("note"))
    rf = float(row.get("rf_hz") or 0)
    rx = [["Signal", f"{float(row.get('snr_db') or 0):.1f} dB über dem Rauschen"], ["Antenne", row.get("antenna")],
          ["Übertragung", "von Hand aus dem Dashboard nachgereicht"]]
    if row.get("band") == "Satelliten":
        import satellites as SAT
        sat = next((x for x in SAT.SATELLITES if x["id"] == row.get("segment_id")), None)
        if sat is None:
            raise ValueError("Satellit nicht mehr in der Liste")
        f0 = float(sat["center_hz"])
        obj = SAT.OBJECTS.get(sat["norad"]) or sat.get("name") or sat["id"]
        extra = {"kind": "satellite", "norad": str(sat["norad"]), "freq_hz": round(f0), "snr_db": round(float(row.get("snr_db") or 0), 1)}
        secs = []
        try:
            s = json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
            lat, lon = SAT.locator_to_latlon(s.get("my_locator", ""))
            tles, _ = SAT.load_tles(fetch=False)
            tr = SAT.Tracker(tles[sat["norad"]], lat, lon)
            ep = (tr.sat.jdsatepoch + tr.sat.jdsatepochF - 2440587.5) * 86400.0
            if abs(t - ep) > 30 * 86400:                    # Bahndaten passen nicht mehr zum Tag des Bildes
                raise ValueError("Bahndaten zu alt")
            rows, geo = sat_rows(tr, sat, f0, t)
            extra.update(geo)
            secs.append({"title": "Satellit und Überflug", "rows": rows})
        except Exception:
            secs.append({"title": "Satellit", "rows": [["Satellit", _sat_name(sat)],
                                                      ["Frequenz (Soll)", f"{f0 / 1e6:.4f} MHz"]]})
        secs.append({"title": "Empfang", "rows": [r for r in rx if r[1]]})
        source = obj
    else:
        band = row.get("band") or "?"
        rx = [["Band", band], ["Bildmitte (1900 Hz)", f"{rf / 1e6:.4f} MHz"], ["Seitenband", row.get("sideband")]] + rx
        secs = [{"title": "Empfang", "rows": [r for r in rx if r[1]]}]
        source = f"Kurzwelle {band}"
        extra = {"kind": "hf", "band": band, "freq_hz": round(rf)}
    extra["manual"] = True
    meta = build(source, t, img, secs, extra, pi=False)
    return enqueue(png, meta, key=key_for(png), manual=True)


def test_image(path):
    """Kleines Testbild (320×256 wie Robot 36) mit Farbbalken und Schrift."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (320, 256), (20, 30, 45))
    d = ImageDraw.Draw(im)
    cols = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0)]
    for i, col in enumerate(cols):
        d.rectangle([i * 40, 0, i * 40 + 39, 150], fill=col)
    d.text((12, 170), "HamScanPi – Testbild", fill=(255, 255, 255))
    d.text((12, 195), time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()), fill=(200, 210, 220))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "PNG")
