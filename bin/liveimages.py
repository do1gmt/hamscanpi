"""Bilder aus Live (SSTV, Wetterfax) – Liste, Vorschaubilder, behalten (★), löschen, auf die Webseite.

Die Live-Dekoder legen die Bilder als reports/live_sstv_<UTC>.png bzw. reports/live_wefax_<UTC>.png ab, dazu
eine kleine Beschreibung <name>.json (Frequenz, Demodulator, Bildart, Vollständigkeit). Aufgeräumt wird nach
30 Tagen (housekeeping 2c) – außer den mit ★ behaltenen (data/live_keep.json, kommt in jede Sicherung).
Vorschaubilder (240 px breit) entstehen bei Bedarf im RAM-Ordner, nicht auf der Speicherkarte.
"""
import calendar
import json
import os
import re
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
REPORTS = BASE / "reports"
KEEP = BASE / "data" / "live_keep.json"
NAME_RE = re.compile(r"^live_(sstv|wefax)_(\d{8})_(\d{6})\.png$")
KEEP_DAYS = 30
THUMB_W = 240
_lock = threading.Lock()


def checked(name):
    name = str(name or "")
    if not NAME_RE.match(name) or not (REPORTS / name).is_file():
        raise ValueError("Bild nicht gefunden")
    return REPORTS / name


def keep_list():
    try:
        d = json.loads(KEEP.read_text(encoding="utf-8"))
        return [str(x) for x in d] if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _save_keep(names):
    KEEP.parent.mkdir(parents=True, exist_ok=True)
    tmp = KEEP.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(set(names))), encoding="utf-8")
    os.replace(tmp, KEEP)


def set_keep(name, on):
    checked(name)
    with _lock:
        k = set(keep_list())
        (k.add if on else k.discard)(name)
        _save_keep(k)


def meta_path(png):
    return Path(png).with_suffix(".json")


def write_meta(png, **info):
    """Beschreibung neben das Bild legen (vom Live-Dekoder bei jedem Speichern)."""
    try:
        tmp = meta_path(png).with_suffix(".jtmp")
        tmp.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, meta_path(png))
    except OSError:
        pass


def _meta(png):
    try:
        return json.loads(meta_path(png).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def listing(limit=300):
    keep = set(keep_list())
    out = []
    for p in REPORTS.glob("live_*.png"):
        m = NAME_RE.match(p.name)
        if not m:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        t = listing_time(p.name)
        md = _meta(p)
        out.append({"name": p.name, "kind": m.group(1), "t": t, "size": st.st_size, "mtime": st.st_mtime,
                    "keep": p.name in keep, "freq_hz": md.get("freq_hz"), "demod": md.get("mode"),
                    "image_mode": md.get("image_mode"), "complete": md.get("complete"), "lines": md.get("lines"),
                    "expires": None if p.name in keep else st.st_mtime + KEEP_DAYS * 86400})
    out.sort(key=lambda x: x["t"], reverse=True)
    return out[:limit]


def delete(name):
    p = checked(name)
    p.unlink(missing_ok=True)
    meta_path(p).unlink(missing_ok=True)
    with _lock:
        k = keep_list()
        if name in k:
            _save_keep([x for x in k if x != name])


def thumb(name):
    """Vorschaubild (PNG, 240 px breit) – im RAM-Ordner zwischengespeichert, neu, wenn das Bild gewachsen ist."""
    p = checked(name)
    import rundir
    d = rundir.ensure() / "thumbs"
    d.mkdir(exist_ok=True)
    mt = int(p.stat().st_mtime)
    t = d / f"{p.stem}_{mt}.png"
    if t.exists():
        return t
    for old in d.glob(f"{p.stem}_*.png"):                    # ältere Fassung desselben Bildes
        old.unlink(missing_ok=True)
    from PIL import Image
    with Image.open(p) as im:
        im = im.convert("RGB") if im.mode not in ("RGB", "L") else im
        w, h = im.size
        if w > THUMB_W:
            im = im.resize((THUMB_W, max(1, round(h * THUMB_W / w))))
        tmp = t.with_suffix(".tmp")
        im.save(tmp, "PNG", optimize=True)
        os.replace(tmp, t)
    return t


def publish(name):
    """SSTV-Bild aus Live von Hand auf die Webseite (gleiche Kennung wie beim automatischen Hochladen)."""
    p = checked(name)
    if not name.startswith("live_sstv_"):
        raise ValueError("Auf die Webseite gehen nur SSTV-Bilder")
    import publish as PUB
    md = _meta(p)
    start = md.get("start") or listing_time(name)
    rows = [["Frequenz", f"{md['freq_hz'] / 1e6:.4f} MHz" if md.get("freq_hz") else ""],
            ["Betriebsart", str(md.get("mode") or "").upper()], ["Empfang", "Live (im Dashboard eingestellt)"],
            ["Antenne", md.get("antenna") or ""]]
    meta = PUB.build(f"Live {md['freq_hz'] / 1e6:.3f} MHz" if md.get("freq_hz") else "Live", start,
                     {"mode": md.get("image_mode") or "SSTV", "complete": md.get("complete") or 0},
                     [{"title": "Empfang", "rows": [r for r in rows if r[1]]}],
                     {"kind": "live", "freq_hz": int(md.get("freq_hz") or 0)}, pi=False)
    if not PUB.enqueue(p, meta, manual=True):
        raise ValueError("SSTV-Export ist nicht eingerichtet (Einstellungen → SSTV-Export: Adresse der Seite)")
    return PUB.states().get(PUB.key_for(p))


def listing_time(name):
    m = NAME_RE.match(name)
    return calendar.timegm(time.strptime(m.group(2) + m.group(3), "%Y%m%d%H%M%S"))


def cleanup(now=None, log=None):
    """Housekeeping: Bilder (und ihre Beschreibung) älter als 30 Tage löschen – außer ★; verwaiste
    Beschreibungen und Einträge in der ★-Liste ohne Bild entfernen."""
    now = now or time.time()
    keep = set(keep_list())
    n = 0
    for p in REPORTS.glob("live_*.png"):
        if not NAME_RE.match(p.name) or p.name in keep:
            continue
        try:
            if now - p.stat().st_mtime > KEEP_DAYS * 86400:
                p.unlink()
                meta_path(p).unlink(missing_ok=True)
                n += 1
        except OSError:
            pass
    for j in REPORTS.glob("live_*.json"):
        if not j.with_suffix(".png").exists():
            j.unlink(missing_ok=True)
    gone = [k for k in keep if not (REPORTS / k).exists()]
    if gone:
        with _lock:
            _save_keep([k for k in keep_list() if k not in gone])
    return n
