#!/usr/bin/env python3
"""Name dieser Station (Einstellungen → Station → „Name dieser Station“), Vorgabe „HamScanPi“.

Erscheint in Mail und Push (Absender/Betreff), im Browser-Tab und in der Kopfzeile, als Kennung im WSJT-X-Protokoll
(OpenHamClock) und bei PSK Reporter in der Antennenangabe – so lassen sich mehrere HamScanPis auseinanderhalten.
Daraus abgeleitet der Rechnername im Netz (hostname_for): „HamScanPi-AFGN“ → hamscanpi-afgn → hamscanpi-afgn.local.
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DEFAULT = "HamScanPi"
_UML = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"}


def validate(name):
    """Gültiger Stationsname oder ValueError. Leer = Vorgabe."""
    n = re.sub(r"\s+", " ", str(name or "")).strip()
    if not n:
        return DEFAULT
    if len(n) > 32 or not re.fullmatch(r"[A-Za-z0-9ÄÖÜäöüß][A-Za-z0-9ÄÖÜäöüß _.-]*", n):
        raise ValueError("Name: höchstens 32 Zeichen – Buchstaben, Ziffern, Leerzeichen, Bindestrich, Punkt, Unterstrich")
    return n


def hostname_for(name):
    """Rechnername (a–z, 0–9, Bindestrich, höchstens 63 Zeichen) aus dem Stationsnamen."""
    n = "".join(_UML.get(c, c) for c in str(name or DEFAULT)).lower()
    n = re.sub(r"[^a-z0-9]+", "-", n).strip("-")[:63].strip("-")
    return n or "hamscanpi"


def name(settings=None):
    if settings is None:
        try:
            settings = json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    try:
        return validate(settings.get("station_name"))
    except ValueError:
        return DEFAULT


def custom(settings=None):
    """True, wenn ein eigener Name eingetragen ist (nicht die Vorgabe)."""
    return name(settings) != DEFAULT
