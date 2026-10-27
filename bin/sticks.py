#!/usr/bin/env python3
"""
SDR-Sticks erkennen, unterscheiden und einbinden (Reiter System → Geräte).

Erkennung: aus /sys/bus/usb/devices (Hersteller, Produkt, Seriennummer, Port) – ohne den Stick zu öffnen,
also auch während eines Scans. Den Tuner-Typ fragt der Zeitplaner einmal pro Stick im Leerlauf ab
(rtl_test unter der Scan-Sperre).

Register: data/sticks.json (bleibt bei Updates erhalten). Je Stick: Rolle, ppm, Verstärkung, Antennen-Notiz.
  Rollen: "main" = Haupt-Stick (VHF/UHF, Satelliten, Live) · "spare" = Reserve · "ignore" = nicht benutzen ·
          None = neu, noch nicht festgelegt (wird nie benutzt, bis du entscheidest).
  Der allererste RTL-Stick wird automatisch Haupt-Stick (bestehende Installation: der NESDR).

Benutzt wird nur der Haupt-Stick, angesprochen über seine Seriennummer (rtl_sdr -d <Seriennummer>), damit die
USB-Reihenfolge keine Rolle spielt. Haben zwei angesteckte Sticks dieselbe Seriennummer (bei Billigsticks
oft 00000001), geht das nicht – dann über die Reihenfolge, mit Hinweis, und auf Wunsch schreibt
rtl_eeprom eine eigene Nummer.

Zum Testen ohne Hardware: HSP_SYSFS_USB=<Verzeichnis> zeigt auf einen nachgebauten USB-Baum.
"""
import fcntl
import json
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
REG = BASE / "data" / "sticks.json"
REG_LOCK = BASE / "data" / "sticks.lock"
SCAN_LOCK = BASE / "data" / "scan.lock"
RTL_IDS = {("0bda", "2838"), ("0bda", "2832")}
# erkannt, aber (noch) nicht unterstützt – damit ein angesteckter Stick nicht einfach „unsichtbar“ bleibt
OTHER_IDS = {
    ("1d50", "60a1"): "Airspy R2 / Mini", ("03eb", "800c"): "Airspy HF+", ("1d50", "6089"): "HackRF One",
    ("1df7", "2500"): "SDRplay RSP1", ("1df7", "3000"): "SDRplay RSP1A", ("1df7", "3010"): "SDRplay RSP2",
    ("1df7", "3020"): "SDRplay RSPduo", ("1df7", "3030"): "SDRplay RSPdx", ("1df7", "3050"): "SDRplay RSP1B",
    ("04d8", "fb56"): "FUNcube Dongle Pro", ("04d8", "fb31"): "FUNcube Dongle Pro+",
}
GENERIC_SERIAL = re.compile(r"^0{4,}\d{0,4}$")       # 00000001, 00000000 … – ab Werk bei vielen Sticks gleich
ROLES = (None, "main", "spare", "ignore")
LAST_SEEN_WRITE_S = 3600


def sysfs():
    return Path(os.environ.get("HSP_SYSFS_USB") or "/sys/bus/usb/devices")


def _rd(p):
    try:
        return Path(p).read_text().strip()
    except Exception:
        return ""


# ---------------------------------------------------------------- Erkennung
def usb_scan():
    """Angesteckte SDR-Geräte: [{port, vid, pid, kind, manufacturer, product, serial, node}]."""
    out = []
    for d in sorted(sysfs().glob("*")):
        if ":" in d.name:                                    # Schnittstellen, keine Geräte
            continue
        vid, pid = _rd(d / "idVendor").lower(), _rd(d / "idProduct").lower()
        if not vid:
            continue
        if (vid, pid) in RTL_IDS:
            kind = "rtl"
        elif (vid, pid) in OTHER_IDS:
            kind = "other"
        else:
            continue
        bus, dev = _rd(d / "busnum"), _rd(d / "devnum")
        out.append({"port": d.name, "vid": vid, "pid": pid, "kind": kind,
                    "manufacturer": _rd(d / "manufacturer"), "product": _rd(d / "product") or OTHER_IDS.get((vid, pid), ""),
                    "serial": _rd(d / "serial"),
                    "node": f"/dev/bus/usb/{int(bus):03d}/{int(dev):03d}" if bus.isdigit() and dev.isdigit() else None})
    return out


def key_of(dev):
    return f"sn:{dev['vid']}:{dev['pid']}:{dev['serial']}" if dev["serial"] else f"port:{dev['port']}"


def _as_index(serial):
    """Wie librtlsdr (strtol, Basis 0) die Seriennummer als Gerätenummer lesen würde – oder None.
    rtl_sdr -d prüft zuerst auf eine Nummer: „00000001“ (oktal 1) öffnet bei zwei Sticks Gerät 1."""
    if re.fullmatch(r"0[xX][0-9a-fA-F]+", serial):
        return int(serial, 16)
    if re.fullmatch(r"0[0-7]*", serial):
        return int(serial, 8)
    if re.fullmatch(r"[1-9][0-9]*", serial):
        return int(serial)
    return None


def _by_serial_ok(dev, rtl):
    """Lässt sich dieser Stick sicher über seine Seriennummer öffnen?"""
    sn = dev["serial"]
    if not sn or sum(1 for x in rtl if x["serial"] == sn) != 1:
        return False
    n = _as_index(sn)
    return n is None or n >= len(rtl) + 4          # sonst nähme rtl_sdr sie als Gerätenummer


def generic_serial(serial):
    return not serial or bool(GENERIC_SERIAL.match(serial))


# ---------------------------------------------------------------- Register
@contextmanager
def _locked():
    REG_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(REG_LOCK, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load():
    try:
        d = json.loads(REG.read_text(encoding="utf-8"))
        if isinstance(d.get("sticks"), dict):
            return d
    except Exception:
        pass
    return {"sticks": {}}


def _save(reg):
    tmp = REG.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, REG)


def _label(r):
    """Hersteller + Produkt, ohne doppelte Wörter („RTL-SDR Blog“ + „Blog V4“ → „RTL-SDR Blog V4“)."""
    p = r.get("product") or "Stick"
    m = (r.get("manufacturer") or "").strip()
    if not m or m.lower() in p.lower():
        return p
    seen = {w.lower() for w in m.split()}
    rest = " ".join(w for w in p.split() if w.lower() not in seen)
    return f"{m} {rest}".strip()


def sync(now=None, notify=True):
    """Angesteckte Geräte mit dem Register abgleichen. Neue eintragen (und melden), last_seen pflegen.
    Schreibt nur bei Änderungen. Rückgabe: (register, angesteckte Geräte, neu eingetragene Schlüssel)."""
    now = now or time.time()
    present = usb_scan()
    new = []
    with _locked():
        reg = load()
        st = reg["sticks"]
        changed = False
        for dev in present:
            k = key_of(dev)
            r = st.get(k)
            if r is None:
                rtl_known = any(x.get("kind") == "rtl" for x in st.values())
                role = "main" if dev["kind"] == "rtl" and not rtl_known else None
                r = st[k] = {"key": k, "kind": dev["kind"], "vid": dev["vid"], "pid": dev["pid"],
                             "manufacturer": dev["manufacturer"], "product": dev["product"], "serial": dev["serial"],
                             "tuner": None, "tuner_tries": 0, "role": role, "auto": role == "main",
                             "ppm": None, "gain": None, "antenna": "", "first_seen": int(now), "last_seen": int(now)}
                new.append(k)
                changed = True
            elif now - r.get("last_seen", 0) > LAST_SEEN_WRITE_S:
                r["last_seen"] = int(now)
                changed = True
            if dev["kind"] == "rtl" and r.get("port") != dev["port"]:
                r["port"] = dev["port"]
                changed = True
        if changed:
            _save(reg)
    if new and notify:
        for k in new:
            _announce(reg["sticks"][k])
    return reg, present, new


def _announce(r):
    """Neuer Stick: Protokoll und (falls eingerichtet) Mail/Push – im Hintergrund, der Aufrufer wartet nicht."""
    label = _label(r)
    sn = r.get("serial") or "ohne"
    try:
        from applog import log
        if r["kind"] == "other":
            log("INFO", "devices", "stick_unsupported", stick=label, serial=sn)
        elif r.get("auto"):
            log("INFO", "devices", "stick_main_auto", stick=label, serial=sn)
        else:
            log("WARN", "devices", "stick_new", stick=label, serial=sn)
    except Exception:
        pass
    if r.get("auto"):
        return                                               # erster Stick der Installation: kein Aufheben machen
    if r["kind"] == "other":
        title, text = f"SDR erkannt: {label}", (f"{label} (Seriennummer {sn}) steckt am Pi. HamScanPi unterstützt "
                                                "dieses Gerät nicht und benutzt es nicht.")
    else:
        title, text = f"Neuer SDR-Stick: {label}", (f"{label} (Seriennummer {sn}) steckt am Pi. HamScanPi benutzt ihn "
                                                    "erst, wenn du unter System → Geräte festlegst, was er sein soll "
                                                    "(Haupt-Stick, Reserve oder ignorieren).")

    def send():
        try:
            import notify as N
            c = N.cfg()
            if c.get("enabled") or N.push_on(c):
                N.send_all(title, text, c)
        except Exception:
            pass
    threading.Thread(target=send, daemon=True).start()


def update(key, **fields):
    """Rolle, ppm, Verstärkung, Antenne eines Sticks ändern. Neuer Haupt-Stick: der alte wird Reserve."""
    with _locked():
        reg = load()
        r = reg["sticks"].get(key)
        if r is None:
            raise ValueError("Stick nicht im Register")
        if "role" in fields:
            role = fields["role"] or None
            if role not in ROLES:
                raise ValueError("unbekannte Rolle")
            if role == "main" and r["kind"] != "rtl":
                raise ValueError("Dieses Gerät unterstützt HamScanPi nicht")
            if role == "main":
                for o in reg["sticks"].values():
                    if o is not r and o.get("role") == "main":
                        o["role"] = "spare"
            r["role"], r["auto"] = role, False
        if "ppm" in fields:
            v = fields["ppm"]
            if v in (None, ""):
                r["ppm"] = None
            else:
                v = int(round(float(v)))
                if not -200 <= v <= 200:
                    raise ValueError("ppm zwischen -200 und 200")
                r["ppm"] = v
        if "gain" in fields:
            v = fields["gain"]
            if v in (None, ""):
                r["gain"] = None
            else:
                v = float(v)
                if not 0 <= v <= 60:
                    raise ValueError("Verstärkung zwischen 0 und 60 dB (0 = automatisch)")
                r["gain"] = v
        if "antenna" in fields:
            r["antenna"] = str(fields["antenna"] or "")[:60]
        # Richtantenne: Azimut, Öffnungswinkel, Frequenzbereich (MHz) – leer = Rundstrahler
        def num(k, lo, hi, what):
            v = fields[k]
            if v in (None, ""):
                return None
            v = float(str(v).replace(",", "."))
            if not lo <= v <= hi:
                raise ValueError(f"{what} zwischen {lo:g} und {hi:g}")
            return v
        for k, lo, hi, what in (("ant_az", 0, 360, "Richtung (°)"), ("ant_bw", 10, 360, "Öffnungswinkel (°)"),
                                ("ant_el", 0, 90, "Erhebung (°)"),
                                ("ant_fmin", 1, 6000, "Frequenz von (MHz)"), ("ant_fmax", 1, 6000, "Frequenz bis (MHz)")):
            if k in fields:
                r[k] = num(k, lo, hi, what)
        if r.get("ant_fmin") and r.get("ant_fmax") and r["ant_fmin"] > r["ant_fmax"]:
            r["ant_fmin"], r["ant_fmax"] = r["ant_fmax"], r["ant_fmin"]
        _save(reg)
        return r


def forget(key):
    present = {key_of(d) for d in usb_scan()}
    if key in present:
        raise ValueError("Der Stick steckt noch – erst abstecken, dann vergessen")
    with _locked():
        reg = load()
        if reg["sticks"].pop(key, None) is None:
            raise ValueError("Stick nicht im Register")
        _save(reg)


# ---------------------------------------------------------------- Haupt-Stick für die Programme
class NoMainStick(RuntimeError):
    pass


def _rtl_present(present=None):
    return [d for d in (present if present is not None else usb_scan()) if d["kind"] == "rtl"]


def main_device(present=None, reg=None):
    """(Register-Eintrag, Gerät, Index unter den angesteckten RTL-Sticks) des Haupt-Sticks oder None."""
    rtl = _rtl_present(present)
    reg = reg or load()
    for i, d in enumerate(rtl):
        r = reg["sticks"].get(key_of(d))
        if r and r.get("role") == "main":
            return r, d, i
    return None


def rtl_params(rtl_cfg=None):
    """Wie rtl_sdr/rtl_fm den Haupt-Stick öffnen: {"d": "-d"-Argument, "ppm", "gain", "node", "label", "how"}.
    Ohne angesteckten RTL-Stick (oder ohne USB-Baum, Testbetrieb) die alte Vorgabe device_index aus der
    Konfiguration – rtl_sdr meldet dann selbst „kein Stick“. Steckt nur kein Haupt-Stick: NoMainStick."""
    rtl_cfg = rtl_cfg or {}
    base = {"d": str(int(rtl_cfg.get("device_index", 0))), "ppm": int(rtl_cfg.get("ppm", 0) or 0),
            "gain": rtl_cfg.get("gain", 0), "node": None, "label": None, "how": "index"}
    present = usb_scan()
    rtl = _rtl_present(present)
    if not rtl:
        return base
    reg, present, _ = sync(notify=True)
    hit = main_device(present, reg)
    if hit is None:
        raise NoMainStick("Kein Haupt-Stick angesteckt – unter System → Geräte einen Stick als Haupt-Stick wählen")
    r, d, i = hit
    if _by_serial_ok(d, rtl):
        base.update(d=d["serial"], how="serial")
    else:
        base.update(d=str(i), how="order")                 # gleiche Seriennummern: nur über die Reihenfolge
    if r.get("ppm") is not None:
        base["ppm"] = int(r["ppm"])
    if r.get("gain") is not None:
        base["gain"] = r["gain"]
    base.update(node=d["node"], label=_label(r), antenna=(r.get("antenna") or "").strip() or None)
    return base


# ---------------------------------------------------------------- Tuner abfragen (Zeitplaner, Leerlauf)
def probe_tuners(max_tries=3):
    """Für angesteckte RTL-Sticks ohne bekannten Tuner rtl_test kurz laufen lassen – nur, wenn die Scan-Sperre
    frei ist (flock -n: ein Scan oder Überflug hat immer Vorrang). Höchstens ein Stick pro Aufruf."""
    if not shutil.which("rtl_test"):
        return None
    reg, present, _ = sync(notify=False)
    rtl = _rtl_present(present)
    for i, d in enumerate(rtl):
        r = reg["sticks"].get(key_of(d))
        if not r or r.get("tuner") or r.get("tuner_tries", 0) >= max_tries:
            continue
        arg = d["serial"] if _by_serial_ok(d, rtl) else str(i)
        tuner = None
        try:
            p = subprocess.run(["/usr/bin/flock", "-n", str(SCAN_LOCK), "env", "LC_ALL=C", "timeout", "4",
                                "rtl_test", "-d", arg, "-t"], capture_output=True, text=True, timeout=8)
            if p.returncode == 1 and not (p.stdout + p.stderr).strip():
                return None                                   # Sperre belegt: später
            m = re.search(r"Found (.+?) tuner", p.stdout + p.stderr)
            tuner = m.group(1).strip() if m else None
        except Exception:
            pass
        with _locked():
            reg = load()
            r = reg["sticks"].get(key_of(d))
            if r:
                r["tuner_tries"] = r.get("tuner_tries", 0) + 1
                if tuner:
                    r["tuner"] = tuner
                _save(reg)
        return tuner
    return None


# ---------------------------------------------------------------- eigene Seriennummer schreiben
def write_serial(key, serial):
    """rtl_eeprom -s: nur, wenn genau dieser eine RTL-Stick steckt (sonst ist unklar, welcher beschrieben wird).
    Einstellungen des Eintrags gehen auf die neue Nummer über; wirksam nach Ab- und Anstecken."""
    serial = str(serial or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,16}", serial):
        raise ValueError("Seriennummer: 4–16 Zeichen, nur Buchstaben, Ziffern, - und _")
    if not shutil.which("rtl_eeprom"):
        raise ValueError("rtl_eeprom fehlt (Paket rtl-sdr)")
    rtl = _rtl_present()
    if len(rtl) != 1 or key_of(rtl[0]) != key:
        raise ValueError("Bitte nur diesen einen Stick angesteckt lassen (alle anderen RTL-Sticks abziehen), "
                         "sonst ist nicht sicher, welcher beschrieben wird")
    if any(r.get("serial") == serial for r in load()["sticks"].values()):
        raise ValueError("Diese Seriennummer ist schon vergeben")
    p = subprocess.run(["/usr/bin/flock", "-n", str(SCAN_LOCK), "env", "LC_ALL=C", "timeout", "20",
                        "rtl_eeprom", "-d", "0", "-s", serial], input="y\n", capture_output=True, text=True, timeout=30)
    out = (p.stdout + p.stderr).strip()
    if p.returncode != 0 or "error" in out.lower():
        raise ValueError("rtl_eeprom fehlgeschlagen" + (f": {out.splitlines()[-1]}" if out else " (Stick belegt?)"))
    with _locked():
        reg = load()
        old = reg["sticks"].get(key)
        if old:
            dev = dict(rtl[0], serial=serial)
            nk = key_of(dev)
            reg["sticks"][nk] = dict(old, key=nk, serial=serial, first_seen=int(time.time()), last_seen=int(time.time()))
            old["role"] = "ignore" if old.get("role") == "main" else old.get("role")   # alte Nummer nicht mehr benutzen
            old["note"] = f"neue Seriennummer {serial} geschrieben"
            _save(reg)
    try:
        from applog import log
        log("INFO", "devices", "stick_serial_written", serial=serial)
    except Exception:
        pass
    return serial


# ---------------------------------------------------------------- Anzeige
def overview():
    """Für den Reiter System → Geräte."""
    reg, present, _ = sync()
    pk = {key_of(d): d for d in present}
    rtl = _rtl_present(present)
    out = []
    for k, r in reg["sticks"].items():
        d = pk.get(k)
        dup = bool(d and d["kind"] == "rtl" and not _by_serial_ok(d, rtl))
        out.append(dict(r, label=_label(r), present=d is not None, port=(d or {}).get("port") or r.get("port"),
                        duplicate=dup, generic=r["kind"] == "rtl" and generic_serial(r.get("serial")),
                        v4=r["kind"] == "rtl" and ("v4" in (r.get("product") or "").lower() or r.get("tuner") == "Rafael Micro R828D")))
    out.sort(key=lambda r: (not r["present"], {"main": 0, None: 1, "spare": 2, "ignore": 3}.get(r.get("role"), 4), r["label"]))
    main = main_device(present, reg)
    return {"sticks": out, "main_present": main is not None, "rtl_present": len(rtl),
            "pending": sum(1 for r in out if r["present"] and r["kind"] == "rtl" and r.get("role") is None),
            "eeprom": bool(shutil.which("rtl_eeprom"))}
