#!/usr/bin/env python3
"""Zeitangaben einheitlich: UTC oder Ortszeit des Pi – Einstellungen → Station → Zeitangaben (Vorgabe UTC).

Gilt für alles, was ein Mensch liest: Dashboard, Hinweise, Mail, Telegram, Webseite. Jede Angabe trägt ihre
Kennung („UTC“ bzw. „CET“/„CEST“ …), nie bleibt offen, welche Zeit gemeint ist. Gespeichert wird intern immer
in UTC (Datenbank, Protokoll); nur die Anzeige folgt der Einstellung. In Bilder eingebrannte Zeiten (Wasserfall)
bleiben UTC und sind so beschriftet.
"""
import json
import os
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
MODES = ("utc", "local")


def mode(settings=None):
    if settings is None:
        try:
            settings = json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    m = settings.get("time_mode")
    return m if m in MODES else "utc"


def tzname():
    """IANA-Name der Zeitzone des Pi (z. B. Europe/Berlin) – für den Browser; None wenn unbekannt."""
    if os.environ.get("TZ", "").count("/") == 1:          # für diesen Dienst gesetzt (z. B. im Test)
        return os.environ["TZ"].lstrip(":")
    try:
        t = Path("/etc/timezone").read_text().strip()
        if t:
            return t
    except OSError:
        pass
    try:
        p = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in p:
            return p.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return os.environ.get("TZ") or None


_DE = {"CET": "MEZ", "CEST": "MESZ"}       # wie im Dashboard (Kürzel in der Sprache der Oberfläche)


def label(t=None, settings=None, m=None):
    m = m or mode(settings)
    if m == "utc":
        return "UTC"
    z = time.strftime("%Z", time.localtime(t if t is not None else time.time()))
    try:
        if settings is None:
            settings = json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
        de = (settings.get("ui_lang") or "de") != "en"
    except Exception:
        de = True
    return _DE.get(z, z) if de else z


def fmt(t, f="%d.%m. %H:%M", settings=None, m=None, with_label=True):
    """Zeit t (Unix) als Text in der eingestellten Zeit, mit Kennung."""
    m = m or mode(settings)
    s = time.strftime(f, time.gmtime(t) if m == "utc" else time.localtime(t))
    return f"{s} {label(t, settings, m=m)}" if with_label else s


def hm(t, settings=None):
    return fmt(t, "%H:%M", settings)


def struct(t=None, settings=None):
    """time.struct_time in der eingestellten Zeit (für Tag/Stunde)."""
    t = time.time() if t is None else t
    return time.gmtime(t) if mode(settings) == "utc" else time.localtime(t)
