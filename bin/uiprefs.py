"""Einstellungen der Oberfläche auf dem Pi – je Profil (PC, Handy).

Was das Dashboard sonst nur im Browser (localStorage) hielte: Anordnung und eingeklappte Karten, Design, Sprache,
Filter der Treffer, zuletzt offener Reiter, Lautstärke und Verstärkung in Live, Karten- und Verlaufsansicht.
Gespeichert in data/ui_prefs.json: {"profiles": {"pc": {schlüssel: wert}, "handy": {...}}} – Werte sind Texte
(so wie im Browser). Kommt mit in jede Sicherung, enthält keine Zugangsdaten.

Der Webserver gibt alle Profile gleich mit der Seite aus (window.HSP_PREFS); web/prefs.js wählt das Profil,
legt die Werte in den Browser-Speicher, bevor Design, Sprache und Anordnung gelesen werden, und schickt
Änderungen gesammelt zurück (POST /api/ui-prefs).
"""
import json
import os
import re
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FILE = BASE / "data" / "ui_prefs.json"
PROFILES = ("pc", "handy")
KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
MAX_VALUE = 20_000              # Anordnung als JSON: wenige KB
MAX_KEYS = 80
_lock = threading.Lock()


def load():
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    prof = d.get("profiles") if isinstance(d.get("profiles"), dict) else {}
    return {"profiles": {p: dict(prof.get(p) or {}) for p in PROFILES}}


def _write(d):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, FILE)


def _clean(items):
    out = {}
    for k, v in (items or {}).items():
        if not KEY_RE.match(str(k)):
            raise ValueError(f"Einstellung: ungültiger Name {str(k)[:40]!r}")
        if v is None:
            out[str(k)] = None
            continue
        v = str(v)
        if len(v) > MAX_VALUE:
            raise ValueError(f"Einstellung {k}: zu lang")
        out[str(k)] = v
    return out


def update(profile, set_=None, replace=None, copy_from=None):
    """Profil ändern: set_ = einzelne Werte (None löscht), replace = ganzes Profil neu, copy_from = anderes Profil
    übernehmen. Gibt das Profil zurück."""
    if profile not in PROFILES:
        raise ValueError("Profil: pc oder handy")
    with _lock:
        d = load()
        if copy_from is not None:
            if copy_from not in PROFILES or copy_from == profile:
                raise ValueError("Profil zum Übernehmen: das jeweils andere")
            d["profiles"][profile] = dict(d["profiles"][copy_from])
        elif replace is not None:
            d["profiles"][profile] = {k: v for k, v in _clean(replace).items() if v is not None}
        else:
            p = d["profiles"][profile]
            for k, v in _clean(set_).items():
                if v is None:
                    p.pop(k, None)
                else:
                    p[k] = v
        if len(d["profiles"][profile]) > MAX_KEYS:
            raise ValueError("Zu viele Einstellungen")
        _write(d)
        return d["profiles"][profile]


def script_tag():
    """<script>window.HSP_PREFS=…</script> für den Kopf der Seite (</ sicher maskiert)."""
    js = json.dumps(load(), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\!--")
    return f"<script>window.HSP_PREFS={js};</script>"
