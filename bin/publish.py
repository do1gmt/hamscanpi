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
"""
import base64
import hashlib
import json
import os
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
    _write(LOG, (read_log() + [entry])[-100:])


def enqueue(png, meta, key=None, settle_s=0):
    """Bild zum Hochladen vormerken. key: gleiche Kennung = Eintrag auffrischen (wachsendes Live-Bild)."""
    c = cfg()
    if not (c["enabled"] and c["url"]):
        return None
    png = str(png)
    key = key or f"{socket.gethostname()}-{Path(png).name}"
    q = queue()
    now = time.time()
    e = next((x for x in q if x["key"] == key), None)
    if e is None:
        e = {"key": key, "uid": hashlib.sha1(key.encode()).hexdigest()[:20], "added": now, "tries": 0, "next": 0}
        q.append(e)
    e.update(png=png, meta=meta, ready=now + settle_s)
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


def upload(c, png, meta, uid, status):
    data = Path(png).read_bytes()
    ctype = "image/png" if str(png).endswith(".png") else "image/jpeg"
    body, ct = _multipart({"meta": json.dumps(meta, ensure_ascii=False), "uid": uid, "status": status},
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
    if not (c["enabled"] and c["url"] and c["user"] and c["app_password"]):
        return 0
    keep, sent, changed = [], 0, False
    for i, e in enumerate(q):
        if now - e["added"] > MAX_AGE_S:
            _log({"t": now, "key": e["key"], "state": "aufgegeben", "error": e.get("error")}); changed = True
            continue
        if sent >= PER_TICK or e["ready"] > now or e["next"] > now:
            keep.append(e)
            continue
        if not Path(e["png"]).exists():
            _log({"t": now, "key": e["key"], "state": "Bild fehlt", "error": "Datei gelöscht, bevor sie hochging"}); changed = True
            continue
        if _complete(e["meta"]) < c["min_complete"]:
            _log({"t": now, "key": e["key"], "state": "zu unvollständig",
                  "error": f"{_complete(e['meta']):.0f} % < {c['min_complete']} %", "sat": _sat(e)}); changed = True
            continue
        try:
            r = upload(c, e["png"], e["meta"], e["uid"], c["status"])
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


def build(source, t_img, image, sections, summary_extra=None):
    """Gemeinsamer Rahmen: summary (zum Filtern/Gruppieren auf der Seite) + beschriftete Abschnitte."""
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
            "sections": [{"title": "Bild", "rows": img_rows}, *sections, st_sec, pi_section()]}


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
