#!/usr/bin/env python3
"""Hinweise für den Benutzer – an einer Stelle gesammelt (data/notices.json), im Dashboard oben als Zähler
und unter System → Hinweise mit Abhilfe, im Protokoll und – je nach Einstellung – per Mail/Push.

Zwei Arten:
  Zustand  (raise_/clear): besteht, solange die Ursache besteht – z. B. „Haupt-Stick fehlt“,
           „Dateisystem nur lesbar“. Wird gemeldet, wenn er neu auftritt, und als behoben vermerkt.
  Ereignis (event):        einmalig – z. B. „System neu gestartet“, „Stick gewechselt“, „Kartenfehler“.
           Bleibt sichtbar, bis er quittiert wird.

Jeder Hinweis: id, level (error|warn|info), area (Bereich), title, detail, fix (was tun), cmd (Befehl zum
Kopieren), first/last, count, active, acked, kind (state|event), key (Ereignisart für Benachrichtigungen).
"""
import fcntl
import json
import time
from contextlib import contextmanager
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FILE = BASE / "data" / "notices.json"
LOCK = BASE / "data" / "notices.lock"
KEEP_S = 7 * 86400                     # behobene/quittierte Hinweise so lange aufheben
AREAS = {"hardware": "Stick/Hardware", "storage": "Speicherkarte", "system": "System",
         "software": "Software", "satellite": "Satelliten", "scan": "Scan"}
LOG_LEVEL = {"error": "ERROR", "warn": "WARN", "info": "INFO"}


@contextmanager
def _locked():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d):
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FILE)


def _log(rec, event):
    try:
        from applog import log
        log(LOG_LEVEL.get(rec["level"], "INFO"), "notice", event, id=rec["id"], area=rec["area"],
            title=rec["title"], detail=(rec.get("detail") or "")[:300])
    except Exception:
        pass


def _notify(rec):
    try:
        import notify as N
        N.notify_notice(rec)
    except Exception:
        pass


def raise_(nid, level, area, title, detail="", fix="", cmd="", key=None, notify=True):
    """Zustand setzen (oder auffrischen). Gemeldet wird nur beim Auftreten, nicht bei jeder Wiederholung."""
    now = time.time()
    with _locked():
        d = _load()
        r = d.get(nid)
        new = r is None or not r.get("active")
        if r is None:
            r = {"id": nid, "first": now, "count": 0, "kind": "state"}
        r.update(level=level, area=area, title=title, detail=detail, fix=fix, cmd=cmd, key=key or nid,
                 last=now, active=True, acked=False if new else r.get("acked", False))
        if new:
            r["first"], r["count"] = now, r.get("count", 0) + 1
            r.pop("resolved", None)
        d[nid] = r
        _save(d)
    if new:
        _log(r, "notice_raised")
        if notify:
            _notify(r)
    return r


def clear(nid, note=""):
    """Zustand ist behoben."""
    with _locked():
        d = _load()
        r = d.get(nid)
        if not r or not r.get("active"):
            return
        r.update(active=False, resolved=time.time(), resolved_note=note)
        _save(d)
    try:
        from applog import log
        log("INFO", "notice", "notice_resolved", id=nid, title=r["title"], note=note or None)
    except Exception:
        pass


def event(key, level, area, title, detail="", fix="", cmd="", notify=True):
    """Einmaliges Ereignis – sichtbar bis zum Quittieren."""
    now = time.time()
    r = {"id": f"{key}_{int(now * 1000)}", "kind": "event", "key": key, "level": level, "area": area,
         "title": title, "detail": detail, "fix": fix, "cmd": cmd, "first": now, "last": now, "count": 1,
         "active": True, "acked": False}
    with _locked():
        d = _load()
        d[r["id"]] = r
        _save(d)
    _log(r, "notice_event")
    if notify:
        _notify(r)
    return r


def ack(nid=None):
    """Quittieren: ein Hinweis oder (nid=None) alle. Zustände bleiben aktiv, bis die Ursache weg ist."""
    with _locked():
        d = _load()
        for k, r in d.items():
            if nid is None or k == nid:
                r["acked"] = True
                if r.get("kind") == "event":
                    r["active"] = False
                    r["resolved"] = time.time()
        _save(d)


def listing():
    """Für das Dashboard: aktive zuerst (Fehler vor Warnungen), dann behobene der letzten 7 Tage."""
    now = time.time()
    with _locked():
        d = _load()
        old = [k for k, r in d.items() if not r.get("active") and now - r.get("resolved", r.get("last", now)) > KEEP_S]
        for k in old:
            d.pop(k)
        if old:
            _save(d)
    order = {"error": 0, "warn": 1, "info": 2}
    items = sorted(d.values(), key=lambda r: (not r.get("active"), r.get("acked", False),
                                              order.get(r.get("level"), 3), -r.get("last", 0)))
    unseen = [r for r in items if r.get("active") and not r.get("acked")]
    return {"items": items, "open": len(unseen),
            "worst": min((order.get(r["level"], 3) for r in unseen), default=None), "areas": AREAS}


def is_active(nid):
    return bool(_load().get(nid, {}).get("active"))
