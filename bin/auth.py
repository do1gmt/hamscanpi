#!/usr/bin/env python3
"""
Zugangsschutz für das Dashboard (optional).

  config/auth.json   {"password_hash": "pbkdf2_sha256$<iter>$<salt>$<hash>", "secret": "<hex>",
                      "trusted_networks": ["192.168.178.0/24"]}
                     – nie im Repository (.gitignore), Rechte 600

Ohne Passwort ist das Dashboard offen wie bisher. Mit Passwort braucht jeder Aufruf außer der
Anmeldeseite eine Sitzung (Cookie, 30 Tage, HttpOnly, SameSite=Strict). Aus „vertrauenswürdigen
Netzen“ (z. B. dem eigenen Heimnetz) geht es auch ohne Anmeldung. Nach 5 Fehlversuchen von einer
Adresse ist dort 5 Minuten lang keine Anmeldung möglich. Ein neues Passwort meldet alle ab.

Kommandozeile (z. B. wenn das Passwort vergessen ist):
  python3 bin/auth.py set        Passwort setzen oder ändern
  python3 bin/auth.py off        Schutz ausschalten
  python3 bin/auth.py status
"""
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
AUTH_FILE = BASE / "config" / "auth.json"
COOKIE = "hsp_session"
SESSION_DAYS = 30
ITERATIONS = 150_000
MAX_FAILS, LOCK_S = 5, 300
_fails = {}                     # ip -> [anzahl, gesperrt_bis]
_lock = threading.Lock()


def load():
    try:
        d = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save(d):
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = AUTH_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, AUTH_FILE)


def enabled():
    return bool(load().get("password_hash"))


def _hash(password, salt=None, iterations=ITERATIONS):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${h}"


def check_password(password):
    stored = load().get("password_hash") or ""
    try:
        algo, it, salt, h = stored.split("$")
        return algo == "pbkdf2_sha256" and hmac.compare_digest(_hash(password, salt, int(it)), stored)
    except ValueError:
        return False


def set_password(password, trusted=None):
    if len(password) < 8:
        raise ValueError("Passwort zu kurz (mindestens 8 Zeichen)")
    d = load()
    d["password_hash"] = _hash(password)
    d["secret"] = secrets.token_hex(32)          # neues Geheimnis: alle Sitzungen ungültig
    if trusted is not None:
        d["trusted_networks"] = trusted
    save(d)


def disable():
    d = load()
    d.pop("password_hash", None)
    d["secret"] = secrets.token_hex(32)
    save(d)


def parse_networks(text):
    """'192.168.178.0/24, 10.0.0.0/8' -> Liste geprüfter Netze (Fehler bei ungültigem Eintrag)."""
    out = []
    for x in [x.strip() for x in str(text or "").replace(";", ",").split(",") if x.strip()]:
        try:
            out.append(str(ipaddress.ip_network(x, strict=False)))
        except ValueError:
            raise ValueError(f"Ungültiges Netz: {x} (Beispiel 192.168.178.0/24)")
    return out


def set_trusted(networks):
    d = load()
    d["trusted_networks"] = networks
    save(d)


def trusted(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if a.is_loopback:
        return True                              # der Pi selbst (Skripte, curl)
    for n in load().get("trusted_networks") or []:
        try:
            if a in ipaddress.ip_network(n, strict=False):
                return True
        except ValueError:
            continue
    return False


def _sign(payload, secret):
    return hmac.new(bytes.fromhex(secret), payload.encode(), hashlib.sha256).hexdigest()


def new_session():
    d = load()
    if not d.get("secret"):
        d["secret"] = secrets.token_hex(32)
        save(d)
    exp = int(time.time()) + SESSION_DAYS * 86400
    payload = f"{exp}.{secrets.token_hex(8)}"
    return f"{payload}.{_sign(payload, d['secret'])}", SESSION_DAYS * 86400


def valid_session(cookie_header):
    d = load()
    if not d.get("secret") or not cookie_header:
        return False
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k != COOKIE:
            continue
        try:
            exp, nonce, sig = v.split(".")
            if int(exp) < time.time():
                return False
            return hmac.compare_digest(sig, _sign(f"{exp}.{nonce}", d["secret"]))
        except ValueError:
            return False
    return False


def allowed(ip, cookie_header):
    return (not enabled()) or trusted(ip) or valid_session(cookie_header)


def login_blocked(ip):
    with _lock:
        n, until = _fails.get(ip, (0, 0))
        return until > time.time()


def login_attempt(ip, password):
    """True bei Erfolg. Nach MAX_FAILS Fehlversuchen ist die Adresse LOCK_S Sekunden gesperrt."""
    with _lock:
        n, until = _fails.get(ip, (0, 0))
        if until > time.time():
            return False
    ok = check_password(password)
    with _lock:
        if ok:
            _fails.pop(ip, None)
        else:
            n = n + 1
            _fails[ip] = (0, time.time() + LOCK_S) if n >= MAX_FAILS else (n, 0)
    return ok


def status():
    d = load()
    return {"enabled": bool(d.get("password_hash")), "trusted_networks": d.get("trusted_networks") or []}


if __name__ == "__main__":
    import getpass
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "set":
        p1 = getpass.getpass("Neues Passwort (mind. 8 Zeichen): ")
        p2 = getpass.getpass("Wiederholen: ")
        if p1 != p2:
            sys.exit("Die Passwörter stimmen nicht überein.")
        set_password(p1)
        print("Passwort gesetzt – alle bisherigen Anmeldungen sind abgemeldet.")
    elif cmd == "off":
        disable()
        print("Zugangsschutz ausgeschaltet – das Dashboard ist ohne Anmeldung erreichbar.")
    else:
        s = status()
        print("Zugangsschutz:", "aktiv" if s["enabled"] else "aus")
        print("Ohne Anmeldung aus:", ", ".join(s["trusted_networks"]) or "–", "(und vom Pi selbst)")
