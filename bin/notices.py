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
                 last=now, active=True, acked=False if new else r.get("acked", False),
                 en={"title": english(title), "detail": english(detail), "fix": english(fix)})
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
         "active": True, "acked": False,
         "en": {"title": english(title), "detail": english(detail), "fix": english(fix)}}
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


# ---------------------------------------------------------------- Englische Fassung der Hinweise
# Die Prüfungen schreiben deutsch; für die englische Oberfläche wird beim Anlegen eine englische Fassung
# daneben gelegt (r["en"]). Erst ganze Sätze, dann Muster mit Werten. Was nicht passt, bleibt deutsch.
import re as _re

EN_EXACT = {
    "System neu gestartet": "System restarted", "Sticks lassen sich nicht abfragen": "Sticks cannot be queried",
    "Haupt-Stick fehlt": "Main stick missing", "Stick mit Allerwelts-Seriennummer": "Stick with a generic serial number",
    "Neuer Stick wartet auf Zuordnung": "New stick waiting for a role", "Kernel-Protokoll nicht lesbar": "Kernel log not readable",
    "Dateisystem nur noch lesbar": "File system is read-only", "Hohe Schreiblast auf der SD-Karte": "High write load on the SD card",
    "Arbeitsspeicher und zram voll": "Memory and zram full", "Unterspannung jetzt": "Undervoltage now", "Pi gedrosselt": "Pi throttled",
    "Uhr nicht synchron": "Clock not synchronised", "Bahndaten veraltet": "Orbital elements outdated",
    "Kurzwelle braucht einen anderen Treiber": "HF needs a different driver", "Treiber ohne Direktabtastung": "Driver without direct sampling",
    "Hängender Überflug beendet": "Hanging pass ended", "Speicherkarte meldet Fehler": "SD card reports errors",
    "Dateisystem meldet Fehler": "File system reports errors", "Lese-/Schreibfehler auf einem Laufwerk": "Read/write errors on a drive",
    "Speicher voll – der Kernel hat einen Prozess beendet": "Out of memory – the kernel ended a process",
    "Unterspannung erkannt": "Undervoltage detected", "Automatische Sicherung fehlgeschlagen": "Automatic backup failed",
    "Programm rtl_sdr fehlt – ohne geht kein RTL-Empfang": "Program rtl_sdr missing – no RTL reception without it",
    "multimon-ng fehlt – APRS und POCSAG werden nicht dekodiert": "multimon-ng missing – APRS and POCSAG are not decoded",
    "Fehler der Speicherkarte und Speichermangel werden darum nicht erkannt.": "SD card errors and out-of-memory are therefore not detected.",
    "Das System-Laufwerk wurde schreibgeschützt eingehängt – meist nach Fehlern der Speicherkarte. Aufnahmen, Datenbank und Einstellungen können nicht mehr gespeichert werden.":
        "The system drive was mounted read-only – usually after SD card errors. Recordings, database and settings can no longer be saved.",
    "Die Versorgung des Pi reicht gerade nicht – Sticks und Speicherkarte können ausfallen.": "The Pi's power supply is not sufficient right now – sticks and SD card may fail.",
    "Takt gesenkt (Unterspannung oder Hitze).": "Clock reduced (undervoltage or heat).", "Ab 80 °C drosselt der Pi.": "From 80 °C the Pi throttles.",
    "Satellitenzeiten und Doppler werden ungenau, FT8 dekodiert schlechter.": "Satellite times and Doppler become inaccurate, FT8 decodes worse.",
    "Ohne Absicht neu gestartet? Netzteil und Unterspannung prüfen (System → Zustand).": "Restarted unintentionally? Check power supply and undervoltage (System → Status).",
    "Unter System → Geräte festlegen: Haupt-Stick, Reserve oder ignorieren.": "Set under System → Devices: main stick, spare or ignore.",
    "Stick gewechselt? Unter System → Geräte den angesteckten zum Haupt-Stick machen.": "Stick changed? Make the connected one the main stick under System → Devices.",
    "Stick anstecken oder USB-Verbindung prüfen.": "Plug in the stick or check the USB connection.",
    "Unter System → Geräte „Seriennummer schreiben“ (vorher andere Sticks abziehen).": "System → Devices “Write serial number” (unplug other sticks first).",
    "Unter System → Geräte eine Rolle wählen.": "Choose a role under System → Devices.",
    "Benutzer in die Gruppe adm aufnehmen und neu anmelden:": "Add the user to the adm group and log in again:",
    "Sicherung herunterladen (System → Sicherung) und die Karte bald tauschen – Fehler kündigen oft einen Ausfall an. Auf eine SSD umziehen: README „SD card or SSD“.":
        "Download a backup (System → Backup) and replace the card soon – errors often announce a failure. Moving to an SSD: README “SD card or SSD”.",
    "Im Systemverlauf den größten Prozess zu dieser Zeit ansehen; Webserver neu starten gibt Speicher frei.": "Look at the largest process at that time in the system history; restarting the web server frees memory.",
    "Offizielles Netzteil 5,1 V/3 A verwenden, USB-Geräte über einen aktiven Hub versorgen.": "Use the official 5.1 V/3 A power supply, power USB devices via an active hub.",
    "Sicherung herunterladen, Pi neu starten; tritt es wieder auf, Karte tauschen.": "Download a backup, restart the Pi; if it happens again, replace the card.",
    "System → Aufräumen: alte Treffer löschen.": "System → Clean-up: delete old hits.",
    "Auf eine SSD umziehen (README „SD card or SSD“) oder Aufnahmen seltener behalten.": "Move to an SSD (README “SD card or SSD”) or keep fewer recordings.",
    "Im Systemverlauf den größten Prozess ansehen; Webserver neu starten gibt Speicher frei.": "Look at the largest process in the system history; restarting the web server frees memory.",
    "Offizielles Netzteil 5,1 V/3 A, kurzes dickes Kabel; USB-Geräte über einen aktiven Hub.": "Official 5.1 V/3 A power supply, short thick cable; USB devices via an active hub.",
    "Kühlung und Netzteil prüfen.": "Check cooling and power supply.", "Kühlkörper oder Lüfter, Gehäuse belüften.": "Heatsink or fan, ventilate the case.",
    "Internetverbindung prüfen; Zeitsynchronisation einschalten:": "Check the internet connection; switch on time synchronisation:",
    "Im Protokoll kurz vor der Uhrzeit nach Fehlern sehen.": "Look for errors in the log shortly before that time.",
    "Installieren:": "Install:", "Internetverbindung prüfen; von Hand holen:": "Check the internet connection; fetch manually:",
    "Treiber selbst übersetzen (README) – bis dahin nur ab 24 MHz.": "Build the driver yourself (README) – until then only from 24 MHz.",
    "Paket rtl-sdr aktualisieren:": "Update the rtl-sdr package:",
    "Kommt das öfter vor: USB-Kabel/Netzteil prüfen, Stick an einen USB-2-Port.": "If this happens often: check USB cable/power supply, stick on a USB 2 port.",
    "USB-Stick einstecken bzw. Netzwerkfreigabe einhängen, Zielordner unter System → Sicherung prüfen.": "Plug in the USB stick or mount the network share, check the target folder under System → Backup.",
    "Router, WLAN bzw. Netzwerkkabel prüfen.": "Check the router, Wi-Fi or network cable.",
}
EN_RX = [
    (r"^Stick angesteckt: (.+)$", r"Stick plugged in: \1"), (r"^Stick abgezogen: (.+)$", r"Stick removed: \1"),
    (r"^Speicher fast voll \((\d+) %\)$", r"Storage almost full (\1 %)"), (r"^Pi heiß \((\d+) °C\)$", r"Pi hot (\1 °C)"),
    (r"^Update eingespielt: (.+)$", r"Update installed: \1"), (r"^Webserver wurde neu gestartet$", "Web server was restarted"),
    (r"^Zeitplaner wurde neu gestartet$", "Scheduler was restarted"), (r"^Neuer SDR-Stick: (.+)$", r"New SDR stick: \1"),
    (r"^SDR erkannt: (.+)$", r"SDR detected: \1"), (r"^Python-Paket (\S+) fehlt$", r"Python package \1 missing"),
    (r"^FT8-Dekoder fehlt \((.+)\) – FT8/FT4 werden nicht dekodiert$", r"FT8 decoder missing (\1) – FT8/FT4 are not decoded"),
    (r"^Der Pi wurde neu gestartet \(vor (\d+) min\), zuletzt erreichbar (.+?)\.(?: Vorher lief er ([\d.]+) (h|Tage)\.)?$",
     lambda m: f"The Pi was restarted ({m.group(1)} min ago), last reachable {m.group(2).replace('unbekannt', 'unknown')}."
               + (f" Before that it ran {m.group(3)} {'h' if m.group(4) == 'h' else 'days'}." if m.group(3) else "")),
    (r"^(.+), Seriennummer (.+?), an (.+?)( – noch ohne Rolle, wird nicht benutzt\.)?$",
     lambda m: f"{m.group(1)}, serial number {m.group(2).replace('ohne', 'none')}, on {m.group(3)}"
               + (" – no role yet, not used." if m.group(4) else "")),
    (r"^Seriennummer (.+?)( – das war der Haupt-Stick\.)?$",
     lambda m: f"Serial number {m.group(1).replace('ohne', 'none')}" + (" – that was the main stick." if m.group(2) else "")),
    (r"^(.+) \(Seriennummer (.+?)\) steckt nicht\. Scan, Satelliten und Live auf VHF/UHF ruhen\.(?: Angesteckt ist: (.+)\.)?$",
     lambda m: f"{m.group(1)} (serial number {m.group(2)}) is not plugged in. Scans, satellites and Live on VHF/UHF are paused."
               + (f" Connected: {m.group(3)}." if m.group(3) else "")),
    (r"^(.+) hat die Seriennummer (.+)\. Mit einem zweiten Stick kann HamScanPi die beiden nicht sicher auseinanderhalten\.$",
     r"\1 has the serial number \2. With a second stick HamScanPi cannot reliably tell them apart."),
    (r"^(\d+)× im Kernel-Protokoll, zuletzt: (.+)$", r"\1× in the kernel log, last: \2"),
    (r"^Noch ([\d.]+) GB frei\.$", r"\1 GB free."),
    (r"^([\d.]+) GB in 24 h geschrieben – das verkürzt die Lebensdauer der Karte\.$", r"\1 GB written in 24 h – this shortens the card's life."),
    (r"^(\d+) von (\d+) MB zram belegt – als Nächstes muss der Kernel einen Prozess beenden\.$", r"\1 of \2 MB zram used – next the kernel has to end a process."),
    (r"^Der Dienst (\S+) hat sich beendet und wurde von systemd neu gestartet \((\d+)×\)\.$", r"The service \1 exited and was restarted by systemd (\2×)."),
    (r"^Zuletzt geholt (.+) – Überflugzeiten werden ungenau\.$", r"Last fetched \1 – pass times become inaccurate."),
    (r"^(.+) könnte Kurzwelle, der installierte Treiber aber nicht\.$", r"\1 could do HF, but the installed driver cannot."),
    (r"^Eine Satellitenaufnahme lief (\d+) min über ihr Ende hinaus \(Stick lieferte keine Daten mehr\) und wurde beendet, damit Scans und Überflüge weiterlaufen\.$",
     r"A satellite recording ran \1 min past its end (the stick delivered no more data) and was ended so that scans and passes continue."),
    (r"^(.+) \(Seriennummer (.+)\) steckt am Pi\. HamScanPi benutzt ihn erst, wenn du unter System → Geräte festlegst, was er sein soll \(Haupt-Stick, Reserve oder ignorieren\)\.$",
     r"\1 (serial number \2) is connected to the Pi. HamScanPi only uses it once you set under System → Devices what it should be (main stick, spare or ignore)."),
    (r"^(.+) \(Seriennummer (.+)\) steckt am Pi\. HamScanPi unterstützt dieses Gerät nicht und benutzt es nicht\.$",
     r"\1 (serial number \2) is connected to the Pi. HamScanPi does not support this device and does not use it."),
    (r"^Zielordner (.+) gibt es nicht \(USB-Stick nicht eingesteckt oder Freigabe nicht eingehängt\?\)$", r"Target folder \1 does not exist (USB stick not plugged in or share not mounted?)"),
    (r"^Zielordner (.+) ist nicht beschreibbar$", r"Target folder \1 is not writable"),
    (r"^(.+) liegt auf demselben Laufwerk wie HamScanPi – eine Sicherung dort hilft bei einem Kartenausfall nicht$", r"\1 is on the same drive as HamScanPi – a backup there does not help if the card fails"),
    (r"^kein Zielordner eingetragen$", "no target folder set"),
    (r"^Kein Internet seit (.+)$", r"No internet since \1"),
    (r"^Dauerwache ([\d.]+) MHz (\S+)( bis (.+))?$", lambda m: f"Continuous watch {m.group(1)} MHz {m.group(2)}" + (f" until {m.group(4)}" if m.group(4) else "")),
    (r"^Live läuft ohne Browser weiter .*$", "Live keeps running without a browser (decoders only). Scans pause meanwhile; satellite passes "
     "interrupt the watch briefly, then it continues on the same frequency."),
    (r"^Beenden: Live → Stopp oder den Haken „Dauerwache“ entfernen\.$", "To end: Live → Stop or untick “Continuous watch”."),
    (r"^Scan, Live, Satelliten und Dekoder laufen weiter\. Es fehlen: .*$",
     "Scans, Live, satellites and decoders keep running. Missing: PSK Reporter (the spots of the last hour are reported "
     "later), mail/push (sent later), new orbital elements and time synchronisation."),
]


def english(text):
    if not text:
        return text
    t = EN_EXACT.get(text)
    if t:
        return t
    for rx, rep in EN_RX:
        if _re.match(rx, text):
            return _re.sub(rx, rep, text)
    return text
