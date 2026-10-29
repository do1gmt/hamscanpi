#!/usr/bin/env python3
"""Regelmäßige Prüfungen (vom Scheduler einmal je Minute) – Ergebnis sind Hinweise (notices.py).

  Neustart des Systems   Boot-Kennung geändert -> Ereignis „System neu gestartet“ (mit Laufzeit davor)
  Sticks                 angesteckt / abgezogen / gewechselt (Seriennummer), Haupt-Stick fehlt
  Speicherkarte          Kernel-Meldungen (mmc, EXT4-fs error, I/O error), Dateisystem nur lesbar,
                         Platz knapp, ungewöhnlich hohe Schreiblast auf einer SD-Karte
  Strom / Wärme          Unterspannung, Drosselung (vcgencmd), Temperatur
  System                 Prozess vom Kernel beendet (Speicher voll), Uhr nicht synchron, Dienst neu gestartet
  Software               Dekoder/Programme fehlen, Bahndaten veraltet
Das Kernel-Protokoll liest `journalctl -k` (Benutzer in der Gruppe adm); geht das nicht, wird das
einmal als Hinweis gemeldet und die übrigen Prüfungen laufen weiter.
   python3 bin/health.py        einmal alle Prüfungen ausführen und das Ergebnis zeigen
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "bin"))
import notices as NT  # noqa: E402

STATE = BASE / "data" / "health_state.json"
KERNEL_PATTERNS = [
    (re.compile(r"mmc\d.*(error|timeout|CRC)|mmcblk\d.*error", re.I), "storage", "error", "Speicherkarte meldet Fehler"),
    (re.compile(r"EXT4-fs error|EXT4-fs warning.*error|Remounting filesystem read-only", re.I), "storage", "error",
     "Dateisystem meldet Fehler"),
    (re.compile(r"(Buffer )?I/O error.*(dev|sector)|blk_update_request: I/O error", re.I), "storage", "error",
     "Lese-/Schreibfehler auf einem Laufwerk"),
    (re.compile(r"Out of memory: Kill|oom-kill:|Killed process \d+", re.I), "system", "error",
     "Speicher voll – der Kernel hat einen Prozess beendet"),
    (re.compile(r"Under-?voltage detected", re.I), "system", "warn", "Unterspannung erkannt"),
]


def _state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(st):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE)


def _run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except Exception as e:
        return -1, str(e)


# ---------------------------------------------------------------- Neustart
def check_reboot(st):
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        up = float(Path("/proc/uptime").read_text().split()[0])
    except OSError:
        return
    prev = st.get("boot_id")
    if prev and prev != boot:
        last = st.get("alive")
        dur = ""
        if last and st.get("boot_time"):
            h = (last - st["boot_time"]) / 3600
            dur = f" Vorher lief er {h:.1f} h." if h < 72 else f" Vorher lief er {h / 24:.1f} Tage."
        NT.event("reboot", "info", "system", "System neu gestartet",
                 f"Der Pi wurde neu gestartet (vor {up / 60:.0f} min), zuletzt erreichbar "
                 f"{time.strftime('%d.%m. %H:%M', time.localtime(last)) if last else 'unbekannt'}.{dur}",
                 fix="Ohne Absicht neu gestartet? Netzteil und Unterspannung prüfen (System → Zustand).")
    st["boot_id"] = boot
    st["boot_time"] = time.time() - up
    st["alive"] = time.time()


# ---------------------------------------------------------------- Sticks
def check_sticks(st):
    try:
        import sticks as STK
        ov = STK.overview()
    except Exception as e:
        NT.raise_("sticks_scan", "warn", "hardware", "Sticks lassen sich nicht abfragen", f"{type(e).__name__}: {e}")
        return
    NT.clear("sticks_scan")
    now_present = {r["key"]: r for r in ov["sticks"] if r.get("present") and r.get("kind") == "rtl"}
    before = set(st.get("sticks_present") or [])
    if "sticks_present" in st:
        for k in now_present.keys() - before:
            r = now_present[k]
            NT.event("stick", "info", "hardware", f"Stick angesteckt: {r['label']}",
                     f"{r['label']}, Seriennummer {r.get('serial') or 'ohne'}, an {r.get('port') or '?'}"
                     + ("" if r.get("role") else " – noch ohne Rolle, wird nicht benutzt."),
                     fix="" if r.get("role") else "Unter System → Geräte festlegen: Haupt-Stick, Reserve oder ignorieren.")
        for k in before - now_present.keys():
            reg = next((r for r in ov["sticks"] if r["key"] == k), {})
            NT.event("stick", "warn" if reg.get("role") == "main" else "info", "hardware",
                     f"Stick abgezogen: {reg.get('label', k)}",
                     f"Seriennummer {reg.get('serial') or 'ohne'}" + (" – das war der Haupt-Stick." if reg.get("role") == "main" else ""))
    st["sticks_present"] = sorted(now_present)
    main = next((r for r in ov["sticks"] if r.get("role") == "main"), None)
    if main and not main.get("present"):
        others = [r for r in now_present.values() if r["key"] != main["key"]]
        NT.raise_("stick_main_missing", "warn", "hardware", "Haupt-Stick fehlt",
                  f"{main['label']} (Seriennummer {main.get('serial') or 'ohne'}) steckt nicht. Scan, Satelliten "
                  "und Live auf VHF/UHF ruhen." + (f" Angesteckt ist: {', '.join(r['label'] + ' ' + (r.get('serial') or '') for r in others)}." if others else ""),
                  fix=("Stick gewechselt? Unter System → Geräte den angesteckten zum Haupt-Stick machen." if others
                       else "Stick anstecken oder USB-Verbindung prüfen."), key="stick")
    else:
        NT.clear("stick_main_missing")
    if main and main.get("present") and main.get("generic"):
        NT.raise_("stick_generic_serial", "info", "hardware", "Stick mit Allerwelts-Seriennummer",
                  f"{main['label']} hat die Seriennummer {main.get('serial')}. Mit einem zweiten Stick kann HamScanPi "
                  "die beiden nicht sicher auseinanderhalten.",
                  fix="Unter System → Geräte „Seriennummer schreiben“ (vorher andere Sticks abziehen).", notify=False)
    else:
        NT.clear("stick_generic_serial")
    pend = [r for r in now_present.values() if r.get("role") is None]
    if pend:
        NT.raise_("stick_pending", "info", "hardware", "Neuer Stick wartet auf Zuordnung",
                  ", ".join(f"{r['label']} ({r.get('serial') or 'ohne'})" for r in pend),
                  fix="Unter System → Geräte eine Rolle wählen.", notify=False)
    else:
        NT.clear("stick_pending")


# ---------------------------------------------------------------- Kernel-Protokoll
def check_kernel(st):
    since = st.get("kernel_since") or (time.time() - 600)
    code, out = _run(["journalctl", "-k", "--no-pager", "-o", "short-unix", "--since", f"@{int(since)}"], timeout=15)
    if code != 0:
        NT.raise_("kernel_log", "info", "software", "Kernel-Protokoll nicht lesbar",
                  "Fehler der Speicherkarte und Speichermangel werden darum nicht erkannt.",
                  fix="Benutzer in die Gruppe adm aufnehmen und neu anmelden:", cmd=f"sudo usermod -aG adm {os.environ.get('USER', 'gmt')}",
                  notify=False)
        return
    NT.clear("kernel_log")
    st["kernel_since"] = time.time()
    seen = {}
    for line in out.splitlines():
        for rx, area, level, title in KERNEL_PATTERNS:
            if rx.search(line):
                seen.setdefault(title, (area, level, []))[2].append(line.split(" ", 2)[-1][:200])
                break
    for title, (area, level, lines) in seen.items():
        key = "storage" if area == "storage" else ("power" if "spannung" in title else None)
        fix = {"storage": "Sicherung herunterladen (System → Sicherung) und die Karte bald tauschen – "
                          "Fehler kündigen oft einen Ausfall an. Auf eine SSD umziehen: README „SD card or SSD“.",
               "system": ""}.get(area, "")
        if "Prozess beendet" in title:
            fix = "Im Systemverlauf den größten Prozess zu dieser Zeit ansehen; Webserver neu starten gibt Speicher frei."
        if "Unterspannung" in title:
            fix = "Offizielles Netzteil 5,1 V/3 A verwenden, USB-Geräte über einen aktiven Hub versorgen."
        NT.event(key or ("oom" if "Prozess" in title else area), level, area, title,
                 f"{len(lines)}× im Kernel-Protokoll, zuletzt: {lines[-1]}", fix=fix)


# ---------------------------------------------------------------- Speicher, Strom, Uhr, Dienste
def check_storage(st):
    ro = False
    try:
        for line in Path("/proc/mounts").read_text().splitlines():
            f = line.split()
            if len(f) > 3 and f[1] == "/" and "ro" in f[3].split(","):
                ro = True
    except OSError:
        pass
    if ro:
        NT.raise_("fs_readonly", "error", "storage", "Dateisystem nur noch lesbar",
                  "Das System-Laufwerk wurde schreibgeschützt eingehängt – meist nach Fehlern der Speicherkarte. "
                  "Aufnahmen, Datenbank und Einstellungen können nicht mehr gespeichert werden.",
                  fix="Sicherung herunterladen, Pi neu starten; tritt es wieder auf, Karte tauschen.", key="storage")
    else:
        NT.clear("fs_readonly")
    du = shutil.disk_usage(str(BASE))
    pct = 100 * (du.total - du.free) / du.total
    if pct >= 90:
        NT.raise_("disk_full", "error" if pct >= 97 else "warn", "storage", f"Speicher fast voll ({pct:.0f} %)",
                  f"Noch {du.free / 1e9:.1f} GB frei.", fix="System → Aufräumen: alte Treffer löschen.", key="storage")
    elif pct < 88:
        NT.clear("disk_full")
    try:
        import sysmon as SM
        sto = SM.storage_info()
        w = sto.get("written_24h_mb")
        if sto.get("kind") == "SD-Karte" and w and w > 20480:
            NT.raise_("sd_write_load", "warn", "storage", "Hohe Schreiblast auf der SD-Karte",
                      f"{w / 1024:.1f} GB in 24 h geschrieben – das verkürzt die Lebensdauer der Karte.",
                      fix="Auf eine SSD umziehen (README „SD card or SSD“) oder Aufnahmen seltener behalten.")
        elif w is not None and w < 15360:
            NT.clear("sd_write_load")
        swaps = sto.get("swap") or []
        if swaps and all(s["zram"] for s in swaps):
            tot = sum(s["size_mb"] for s in swaps)
            used = sum(s["used_mb"] for s in swaps)
            if tot and used >= 0.95 * tot:
                NT.raise_("zram_full", "warn", "system", "Arbeitsspeicher und zram voll",
                          f"{used} von {tot} MB zram belegt – als Nächstes muss der Kernel einen Prozess beenden.",
                          fix="Im Systemverlauf den größten Prozess ansehen; Webserver neu starten gibt Speicher frei.",
                          cmd="sudo systemctl restart hamscanpi-web")
            elif not tot or used < 0.8 * tot:
                NT.clear("zram_full")
    except Exception:
        pass


def check_power(st):
    code, out = _run(["vcgencmd", "get_throttled"], timeout=5)
    if code == 0 and "=" in out:
        try:
            v = int(out.strip().split("=")[1], 16)
        except ValueError:
            v = 0
        if v & 0x1:
            NT.raise_("undervoltage", "error", "system", "Unterspannung jetzt",
                      "Die Versorgung des Pi reicht gerade nicht – Sticks und Speicherkarte können ausfallen.",
                      fix="Offizielles Netzteil 5,1 V/3 A, kurzes dickes Kabel; USB-Geräte über einen aktiven Hub.",
                      key="power")
        else:
            NT.clear("undervoltage")
        if v & 0x4 or v & 0x8:
            NT.raise_("throttled", "warn", "system", "Pi gedrosselt", "Takt gesenkt (Unterspannung oder Hitze).",
                      fix="Kühlung und Netzteil prüfen.", key="power")
        else:
            NT.clear("throttled")
    try:
        import sysmon as SM
        t = SM.cpu_temp()
        if t and t >= 78:
            NT.raise_("hot", "warn", "system", f"Pi heiß ({t:.0f} °C)", "Ab 80 °C drosselt der Pi.",
                      fix="Kühlkörper oder Lüfter, Gehäuse belüften.", key="power")
        elif t and t < 72:
            NT.clear("hot")
    except Exception:
        pass


def check_clock(st):
    code, out = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], timeout=5)
    if code == 0 and out.strip() == "no":
        st.setdefault("ntp_bad_since", time.time())
        if time.time() - st["ntp_bad_since"] > 900:
            NT.raise_("ntp", "warn", "system", "Uhr nicht synchron",
                      "Satellitenzeiten und Doppler werden ungenau, FT8 dekodiert schlechter.",
                      fix="Internetverbindung prüfen; Zeitsynchronisation einschalten:", cmd="sudo timedatectl set-ntp true")
    else:
        st.pop("ntp_bad_since", None)
        NT.clear("ntp")


def check_services(st):
    for unit, name in (("hamscanpi-web", "Webserver"), ("hamscanpi-scheduler", "Zeitplaner")):
        code, out = _run(["systemctl", "show", unit, "-p", "NRestarts", "--value"], timeout=5)
        if code != 0 or not out.strip().isdigit():
            continue
        n = int(out.strip())
        prev = st.get(f"nrestarts_{unit}")
        if prev is not None and n > prev:
            NT.event("service", "warn", "system", f"{name} wurde neu gestartet",
                     f"Der Dienst {unit} hat sich beendet und wurde von systemd neu gestartet ({n - prev}×).",
                     fix="Im Protokoll kurz vor der Uhrzeit nach Fehlern sehen.")
        st[f"nrestarts_{unit}"] = n


def check_software(st):
    if (st.get("sw_checked") or 0) > time.time() - 3600:
        return                                                 # stündlich reicht
    st["sw_checked"] = time.time()
    missing = []
    if not shutil.which("rtl_sdr"):
        missing.append(("rtl-sdr", "Programm rtl_sdr fehlt – ohne geht kein RTL-Empfang", "sudo apt install -y rtl-sdr"))
    if not shutil.which("multimon-ng"):
        missing.append(("multimon-ng", "multimon-ng fehlt – APRS und POCSAG werden nicht dekodiert",
                        "sudo apt install -y multimon-ng"))
    try:
        cfgd = json.loads((BASE / "config" / "scanner.json").read_text(encoding="utf-8"))
    except Exception:
        cfgd = {}
    dec = Path(cfgd.get("ft8_decoder", str(Path.home() / "ft8_lib" / "decode_ft8"))).expanduser()
    if not dec.exists():
        missing.append(("ft8_lib", f"FT8-Dekoder fehlt ({dec}) – FT8/FT4 werden nicht dekodiert",
                        "cd ~ && git clone https://github.com/kgoba/ft8_lib && cd ft8_lib && make"))
    for mod, pkg in (("sgp4", "python3-sgp4"), ("scipy", "python3-scipy"), ("PIL", "python3-pil"), ("soundfile", "python3-soundfile")):
        try:
            __import__(mod)
        except ImportError:
            missing.append((mod, f"Python-Paket {mod} fehlt", f"sudo apt install -y {pkg}"))
    have = set()
    for nid, title, cmd in missing:
        have.add(nid)
        NT.raise_(f"sw_{nid}", "warn", "software", title, "", fix="Installieren:", cmd=cmd)
    for nid in ("rtl-sdr", "multimon-ng", "ft8_lib", "sgp4", "scipy", "PIL", "soundfile"):
        if nid not in have:
            NT.clear(f"sw_{nid}")
    try:
        tle = BASE / "data" / "tle.json"
        if tle.exists() and time.time() - tle.stat().st_mtime > 3 * 86400:
            NT.raise_("tle_old", "warn", "satellite", "Bahndaten veraltet",
                      f"Zuletzt geholt {time.strftime('%d.%m. %H:%M', time.localtime(tle.stat().st_mtime))} – "
                      "Überflugzeiten werden ungenau.", fix="Internetverbindung prüfen; von Hand holen:",
                      cmd="cd ~/hamscanpi && python3 bin/satellites.py fetch")
        else:
            NT.clear("tle_old")
    except OSError:
        pass
    try:                                                       # Treiber und Kurzwelle des Haupt-Sticks
        import sdrcaps as SC
        import sticks as STK
        main = next((r for r in STK.load()["sticks"].values() if r.get("role") == "main"), None)
        c = SC.caps(main) if main else None
        if c and c["hf"] == "upconverter":
            NT.raise_("hf_driver", "info", "hardware", "Kurzwelle braucht einen anderen Treiber",
                      f"{c['model']}: {c['note']}.", fix="Treiber selbst übersetzen (README) – bis dahin nur ab 24 MHz.",
                      notify=False)
        elif c and c["hf"] and not SC.tool_flags().get("rtl_sdr"):
            NT.raise_("hf_driver", "info", "hardware", "Treiber ohne Direktabtastung",
                      f"{c['model']} könnte Kurzwelle, der installierte Treiber aber nicht.",
                      fix="Paket rtl-sdr aktualisieren:", cmd="sudo apt update && sudo apt install -y rtl-sdr", notify=False)
        else:
            NT.clear("hf_driver")
    except Exception:
        pass


def internet_ok(timeout=3.0):
    """Erreicht der Pi das Internet? TCP-Verbindung zu öffentlichen Diensten (DNS/HTTPS) – eine genügt."""
    import socket
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53), ("9.9.9.9", 443)):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def check_internet(st):
    """Kein Internet: nach 15 min ein Hinweis (verschwindet von selbst); Ende des Ausfalls im Protokoll.
    Ohne Netz laufen Scan, Live, Satelliten und Dekoder weiter – es fehlen PSK Reporter, Mail/Push (werden
    nachgeschickt), Bahndaten und die Zeitsynchronisation."""
    if internet_ok():
        since = st.pop("offline_since", None)
        if since:
            NT.clear("internet")
            try:
                from applog import log
                log("INFO", "health", "internet_back", offline_min=round((time.time() - since) / 60))
            except Exception:
                pass
        return
    st.setdefault("offline_since", time.time())
    if time.time() - st["offline_since"] >= 900:
        NT.raise_("internet", "warn", "system",
                  f"Kein Internet seit {time.strftime('%H:%M', time.localtime(st['offline_since']))}",
                  "Scan, Live, Satelliten und Dekoder laufen weiter. Es fehlen: PSK Reporter (die Spots der letzten Stunde "
                  "werden nachgemeldet), Mail/Push (werden nachgeschickt), neue Bahndaten und die Zeitsynchronisation.",
                  fix="Router, WLAN bzw. Netzwerkkabel prüfen.", notify=False)


CHECKS = (check_reboot, check_internet, check_sticks, check_kernel, check_storage, check_power, check_clock, check_services,
          check_software)


def run_all():
    st = _state()
    for fn in CHECKS:
        try:
            fn(st)
        except Exception as e:
            try:
                from applog import log
                log("WARN", "health", "check_failed", check=fn.__name__, error=f"{type(e).__name__}: {e}")
            except Exception:
                pass
    st["alive"] = time.time()
    _save(st)
    try:
        import notify as N
        if not st.get("offline_since"):
            N.flush_outbox()                                   # ohne Internet aufgehobene Nachrichten
        N.flush_digest()
        N.log_send_due()
    except Exception:
        pass
    try:                                                       # automatische Sicherung (System → Sicherung)
        import backup as BK
        from applog import log as _log
        BK.auto_tick(log=_log)
    except Exception:
        pass


if __name__ == "__main__":
    run_all()
    print(json.dumps(NT.listing(), ensure_ascii=False, indent=1)[:4000])
