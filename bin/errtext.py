#!/usr/bin/env python3
"""Fehler aus dem Protokoll als lesbare Meldung (Mail, Telegram, ntfy) – statt „scheduler / loop_exception“ und
eines rohen Datensatzes: was passiert ist, ob Hesperos DX es selbst behebt, und nur wenn nötig, was zu tun ist.

text(component, event, data, en=False) -> (Titel, Text). Unbekannte Ereignisse: allgemeiner Satz und die Angaben als
„Name: Wert“-Zeilen (lange Felder wie Programmausgaben bleiben im Protokoll). Der Fachname steht klein am Ende –
für die Suche im Protokoll.
"""

COMP = {"scheduler": ("Zeitplaner", "scheduler"), "scanner": ("Scanner", "scanner"), "cycle": ("Scanlauf", "scan run"),
        "satellite": ("Satellit", "satellite"), "webapp": ("Dashboard", "dashboard"), "live": ("Live", "Live"),
        "housekeeping": ("Haushalt", "housekeeping"), "publish": ("Webseite", "website"), "notify": ("Meldungen", "notifications")}

LONG = {"where", "output_tail", "tail", "trace", "traceback"}


def _g(d, k, dflt="?"):
    v = d.get(k)
    return dflt if v in (None, "") else v


def _known(c, e, d, en):
    """(Titel, Was ist passiert, Was tun – oder None) für bekannte Fehler."""
    err = _g(d, "error", "")
    if (c, e) == ("scheduler", "loop_exception"):
        return (("Scheduler: one round failed", f"{err}\nThe scheduler keeps running and tries again every 10 s.",
                 "Only if this repeats: System → Services → restart the scheduler and send a diagnostics package.")
                if en else
                ("Zeitplaner: eine Runde fehlgeschlagen", f"{err}\nDer Zeitplaner läuft weiter und versucht es alle 10 s erneut.",
                 "Nur wenn sich das wiederholt: System → Dienste → Zeitplaner neu starten und ein Diagnosepaket schicken."))
    if (c, e) == ("scheduler", "scan_hung"):
        return (("Scan hung – stopped", f"No sign of life for {_g(d, 'silent_min')} min. The scan was ended, the next one starts as planned.",
                 "If it happens often: check stick and USB (powered hub, shorter cable).")
                if en else
                ("Scan hing – beendet", f"{_g(d, 'silent_min')} min kein Lebenszeichen. Der Scan wurde beendet, der nächste startet wie geplant.",
                 "Passiert das öfter: Stick und USB prüfen (aktiver Hub, kürzeres Kabel)."))
    if (c, e) == ("scheduler", "satellite_watchdog"):
        return (("Pass recording hung – stopped", "The recording ran far beyond the end of the pass and was ended.", None)
                if en else
                ("Überflug-Aufnahme hing – beendet", "Die Aufnahme lief weit über das Ende des Überflugs und wurde beendet. "
                 "Der nächste Überflug startet wie geplant.", None))
    if (c, e) in (("scheduler", "scan_skipped_disk_full"), ("housekeeping", "disk_full")):
        pct = d.get("used_pct")
        return (("Memory card almost full – no new scans", f"Used: {pct} %." if pct else "", "Settings → Clean up: delete old hits.")
                if en else
                ("Speicherkarte fast voll – keine neuen Scans", f"Belegt: {pct} %." if pct else "",
                 "Einstellungen → Aufräumen: alte Treffer löschen."))
    if (c, e) == ("scheduler", "restore_failed"):
        return (("Restore failed", err, "Check the backup file and try again (Settings → Backup).") if en else
                ("Wiederherstellung fehlgeschlagen", err, "Sicherungsdatei prüfen und noch einmal einspielen (Einstellungen → Sicherung)."))
    if (c, e) == ("scanner", "device_open_failed"):
        return ((f"Receiver {_g(d, 'device')} cannot be opened", err, None) if en else
                (f"Empfänger {_g(d, 'device')} lässt sich nicht öffnen", err, None))
    if (c, e) == ("scanner", "run_exception"):
        return (("Scan aborted", err, None) if en else ("Scan abgebrochen", err, None))
    if (c, e) == ("cycle", "run_failed"):
        return (("Scan run failed", str(_g(d, "error", "")).removeprefix("FEHLER: "), None) if en else
                ("Scanlauf fehlgeschlagen", str(_g(d, "error", "")).removeprefix("FEHLER: "), None))
    if (c, e) == ("webapp", "run_interrupted"):
        return (("Scan run interrupted", "The run ended without finishing (restart, service stopped or memory full).", None) if en else
                ("Scanlauf unterbrochen", "Der Lauf endete ohne Abschluss (Neustart, Dienst gestoppt oder Speicher voll).", None))
    if (c, e) == ("satellite", "pass_failed"):
        return ((f"Pass {_g(d, 'sat')} not recorded", err, None) if en else
                (f"Überflug {_g(d, 'sat')} nicht aufgenommen", err, None))
    if (c, e) == ("satellite", "pass_eval_failed"):
        return ((f"Evaluation of pass {_g(d, 'sat')} failed", err, "Raw data can be evaluated for 48 h: python3 bin/sat_recover.py") if en else
                (f"Auswertung des Überflugs {_g(d, 'sat')} fehlgeschlagen", err,
                 "Die Rohdaten lassen sich 48 h lang retten: python3 bin/sat_recover.py"))
    if (c, e) == ("housekeeping", "pass_aborted"):
        st = _g(d, "stage", "")
        return (("Pass did not finish", f"Stage: {st}. The recording process ended without a result.", d.get("hint")) if en else
                ("Überflug nicht beendet", f"Schritt: {st}. Der Aufnahmeprozess endete ohne Ergebnis.", d.get("hint")))
    if (c, e) == ("live", "start_failed"):
        return (("Live reception does not start", err, None) if en else ("Live-Empfang startet nicht", err, None))
    return None


def text(component, event, data, en=False):
    d = dict(data or {})
    k = _known(component, event, d, en)
    tag = f"({component} / {event})"
    if k:
        title, what, todo = k
        body = what.strip()
        if todo:
            body += ("\n\nWhat to do: " if en else "\n\nWas tun: ") + todo
        return title, f"{body}\n\n{tag}".strip()
    comp = COMP.get(component, (component, component))[1 if en else 0]
    rows = [f"{k_}: {v}" for k_, v in d.items() if k_ not in LONG and v not in (None, "") and len(str(v)) < 300]
    title = (f"{comp}: error" if en else f"{comp}: Fehler")
    body = "\n".join(rows) or ("No details." if en else "Keine Angaben.")
    return title, f"{body}\n\n{'Details: dashboard → Log' if en else 'Mehr im Dashboard unter Protokoll'} {tag}"
