#!/usr/bin/env python3
"""Notfall-Seite von HamScanPi – ein eigener, winziger Dienst (Standard: Port 8081), der weiterläuft, wenn der
Webserver hängt. Zeigt Zustand der Dienste, Speicher und die letzten Protokollzeilen und kann Webserver und
Zeitplaner neu starten, den Pi neu starten und ein Diagnose-Paket herunterladen.

Nur Standardbibliothek (kein numpy, kein Dashboard-Code), damit er auch bei vollem Speicher antwortet.
Geschützt wie das Dashboard (gleiches Passwort, gleiche Anmeldung, vertrauenswürdige Netze).
   python3 bin/rescue.py [port]
"""
import html
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "bin"))
import auth as AUTH  # noqa: E402
import services as SV  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8081



class _Server(ThreadingHTTPServer):
    """Warteschlange für neue Verbindungen: 128 statt 5 (Standard von socketserver). Mit 5 liefen bei vielen
    gleichzeitigen Abfragen (Live: Wasserfall und Status jede Sekunde, dazu Trefferliste, Hinweise …) neue Verbindungen
    über – der Kernel meldete „Possible SYN flooding on port 8080“, der Browser „NetworkError“ bzw. das Dashboard
    „Server nicht erreichbar“, obwohl der Dienst lief."""
    request_queue_size = 128
    daemon_threads = True

def log(level, event, **data):
    try:
        from applog import log as _l
        _l(level, "rescue", event, **data)
    except Exception:
        pass


def meminfo():
    m = {}
    try:
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            m[k] = int(v.split()[0]) // 1024
    except OSError:
        pass
    return m


def last_log(n=40):
    try:
        with open(BASE / "log" / "app.jsonl", "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            lines = f.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            r = json.loads(line)
            d = r.get("data") or {}
            out.append((r.get("ts", "")[11:19], r.get("level", ""), r.get("component", ""), r.get("event", ""),
                        " ".join(f"{k}={v}" for k, v in d.items())[:160]))
        except Exception:
            continue
    return out


CSS = """body{font-family:system-ui,sans-serif;background:#0f1620;color:#e7edf4;margin:0;padding:1rem;max-width:900px;margin:auto}
h1{font-size:1.3rem}h2{font-size:1.05rem;margin-top:1.4rem}a{color:#38bdf8}table{border-collapse:collapse;width:100%;font-size:.9rem}
td,th{border-bottom:1px solid #2c3a4d;padding:.3rem .4rem;text-align:left}.ok{color:#34d399}.bad{color:#f87171}.muted{color:#9aaabc}
button{font:inherit;padding:.5rem .9rem;margin:.2rem .3rem .2rem 0;border-radius:8px;border:1px solid #2c3a4d;background:#1d2a3a;color:#e7edf4;cursor:pointer}
button.danger{border-color:#f87171;color:#f87171}input{font:inherit;padding:.4rem;border-radius:6px;border:1px solid #2c3a4d;background:#1d2a3a;color:#e7edf4}
.note{background:#1d2a3a;border-left:4px solid #38bdf8;padding:.5rem .7rem;margin:.6rem 0}code{font-size:.85rem}"""


def page(msg=""):
    st = SV.status()
    m = meminfo()
    try:
        load = open("/proc/loadavg").read().split()[:3]
        up = float(open("/proc/uptime").read().split()[0])
    except OSError:
        load, up = ["?"] * 3, 0
    rows = "".join(
        f"<tr><td><b>{html.escape(u['label'])}</b> <span class='muted'>{u['unit']}</span></td>"
        f"<td class='{'ok' if u['active'] == 'active' else 'bad'}'>{html.escape(str(u['active']))} ({html.escape(str(u['sub']))})</td>"
        f"<td>{u['memory_mb'] if u['memory_mb'] is not None else '–'} MB</td><td>{u['restarts'] if u['restarts'] is not None else '–'}×</td>"
        f"<td><form method=post action='/restart'><input type=hidden name=unit value='{u['unit']}'>"
        f"<button onclick=\"return confirm('{html.escape(u['label'])} neu starten?')\">Neu starten</button></form></td></tr>"
        for u in st["units"] if u["loaded"])
    logs = "".join(f"<tr><td>{html.escape(t)}</td><td class='{'bad' if lv in ('ERROR', 'WARN') else 'muted'}'>{lv}</td>"
                   f"<td>{html.escape(c)}</td><td>{html.escape(e)}</td><td class='muted'>{html.escape(d)}</td></tr>"
                   for t, lv, c, e, d in last_log())
    host = "{host}"
    sudo = "" if st["sudo"] else ("<div class='note bad'>Neu starten ist noch nicht freigegeben (sudo-Regel fehlt). "
                                   "Update erneut einspielen.</div>")
    return f"""<!doctype html><html lang=de><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=20><title>HamScanPi – Notfall</title><style>{CSS}</style>
<h1>HamScanPi – Notfall-Seite</h1>
<p class=muted>Läuft unabhängig vom Dashboard. <a href="http://{host}:8080/" id=dash>Zum Dashboard</a></p>
{f"<div class=note>{html.escape(msg)}</div>" if msg else ""}{sudo}
<h2>Dienste</h2><table><tr><th>Dienst</th><th>Zustand</th><th>Speicher</th><th>Neustarts</th><th></th></tr>{rows}</table>
<h2>Pi</h2><p>Speicher verfügbar <b>{m.get('MemAvailable', '?')} MB</b> von {m.get('MemTotal', '?')} MB ·
Swap belegt {m.get('SwapTotal', 0) - m.get('SwapFree', 0)} MB · Last {' '.join(load)} · läuft seit {up / 3600:.1f} h</p>
<form method=post action='/reboot'><button class=danger onclick="return confirm('Den Raspberry Pi wirklich neu starten? Laufende Scans und Aufnahmen werden abgebrochen.')">Pi neu starten</button></form>
<form method=get action='/diagnose'><button>Diagnose herunterladen</button></form>
<h2>Protokoll (neueste zuerst)</h2><table>{logs or "<tr><td class=muted>leer</td></tr>"}</table>
<script>document.getElementById('dash').href = location.protocol + '//' + location.hostname + ':8080/';</script>
</html>"""


LOGIN = """<!doctype html><html lang=de><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>HamScanPi – Notfall</title><style>{css}</style><h1>HamScanPi – Notfall-Seite</h1>{msg}
<form method=post action='/login'><p>Passwort des Dashboards: <input type=password name=password autofocus>
<button>Anmelden</button></p></form></html>"""


class H(BaseHTTPRequestHandler):
    server_version = "HamScanPi-Rescue"

    def log_message(self, fmt, *args):
        pass

    def ip(self):
        return self.client_address[0]

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def redirect(self, msg=""):
        self.send_response(303)
        self.send_header("Location", "/" + (("?msg=" + urllib.parse.quote(msg)) if msg else ""))
        self.end_headers()

    def ok(self):
        return AUTH.allowed(self.ip(), self.headers.get("Cookie"))

    def same_origin(self):
        o = self.headers.get("Origin") or self.headers.get("Referer") or ""
        return not o or urllib.parse.urlparse(o).hostname == (self.headers.get("Host") or "").split(":")[0]

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if not self.ok():
            return self.send(200, LOGIN.format(css=CSS, msg=""))
        if u.path == "/diagnose":
            import diag
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Disposition", f'attachment; filename="{diag.filename()}"')
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            diag.write(self.wfile, stream=True)
            log("INFO", "diagnose_downloaded", ip=self.ip())
            return
        msg = urllib.parse.parse_qs(u.query).get("msg", [""])[0]
        return self.send(200, page(msg).replace("{host}", (self.headers.get("Host") or "").split(":")[0]))

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(min(n, 10000)).decode("utf-8", "replace"))
        if u.path == "/login":
            if AUTH.login_attempt(self.ip(), form.get("password", [""])[0]):
                tok, age = AUTH.new_session()
                self.send_response(303)
                self.send_header("Set-Cookie", f"{AUTH.COOKIE}={tok}; Max-Age={age}; Path=/; HttpOnly; SameSite=Lax")
                self.send_header("Location", "/")
                self.end_headers()
                log("INFO", "login", ip=self.ip())
                return
            log("WARN", "login_failed", ip=self.ip())
            return self.send(200, LOGIN.format(css=CSS, msg="<p class=bad>Falsches Passwort.</p>"))
        if not self.ok() or not self.same_origin():
            return self.send(403, "nicht erlaubt", "text/plain; charset=utf-8")
        try:
            if u.path == "/restart":
                unit = form.get("unit", [""])[0]
                SV.restart(unit, log=lambda lv, comp, ev, **d: log(lv, ev, ip=self.ip(), **d))
                return self.redirect(f"{SV.UNITS.get(unit, unit)} wird neu gestartet – Seite lädt in 20 s neu.")
            if u.path == "/reboot":
                log("WARN", "reboot_requested", ip=self.ip())
                threading.Thread(target=SV.reboot, daemon=True).start()
                return self.send(200, f"<!doctype html><meta charset=utf-8><style>{CSS}</style><h1>Der Pi startet neu …</h1>"
                                      "<p>In etwa einer Minute ist er wieder da. Diese Seite lädt dann neu.</p>"
                                      "<script>setTimeout(()=>location.href='/',75000)</script>")
        except Exception as e:
            return self.redirect(f"Fehler: {e}")
        return self.send(404, "unbekannt", "text/plain; charset=utf-8")


def main():
    srv = _Server(("0.0.0.0", PORT), H)
    srv.daemon_threads = True
    log("INFO", "start", port=PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
