#!/usr/bin/env python3
"""Sicherung und Wiederherstellung von Hesperos DX – Einstellungen, Datenbestände, Aufnahmen.

Umfang
  settings  config/ (ohne Vorlagen), Geräteregister, Satelliten-Statistik und -Ergebnisse, kleine Zustände
  data      + Datenbanken (Treffer, Stationen, UKW-Sender, Störlinien, Systemverlauf)
  all       + recordings/ und reports/ (Ton, I/Q, Wasserfälle, SSTV-Bilder – kann groß sein)

Die Datenbanken werden über die Sicherungsfunktion von SQLite kopiert: stimmig, auch wenn gerade
geschrieben wird. Kurzlebiges (Sperren, PIDs, Status, Pegel) kommt nicht hinein.

Zugangsdaten (Mail-Passwort, Telegram-/ntfy-Token, Anwendungspasswort der Webseite, Zugangsschutz
config/auth.json) stehen seit 1.8.22 nie im Klartext in einer Sicherung. Auf Wunsch kommen sie verschlüsselt
mit (secrets.enc, AES-256 mit einem eigenen Passwort über openssl, PBKDF2) – auf eigenes Risiko: wer Datei und
Passwort hat, hat die Zugangsdaten. Das Passwort liegt nur auf dem Pi (config/.backup_key, nur für den Benutzer
lesbar), nie in einer Sicherung. Beim Einspielen ohne (richtiges) Passwort bleiben die Zugangsdaten des Pi erhalten.

Kommandozeile
  python3 bin/backup.py create [--scope settings|data|all] [--secrets none|encrypted] [-o datei.tar.gz]
  python3 bin/backup.py info datei.tar.gz
  python3 bin/backup.py restore datei.tar.gz [--yes] [--password]
Beim Wiederherstellen wird gewartet, bis kein Scan und kein Überflug läuft (Stick-Sperre); der bisherige
Stand von config/ und data/ wird vorher nach restore-previous/<zeit>/ gelegt. Danach die Dienste neu
starten: sudo systemctl restart hesperosdx-scheduler hesperosdx-web (im Dashboard geschieht das selbst).
"""
import argparse
import fcntl
import subprocess
import io
import json
import os
import shutil
import socket
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FORMAT = 1
SCOPES = ("settings", "data", "all")
SECRET_KEYS = ("smtp_pass", "tg_token", "ntfy_token")
DATA_SMALL = ("sticks.json", "sat_stats.json", "sat_results.json", "fm_gain.json", "pskreporter_state.json", "live_state.json", "antennas.json",
              "notify_state.json", "tle.json", "ui_prefs.json")
DATABASES = ("scanner.db", "sysmon.db")
SKIP_SUFFIX = (".lock", ".pid", ".tmp", ".trigger")
ALLOWED_TOP = ("config/", "data/", "recordings/", "reports/")
UPLOAD = BASE / "data" / "restore_upload.tar.gz"
KEY_FILE = BASE / "config" / ".backup_key"          # Passwort für verschlüsselte Zugangsdaten (nie in Sicherungen)
RESTORE_SECRETS = BASE / "data" / ".restore_secrets.json"   # entschlüsselt fürs Einspielen, danach gelöscht
MAGIC = b"HSPSECRETS1\n"
SECRET_MODES = ("none", "encrypted")
PENDING = BASE / "data" / "restore.pending"


def _version():
    try:
        return (BASE / "VERSION").read_text().strip()
    except OSError:
        return "?"


def _settings():
    try:
        return json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _config_files(secrets=False):
    """[(arcname, Pfad oder Bytes)] aus config/ – ohne Vorlagen und immer ohne Zugangsdaten im Klartext
    (secrets wird nicht mehr ausgewertet; Zugangsdaten kommen nur verschlüsselt als secrets.enc mit)."""
    out = []
    for p in sorted((BASE / "config").glob("*")):
        if not p.is_file() or p.name.endswith((".default.json", ".example.json")) or p.name.startswith("."):
            continue
        if p.name == "auth.json":
            continue
        if p.name == "web_settings.json":
            d = json.loads(p.read_text(encoding="utf-8"))
            n = d.get("notify") or {}
            for k in SECRET_KEYS:
                n.pop(k, None)
            d["notify"] = n
            if isinstance(d.get("publish"), dict):          # Anwendungspasswort der Webseite
                d["publish"].pop("app_password", None)
            out.append((f"config/{p.name}", json.dumps(d, indent=2, ensure_ascii=False).encode("utf-8")))
            continue
        out.append((f"config/{p.name}", p))
    return out


# ---------------------------------------------------------------- verschlüsselte Zugangsdaten
def key_set():
    try:
        return len(KEY_FILE.read_text(encoding="utf-8").strip()) >= 8
    except OSError:
        return False


def set_key(password):
    password = str(password or "")
    if len(password) < 8:
        raise ValueError("Passwort für die Zugangsdaten: mindestens 8 Zeichen")
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(password)
    os.chmod(KEY_FILE, 0o600)


def clear_key():
    KEY_FILE.unlink(missing_ok=True)


def _openssl(data, password, decrypt=False):
    exe = shutil.which("openssl")
    if not exe:
        raise ValueError("openssl fehlt – sudo apt install openssl")
    cmd = [exe, "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-md", "sha256", "-salt", "-pass", "env:HSP_BKPW"]
    if decrypt:
        cmd.append("-d")
    r = subprocess.run(cmd, input=data, capture_output=True, timeout=60, env=dict(os.environ, HSP_BKPW=password))
    if r.returncode != 0:
        raise ValueError("Passwort falsch oder Datei beschädigt" if decrypt else
                         "Verschlüsseln fehlgeschlagen: " + r.stderr.decode("utf-8", "replace")[-200:])
    return r.stdout


def current_secrets():
    """Zugangsdaten, die gerade auf dem Pi gelten: {"notify": {...}, "publish": {...}, "auth": "<auth.json>"}."""
    st = _settings()
    n = {k: v for k, v in (st.get("notify") or {}).items() if k in SECRET_KEYS and v}
    pub = st.get("publish") if isinstance(st.get("publish"), dict) else {}
    out = {"notify": n, "publish": {"app_password": pub["app_password"]} if pub.get("app_password") else {}}
    try:
        out["auth"] = (BASE / "config" / "auth.json").read_text(encoding="utf-8")
    except OSError:
        pass
    return out


def encrypt_secrets(password):
    return _openssl(MAGIC + json.dumps(current_secrets(), ensure_ascii=False).encode("utf-8"), password)


def decrypt_secrets(blob, password):
    data = _openssl(blob, password, decrypt=True)
    if not data.startswith(MAGIC):
        raise ValueError("Passwort falsch oder Datei beschädigt")
    return json.loads(data[len(MAGIC):].decode("utf-8"))


def read_secrets(path, password):
    """Verschlüsselte Zugangsdaten aus einer Sicherung lesen (ValueError bei falschem Passwort)."""
    with tarfile.open(path, "r:gz") as tar:
        try:
            m = tar.extractfile("secrets.enc")
        except KeyError:
            m = None
        if not m:
            raise ValueError("Diese Sicherung enthält keine verschlüsselten Zugangsdaten")
        return decrypt_secrets(m.read(), str(password or ""))


def apply_secrets(sec, only_missing=False):
    """Zugangsdaten in die Einstellungen (und auth.json) schreiben; only_missing: nur leere Felder füllen."""
    p = BASE / "config" / "web_settings.json"
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(st.get("notify"), dict):
        st["notify"] = {}
    n = st["notify"]
    changed = False
    for k, v in ((sec or {}).get("notify") or {}).items():
        if k in SECRET_KEYS and v and not (only_missing and n.get(k)):
            n[k] = v
            changed = True
    ap = ((sec or {}).get("publish") or {}).get("app_password")
    if ap:
        pub = st.setdefault("publish", {})
        if isinstance(pub, dict) and not (only_missing and pub.get("app_password")):
            pub["app_password"] = ap
            changed = True
    if changed:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    auth = (sec or {}).get("auth")
    ap_ = BASE / "config" / "auth.json"
    if auth and not (only_missing and ap_.exists()):
        tmp = ap_.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(auth)
        os.replace(tmp, ap_)


def secrets_mode(v):
    """Gespeicherte/übergebene Angabe vereinheitlichen: „encrypted“ oder „none“ (alte true/false → none)."""
    return "encrypted" if v == "encrypted" else "none"


def _walk(top):
    root = BASE / top
    if not root.is_dir():
        return []
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not d.startswith(".")]
        for f in fn:
            if f.startswith(".") or f.endswith(SKIP_SUFFIX):
                continue
            p = Path(dp) / f
            out.append((p.relative_to(BASE).as_posix(), p))
    return sorted(out)


def sizes():
    """Ungefähre Größe je Umfang in Bytes (für die Anzeige im Dashboard)."""
    s = sum(p.stat().st_size for _, p in _config_files(True) if isinstance(p, Path))
    s += sum((BASE / "data" / f).stat().st_size for f in DATA_SMALL if (BASE / "data" / f).exists())
    d = sum((BASE / "data" / f).stat().st_size for f in DATABASES if (BASE / "data" / f).exists())
    r = sum(p.stat().st_size for top in ("recordings", "reports") for _, p in _walk(top))
    return {"settings": s, "data": s + d, "all": s + d + r}


def _db_snapshot(name, tmpdir):
    """Stimmige Kopie einer SQLite-Datenbank über die Sicherungsfunktion (auch während geschrieben wird)."""
    src = BASE / "data" / name
    if not src.exists():
        return None
    dst = Path(tmpdir) / name
    a = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    b = sqlite3.connect(dst)
    try:
        a.backup(b)
    finally:
        b.close()
        a.close()
    return dst


def write(fileobj, scope="data", secrets="none", stream=False):
    """Sicherung als tar.gz in fileobj schreiben (stream=True: ohne Zurückspulen, für die direkte Ausgabe).
    secrets: „none“ oder „encrypted“ (braucht das Passwort unter System → Sicherung)."""
    if scope not in SCOPES:
        raise ValueError(f"Umfang: {', '.join(SCOPES)}")
    mode = secrets_mode(secrets)
    enc = None
    if mode == "encrypted":
        if not key_set():
            raise ValueError("Für verschlüsselte Zugangsdaten zuerst ein Passwort festlegen (System → Sicherung)")
        enc = encrypt_secrets(KEY_FILE.read_text(encoding="utf-8").strip())
    st = _settings()
    files = _config_files()
    files += [(f"data/{f}", BASE / "data" / f) for f in DATA_SMALL if (BASE / "data" / f).exists()]
    with tempfile.TemporaryDirectory(dir=BASE / "data") as td:
        if scope in ("data", "all"):
            for name in DATABASES:
                snap = _db_snapshot(name, td)
                if snap:
                    files.append((f"data/{name}", snap))
        if scope == "all":
            files += _walk("recordings") + _walk("reports")
        manifest = {"format": FORMAT, "app": "HamScanPi", "version": _version(),
                    "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": socket.gethostname(),
                    "call": st.get("my_call", ""), "locator": st.get("my_locator", ""), "scope": scope,
                    "secrets": mode, "files": len(files)}
        with tarfile.open(fileobj=fileobj, mode="w|gz" if stream else "w:gz") as tar:
            data = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
            ti = tarfile.TarInfo("manifest.json")
            ti.size, ti.mtime = len(data), int(time.time())
            tar.addfile(ti, io.BytesIO(data))
            if enc:
                ti = tarfile.TarInfo("secrets.enc")
                ti.size, ti.mtime = len(enc), int(time.time())
                tar.addfile(ti, io.BytesIO(enc))
            for arc, src in files:
                if isinstance(src, (bytes, bytearray)):
                    ti = tarfile.TarInfo(arc)
                    ti.size, ti.mtime = len(src), int(time.time())
                    tar.addfile(ti, io.BytesIO(src))
                else:
                    try:
                        tar.add(str(src), arcname=arc, recursive=False)
                    except FileNotFoundError:
                        pass                                   # zwischendurch gelöscht (Aufräumen)
    return manifest


def filename(scope):
    return f"hesperosdx-backup-{time.strftime('%Y%m%d-%H%M')}-{scope}.tar.gz"


def info(path):
    """Inhaltsliste einer Sicherung prüfen und zurückgeben (wirft ValueError bei fremden Dateien)."""
    try:
        with tarfile.open(path, "r:gz") as tar:
            m = tar.extractfile("manifest.json")
            man = json.loads(m.read().decode("utf-8")) if m else None
            names = tar.getnames()
    except (tarfile.TarError, KeyError, OSError, json.JSONDecodeError) as e:
        raise ValueError(f"keine Hesperos-Sicherung ({type(e).__name__})")
    if not man or man.get("app") not in ("HamScanPi", "Hesperos DX"):
        raise ValueError("keine Hesperos-Sicherung (Inhaltsliste fehlt)")
    man["size"] = Path(path).stat().st_size
    man["has_db"] = any(n in ("data/scanner.db", "data/sysmon.db") for n in names)
    man["has_recordings"] = any(n.startswith(("recordings/", "reports/")) for n in names)
    man["secrets_enc"] = "secrets.enc" in names
    # Zugangsdaten: verschlüsselt, im Klartext (Sicherungen bis 1.8.21) oder keine
    man["secrets"] = "encrypted" if man["secrets_enc"] else ("plain" if man.get("secrets") is True else "none")
    return man


def _safe_members(tar):
    for m in tar.getmembers():
        n = m.name
        if n in ("manifest.json", "secrets.enc"):
            continue
        if n.startswith("/") or ".." in Path(n).parts or not n.startswith(ALLOWED_TOP):
            raise ValueError(f"unerlaubter Pfad in der Sicherung: {n}")
        if not (m.isfile() or m.isdir()):
            raise ValueError(f"unerlaubter Eintrag in der Sicherung: {n}")
        yield m


def restore(path, wait=True, log=print, secrets=None):
    """Sicherung einspielen: unter der Stick-Sperre (kein Scan, kein Überflug), bisherigen Stand von config/
    und data/ nach restore-previous/<zeit>/ legen, dann auspacken. Aufnahmen werden ergänzt, nicht gelöscht.
    secrets: entschlüsselte Zugangsdaten aus der Sicherung (read_secrets) – ohne sie bleiben die des Pi erhalten."""
    man = info(path)
    keep = current_secrets()
    lock = open(BASE / "data" / "scan.lock", "a")
    if wait:
        log("warte, bis kein Scan und kein Überflug läuft …")
        fcntl.flock(lock, fcntl.LOCK_EX)
    else:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Scan oder Überflug läuft – später noch einmal")
    try:
        aside = BASE / "restore-previous" / time.strftime("%Y%m%d-%H%M%S")
        aside.mkdir(parents=True, exist_ok=True)
        for top in ("config", "data"):
            src = BASE / top
            if src.is_dir():
                shutil.copytree(src, aside / top, ignore=shutil.ignore_patterns(
                    "*.lock", "*.pid", "restore_upload.tar.gz", "tmp*"), dirs_exist_ok=True)
        log(f"bisheriger Stand gesichert: {aside.relative_to(BASE)}")
        n = 0
        with tarfile.open(path, "r:gz") as tar:
            for m in _safe_members(tar):
                dst = BASE / m.name
                if m.isdir():
                    dst.mkdir(parents=True, exist_ok=True)
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                tmp = dst.with_name(f".{dst.name}.restore")
                with tar.extractfile(m) as f, open(tmp, "wb") as out:
                    shutil.copyfileobj(f, out, 1 << 20)
                os.utime(tmp, (m.mtime, m.mtime))
                if dst.suffix == ".db":                      # alte WAL-/SHM-Dateien gehören nicht zur neuen Datenbank
                    for ext in ("-wal", "-shm"):
                        Path(str(dst) + ext).unlink(missing_ok=True)
                os.replace(tmp, dst)
                n += 1
        if secrets:
            apply_secrets(secrets)
            log("Zugangsdaten aus der Sicherung übernommen (entschlüsselt)")
        apply_secrets(keep, only_missing=True)               # was die Sicherung nicht hat, behält der Pi
        log(f"{n} Dateien eingespielt (Sicherung vom {man['created']}, Version {man['version']}, Umfang {man['scope']})")
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    try:                                                     # Konfiguration auf den aktuellen Stand bringen
        import subprocess
        subprocess.run([sys.executable, str(BASE / "bin" / "migrate_config.py")], cwd=BASE, timeout=120,
                       capture_output=True)
    except Exception:
        pass
    return man


def apply_pending(log=print):
    """Für den Scheduler: eine im Dashboard bestätigte Wiederherstellung ausführen (wenn der Stick frei ist).
    Rückgabe True, wenn eingespielt wurde (der Aufrufer startet dann neu)."""
    if not PENDING.exists():
        return False
    try:
        sec = json.loads(RESTORE_SECRETS.read_text(encoding="utf-8")) if RESTORE_SECRETS.exists() else None
    except (OSError, ValueError):
        sec = None
    try:
        restore(UPLOAD, wait=False, log=log, secrets=sec)
    except RuntimeError:
        return False                                         # Stick belegt: nächste Runde
    PENDING.unlink(missing_ok=True)
    UPLOAD.unlink(missing_ok=True)
    RESTORE_SECRETS.unlink(missing_ok=True)
    return True


def prepare_secrets(password):
    """Beim Bestätigen im Dashboard: Zugangsdaten der hochgeladenen Sicherung mit dem Passwort entschlüsseln
    (prüft es gleich) und bis zum Einspielen nur für den Benutzer lesbar ablegen. Ohne Passwort: nichts."""
    RESTORE_SECRETS.unlink(missing_ok=True)
    if not password:
        return False
    sec = read_secrets(UPLOAD, password)
    fd = os.open(RESTORE_SECRETS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(sec, f)
    return True


# ---------------------------------------------------------------- automatische Sicherung
AUTO_STATE = BASE / "data" / "backup_state.json"


def auto_cfg(settings=None):
    if settings is None:
        try:
            settings = json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    c = dict(settings.get("auto_backup") or {})
    c.setdefault("enabled", False)
    c.setdefault("interval", "daily")                 # daily | weekly (sonntags)
    c.setdefault("hour", 3)
    c.setdefault("dir", "")
    c.setdefault("scope", "data")
    c["secrets"] = secrets_mode(c.get("secrets"))       # seit 1.8.22: none | encrypted (nie mehr Klartext)
    c.setdefault("keep", 7)
    c.setdefault("target", {})
    t = c.get("target") or {}
    if t.get("kind") in ("usb", "nas"):                 # gewähltes Ziel (System → Sicherung): Ordner ergibt sich daraus
        import storage as STO
        c["dir"] = str(STO.host_dir(STO.USB_MP if t["kind"] == "usb" else STO.NAS_MP))
    return c


def auto_state():
    try:
        return json.loads(AUTO_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def candidates():
    """Mögliche Ziele: eingehängte USB-Sticks/Laufwerke unter /media und /mnt (beschreibbar)."""
    out = []
    for root in (Path("/media"), Path("/mnt")):
        try:
            for p in sorted(root.rglob("*")):
                if len(p.relative_to(root).parts) > 2 or not p.is_dir():
                    continue
                if os.path.ismount(p) and os.access(p, os.W_OK):
                    du = shutil.disk_usage(p)
                    out.append({"dir": str(p), "free_gb": round(du.free / 1e9, 1)})
        except (OSError, ValueError):
            continue
    return out


def _probe(p):
    """Ordner anlegen und eine kleine Datei schreiben und löschen. Rückgabe Fehlertext oder None."""
    try:
        p.mkdir(parents=True, exist_ok=True)
        f = p / ".hesperos-probe"
        f.write_bytes(b"ok")
        f.unlink()
        return None
    except OSError as e:
        import errno
        if e.errno == errno.EIO:
            return "Ein-/Ausgabefehler"
        if e.errno == errno.EROFS:
            return "nur noch lesbar – das Dateisystem hat Fehler gemeldet"
        if e.errno == errno.ENOSPC:
            return "voll"
        return e.strerror or str(e)


def check_dir(d, target=None):
    """Ziel prüfen: vorhanden, eingehängt (nicht die SD-Karte selbst), beschreibbar. Rückgabe Fehlertext oder None."""
    if not d:
        return "kein Sicherungsziel gewählt"
    p = Path(d)
    t = target or {}
    if t.get("kind") in ("usb", "nas"):
        import storage as STO
        mp = STO.USB_MP if t["kind"] == "usb" else STO.NAS_MP
        if not STO.mounted(mp):
            st = STO.read_state()
            if t["kind"] == "usb":
                return (f"USB-Stick „{t.get('label') or t.get('uuid')}“ ist ausgeworfen – abziehen und wieder anstecken"
                        if st.get("ejected") else f"USB-Stick „{t.get('label') or t.get('uuid')}“ steckt nicht"
                        + (f" ({st['usb_error']})" if st.get("usb_error") else ""))
            return f"Netzwerkfreigabe {t.get('share')} nicht erreichbar" + (f" ({st['nas_error']})" if st.get("nas_error") else "")
        err = _probe(p)
        if err and t["kind"] == "usb" and t.get("uuid"):
            # Ein-/Ausgabefehler: meist war der Stick kurz vom USB getrennt und die alte Einhängung ist tot –
            # einmal aus- und wieder einhängen, dann noch einmal versuchen
            import applog
            ok, out = STO.remount_usb(t["uuid"], applog.log, f"Sicherung: {err}")
            err = _probe(p) if ok else f"neu einhängen fehlgeschlagen: {STO._fail(out)}"
            if err:
                return (f"USB-Stick „{t.get('label') or t.get('uuid')}“ lässt sich nicht beschreiben ({err}). Abziehen und "
                        "wieder anstecken; hilft das nicht, den Stick am PC prüfen (Dateisystem reparieren) oder einen "
                        "anderen nehmen. Setzt er sich öfter zurück: über einen aktiven USB-Hub anschließen.")
        elif err:
            return f"Ordner {d} lässt sich nicht anlegen oder beschreiben ({err})"
        return None
    if not p.is_dir():
        return f"Zielordner {d} gibt es nicht (USB-Stick nicht eingesteckt oder Freigabe nicht eingehängt?)"
    if not os.access(p, os.W_OK):
        return f"Zielordner {d} ist nicht beschreibbar"
    try:
        if os.stat(p).st_dev == os.stat(BASE).st_dev:
            return f"{d} liegt auf demselben Laufwerk wie Hesperos DX – eine Sicherung dort hilft bei einem Kartenausfall nicht"
    except OSError:
        pass
    return None


def run_auto(cfg=None, log=print):
    """Sicherung in den Zielordner schreiben (erst .part, dann umbenennen), alte über „keep“ hinaus löschen."""
    cfg = cfg or auto_cfg()
    if (cfg.get("target") or {}).get("kind") in ("usb", "nas"):
        try:
            import storage as STO
            STO.ensure_now()
        except Exception:
            pass
    err = check_dir(cfg.get("dir"), cfg.get("target"))
    if err:
        raise RuntimeError(err)
    d = Path(cfg["dir"])
    scope = cfg.get("scope") if cfg.get("scope") in SCOPES else "data"
    out = d / filename(scope)
    tmp = out.with_suffix(".part")
    with open(tmp, "wb") as f:
        man = write(f, scope, secrets_mode(cfg.get("secrets")))
    os.replace(tmp, out)
    st = {"last": time.time(), "file": str(out), "size": out.stat().st_size, "files": man["files"], "error": None}
    keep = max(1, int(cfg.get("keep", 7)))
    # nach dem Namen (Datum und Uhrzeit stehen darin) – die gerade geschriebene Datei bleibt immer
    import storage as STO                             # alte (hamscanpi-backup-…) und neue Namen zusammen, nach Datum
    others = sorted((p for p in d.iterdir() if p != out and STO.BACKUP_RE.match(p.name)),
                    key=lambda p: STO.backup_stamp(p.name), reverse=True)
    old = others[max(0, keep - 1):]
    for p in old:
        try:
            p.unlink()
        except OSError:
            pass
    AUTO_STATE.write_text(json.dumps(st), encoding="utf-8")
    log(f"Sicherung: {out} ({out.stat().st_size / 1e6:.1f} MB, {man['files']} Dateien, {len(old)} alte gelöscht)")
    return st


def reset_error():
    """Alte Fehlermeldung vergessen (Sicherung ausgeschaltet, anderes Ziel gewählt): Hinweis und rote Zeile weg."""
    st = auto_state()
    if st.get("error") or st.get("tried_day"):
        st.pop("error", None); st.pop("tried_day", None)
        try:
            AUTO_STATE.write_text(json.dumps(st), encoding="utf-8")
        except OSError:
            pass
    try:
        import notices as NT
        NT.clear("auto_backup", "Einstellung geändert")
    except Exception:
        pass


def migrate_plain_secrets():
    """Bis 1.8.21 kamen die Zugangsdaten im Klartext in die automatische Sicherung („secrets“: true). Seit 1.8.22
    gibt es das nicht mehr: einmal auf „ohne“ umstellen und Bescheid sagen (verschlüsselt geht es auf Wunsch)."""
    p = BASE / "config" / "web_settings.json"
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    ab = st.get("auto_backup")
    if not isinstance(ab, dict) or ab.get("secrets") is not True:
        return False
    ab["secrets"] = "none"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    import notices as NT
    NT.event("backup_secrets", "info", "storage", "Sicherungen ohne Zugangsdaten im Klartext",
             "Die automatische Sicherung nimmt Mail-Passwort, Tokens und Zugangsschutz nicht mehr im Klartext mit.",
             fix="Wer sie trotzdem in der Sicherung haben will: System → Sicherung → „Zugangsdaten verschlüsselt“ "
                 "mit eigenem Passwort (auf eigenes Risiko).", notify=False)
    return True


def auto_tick(log=None):
    """Vom Scheduler einmal je Minute: fällig? (zur eingestellten Stunde, täglich bzw. sonntags, einmal)"""
    migrate_plain_secrets()
    cfg = auto_cfg()
    if not cfg.get("enabled"):
        if auto_state().get("error"):
            reset_error()                                      # ausgeschaltet: alter Fehler zählt nicht mehr
        return
    lt = time.localtime()
    if lt.tm_hour != int(cfg.get("hour", 3)) or (cfg.get("interval") == "weekly" and lt.tm_wday != 6):
        return
    st = auto_state()
    if st.get("last") and time.strftime("%Y-%m-%d", time.localtime(st["last"])) == time.strftime("%Y-%m-%d", lt):
        return
    if st.get("tried_day") == time.strftime("%Y-%m-%d", lt) and st.get("error"):
        return                                                 # heute schon gescheitert: nicht jede Minute neu
    import notices as NT
    try:
        res = run_auto(cfg, log=(lambda m: log("INFO", "backup", "auto_backup", detail=m)) if log else print)
        NT.clear("auto_backup", f"Sicherung {Path(res['file']).name}")
    except Exception as e:
        st.update(error=str(e), tried_day=time.strftime("%Y-%m-%d", lt))
        AUTO_STATE.write_text(json.dumps(st), encoding="utf-8")
        NT.raise_("auto_backup", "warn", "storage", "Automatische Sicherung fehlgeschlagen", str(e),
                  fix="USB-Stick einstecken bzw. Netzwerkfreigabe einhängen, Zielordner unter System → Sicherung prüfen.",
                  key="storage")


def main():
    ap = argparse.ArgumentParser(description="Hesperos DX sichern und wiederherstellen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="Sicherung erstellen")
    c.add_argument("--scope", choices=SCOPES, default="data")
    c.add_argument("--secrets", choices=SECRET_MODES, default="none",
                   help="Zugangsdaten verschlüsselt mitsichern (Passwort unter System → Sicherung festlegen)")
    c.add_argument("--no-secrets", action="store_true", help=argparse.SUPPRESS)       # bis 1.8.21
    c.add_argument("-o", "--output")
    i = sub.add_parser("info", help="Inhalt einer Sicherung anzeigen")
    i.add_argument("file")
    r = sub.add_parser("restore", help="Sicherung einspielen")
    r.add_argument("file")
    r.add_argument("--yes", action="store_true", help="ohne Rückfrage")
    r.add_argument("--password", action="store_true", help="nach dem Passwort der verschlüsselten Zugangsdaten fragen")
    a = ap.parse_args()
    if a.cmd == "create":
        out = Path(a.output or filename(a.scope))
        with open(out, "wb") as f:
            man = write(f, a.scope, "none" if a.no_secrets else a.secrets)
        print(f"{out}  ({out.stat().st_size / 1e6:.1f} MB, {man['files']} Dateien, Umfang {a.scope}, "
              f"{'Zugangsdaten verschlüsselt' if man['secrets'] == 'encrypted' else 'ohne Zugangsdaten'})")
    elif a.cmd == "info":
        print(json.dumps(info(a.file), indent=2, ensure_ascii=False))
    else:
        man = info(a.file)
        print(f"Sicherung vom {man['created']} · Version {man['version']} · {man.get('call') or '–'} "
              f"{man.get('locator') or ''} · Umfang {man['scope']} · {man['files']} Dateien")
        if not a.yes and input("Einspielen? Der bisherige Stand wird zur Seite gelegt. [j/N] ").strip().lower() not in ("j", "ja", "y", "yes"):
            sys.exit("abgebrochen")
        sec = None
        if a.password and man.get("secrets_enc"):
            import getpass
            sec = read_secrets(a.file, getpass.getpass("Passwort der Zugangsdaten: "))
        restore(a.file, secrets=sec)
        print("Fertig. Dienste neu starten: sudo systemctl restart hesperosdx-scheduler hesperosdx-web")


if __name__ == "__main__":
    main()
