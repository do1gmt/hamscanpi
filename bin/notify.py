#!/usr/bin/env python3
"""
Mailversand für definierte Ereignisse. Einstellungen in config/web_settings.json unter "notify":
{ "enabled": true, "smtp_host": "...", "smtp_port": 587, "smtp_user": "...", "smtp_pass": "...",
  "mail_from": "...", "mail_to": "...", "tls": "starttls"|"ssl"|"none",
  "on_error": true, "on_scan_done": true, "min_new_hits": 1, "on_live": false, "throttle_min": 30 }
Aufruf aus run_cycle: notify.py scan_done RUN_ID
"""
import json
import smtplib
import sys
import time
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SETTINGS = BASE / "config" / "web_settings.json"
STATE = BASE / "data" / "notify_state.json"


def cfg():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8")).get("notify", {}) or {}
    except Exception:
        return {}


def _state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st):
    try:
        STATE.write_text(json.dumps(st))
    except Exception:
        pass


def send_mail(subject, body, settings=None):
    c = settings or cfg()
    if not c.get("smtp_host") or not c.get("mail_to"):
        raise ValueError("SMTP-Server oder Empfänger fehlt")
    msg = EmailMessage()
    msg["Subject"] = "[HamScanPi] " + subject
    msg["From"] = c.get("mail_from") or c.get("smtp_user") or "pmsdr@localhost"
    msg["To"] = c["mail_to"]
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(body)
    port = int(c.get("smtp_port", 587))
    tls = c.get("tls", "starttls")
    if tls == "ssl":
        s = smtplib.SMTP_SSL(c["smtp_host"], port, timeout=20)
    else:
        s = smtplib.SMTP(c["smtp_host"], port, timeout=20)
        if tls == "starttls":
            s.starttls()
    if c.get("smtp_user"):
        s.login(c["smtp_user"], c.get("smtp_pass", ""))
    s.send_message(msg)
    s.quit()


def _throttled(key, minutes):
    st = _state()
    last = float(st.get(key, 0))
    if time.time() - last < minutes * 60:
        return True
    st[key] = time.time()
    _save_state(st)
    return False


def notify_error(component, event, data):
    c = cfg()
    if not (c.get("enabled") and c.get("on_error")):
        return
    if _throttled("error", float(c.get("throttle_min", 30))):
        return
    try:
        send_mail(f"Fehler: {component} / {event}",
                  f"Komponente: {component}\nEreignis: {event}\n\n{json.dumps(data, ensure_ascii=False, indent=2)}\n", c)
    except Exception as e:
        _log_fail(e)


def notify_scan_done(run_id):
    c = cfg()
    if not (c.get("enabled") and c.get("on_scan_done")):
        return
    import sqlite3
    db = sqlite3.connect(BASE / "data" / "scanner.db")
    db.row_factory = sqlite3.Row
    run = db.execute("SELECT * FROM run WHERE id=?", (int(run_id),)).fetchone()
    if not run:
        return
    hits = db.execute("""SELECT s.band, p.rf_hz, p.snr_db, COALESCE(p.mode,'?') AS mode
                         FROM peak p JOIN scan s ON s.id=p.scan_id WHERE s.time >= ? ORDER BY p.snr_db DESC""",
                      (run["started_at"],)).fetchall()
    db.close()
    if run["status"] != "success":
        subject = f"Scan #{run_id} fehlgeschlagen"
    else:
        if len(hits) < int(c.get("min_new_hits", 1)):
            return
        subject = f"Scan #{run_id}: {len(hits)} Treffer"
    lines = [f"Lauf #{run_id}  Status: {run['status']}  Start: {run['started_at']}  Dauer: {run['duration_s']} s",
             f"Treffer: {run['peaks']}  verworfen: {run['rejected']}", ""]
    for h in hits[:40]:
        lines.append(f"{h['band']:9s} {h['rf_hz']/1e6:10.4f} MHz  {h['snr_db']:5.1f} dB  {h['mode']}")
    if len(hits) > 40:
        lines.append(f"... und {len(hits)-40} weitere")
    try:
        send_mail(subject, "\n".join(lines) + "\n", c)
    except Exception as e:
        _log_fail(e)


def _log_fail(e):
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from applog import log
        log("WARN", "notify", "mail_failed", error=str(e))
    except Exception:
        pass


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "scan_done":
        notify_scan_done(sys.argv[2])
    elif len(sys.argv) >= 2 and sys.argv[1] == "test":
        send_mail("Testnachricht", "Der Mailversand von HamScanPi funktioniert.\n")
        print("gesendet")
