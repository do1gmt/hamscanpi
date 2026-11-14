#!/usr/bin/env python3
"""Ordner im Arbeitsspeicher für Dateien, die sich laufend ändern und nach einem Neustart nichts mehr bedeuten
(Pegelanzeige jede Sekunde, Lebenszeichen des Zeitplaners alle 10 s). Auf der Speicherkarte kostete jede dieser
kleinen Änderungen einen Journal-Schreibvorgang – das schont die Karte.

/dev/shm ist unter Linux immer ein RAM-Dateisystem. Der Ordnername enthält Benutzer und Installationsort, damit
sich mehrere Installationen (oder der Selbsttest) nicht in die Quere kommen. Geht /dev/shm nicht: data/.

Der Ordner kann zur Laufzeit verschwinden: systemd-logind räumt mit RemoveIPC=yes (Vorgabe) alles in /dev/shm ab,
was dem Benutzer gehört, sobald dessen letzte Anmeldung endet – z. B. nach dem Abmelden von SSH. Deshalb legen
touch()/ensure() ihn bei Bedarf neu an (und setup_system.sh schaltet RemoveIPC für den Pi ab).
"""
import hashlib
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
_DIR = None


def directory():
    global _DIR
    if _DIR is None:
        d = Path("/dev/shm") / f"hamscanpi-{os.getuid()}-{hashlib.sha1(str(BASE).encode()).hexdigest()[:8]}"
        try:
            d.mkdir(mode=0o700, exist_ok=True)
            if not os.access(d, os.W_OK):
                raise OSError
            _DIR = d
        except OSError:
            _DIR = BASE / "data"
    return _DIR


def path(name):
    return directory() / name


def ensure():
    """Ordner sicherstellen (war er weg, wird er neu angelegt und das einmal protokolliert)."""
    d = directory()
    if not d.is_dir():
        try:
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
            from applog import log
            log("WARN", "rundir", "rundir_recreated", dir=str(d),
                hint="von systemd-logind beim Abmelden geleert (RemoveIPC) – neu angelegt")
        except Exception:
            pass
    return d


def touch(p):
    """Lebenszeichen setzen – auch wenn der Ordner inzwischen geleert wurde."""
    try:
        Path(p).touch()
    except FileNotFoundError:
        ensure()
        Path(p).touch()
