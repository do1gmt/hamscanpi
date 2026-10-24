#!/usr/bin/env python3
"""
Geräteschicht: PMSDR (Soundkarte + FIFO) und RTL-SDR (RTL2832U-Sticks, z. B. NESDR, RTL-SDR Blog).

Beide liefern dem Scanner dasselbe: capture(seconds) -> (i, q) bei self.rate,
record(...) -> Pfad einer 48-kHz-I/Q-WAV um den Treffer, rerecord(...) für
längere Aufnahmen. Der Rest des Scanners bleibt hardwareunabhängig.
"""
import datetime
import json
import math
import os
import re
import fcntl
import shutil
import signal
import stat
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

BASE = Path(__file__).resolve().parent.parent
RECDIR = BASE / "recordings"

# Parameter, die je Gerät andere Vorgaben brauchen (band-"overrides" gewinnen)
DEVICE_DEFAULTS = {
    "pmsdr": {
        "sample_rate": 48000, "fft_size": 8192, "edge_guard_hz": 2500,
        "dc_guard_hz": 300, "min_peak_spacing_hz": 250, "iq_conjugate": None,  # None = aus scanner.json
        "settle_seconds": 0.25,
    },
    "rtlsdr": {
        "sample_rate": 1024000, "fft_size": 32768, "edge_guard_hz": 60000,
        "dc_guard_hz": 8000, "min_peak_spacing_hz": 8000, "iq_conjugate": False,
        "settle_seconds": 0.0, "max_peaks_per_tune": 20, "birdie_min_fraction": 0.6,
    },
}

# Breite des Aufnahme-Ausschnitts je Betriebsart (RTL-SDR); Standard 48 kHz
SLICE_RATE = {"wfm": 240000, "dab": 240000}


def wav_write_iq(path, i, q, rate):
    i16 = np.clip(i * 32767, -32768, 32767).astype("<i2")
    q16 = np.clip(q * 32767, -32768, 32767).astype("<i2")
    inter = np.empty(len(i16) * 2, dtype="<i2")
    inter[0::2] = i16
    inter[1::2] = q16
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(inter.tobytes())


def recording_name(band, rf_hz):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(band))
    return f"{now}_{safe}_{int(round(rf_hz))}Hz.wav"


# ------------------------------------------------------------------ PMSDR

class PmsdrDevice:
    name = "pmsdr"

    def __init__(self, cfg):
        self.cfg = cfg
        self.rate = int(cfg["sample_rate"])
        self.card, self.dev = self._find_alsa(cfg["alsa_name"], int(cfg["alsa_device"]))
        self.fifo_fd = self._open_fifo(cfg["fifo"])
        self.center = None

    @staticmethod
    def _find_alsa(name, device):
        r = subprocess.run(["env", "LC_ALL=C", "arecord", "-l"], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("arecord -l fehlgeschlagen")
        for line in r.stdout.splitlines():
            m = re.match(r"(?:card|karte) (\d+): (\S+)", line, re.I)
            if m and name.lower() in line.lower():
                return int(m.group(1)), device
        # Name nicht gefunden: Karte über die USB-Kennung suchen (ALSA benennt sie je nach Kernel anders)
        ids, _ = _pmsdr_audio_cfg()
        ids = ids or USB_IDS["aureon"]
        for f in sorted(Path("/proc/asound").glob("card*/usbid")):
            try:
                vid, pid = f.read_text().strip().lower().split(":")
            except Exception:
                continue
            if (vid, pid) in ids:
                return int(f.parent.name[4:]), device
        usb_there = any((d / "idVendor").exists() and ((d / "idVendor").read_text().strip(), (d / "idProduct").read_text().strip()) in ids
                        for d in Path("/sys/bus/usb/devices").glob("*") if (d / "idProduct").exists())
        if usb_there:
            raise RuntimeError(f"Soundkarte '{name}' steckt (USB), wird aber nicht als Soundkarte erkannt – "
                               "Stecker der Soundkarte ziehen und neu stecken oder den Pi neu starten")
        raise RuntimeError(f"Soundkarte '{name}' nicht gefunden – ist die Soundkarte der PMSDR angesteckt?")

    @staticmethod
    def _open_fifo(path, wait_s=20.0):
        """FIFO zum PMSDR-Steuerprogramm öffnen. Liest dort gerade niemand (Programm startet
        neu oder läuft nicht), liefert der Kernel ENXIO – dann bis zu wait_s warten."""
        p = Path(path)
        t0 = time.time()
        while True:
            if p.exists() and stat.S_ISFIFO(p.stat().st_mode):
                try:
                    return os.open(str(p), os.O_WRONLY | os.O_NONBLOCK)
                except OSError as e:
                    if e.errno not in (6,):            # ENXIO: kein Leser
                        raise
            if time.time() - t0 > wait_s:
                raise RuntimeError("PMSDR-Steuerung läuft nicht (pmsdr-control: "
                                   f"{_unit_state('pmsdr-control')}) – PMSDR angesteckt? "
                                   "sudo systemctl start pmsdr-control")
            time.sleep(0.5)

    def tune(self, center):
        cmd = f"f {int(center)}\n".encode("ascii")
        try:
            os.write(self.fifo_fd, cmd)
        except OSError:
            # Steuerprogramm wurde neu gestartet (z. B. nach USB-Reset): neu verbinden, einmal wiederholen
            try:
                os.close(self.fifo_fd)
            except OSError:
                pass
            _applog("WARN", "scanner", "pmsdr_fifo_reconnect", fifo=self.cfg["fifo"])
            self.fifo_fd = self._open_fifo(self.cfg["fifo"])
            os.write(self.fifo_fd, cmd)
        self.center = int(center)
        time.sleep(float(self.cfg.get("settle_seconds", 0.25)))

    def capture(self, seconds):
        # Genau N Abtastwerte (-s) statt „seconds schlafen, dann abbrechen“: arecord braucht am Pi
        # bis über eine Sekunde zum Öffnen der Soundkarte – vorher kam bei 3 s nur ~1,5 s Audio an.
        frames = int(round(self.rate * seconds))
        cmd = ["arecord", "-q", "-D", f"plughw:{self.card},{self.dev}", "-f", "S16_LE",
               "-r", str(self.rate), "-c", "2", "-t", "raw", "-s", str(frames)]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            raw, _ = p.communicate(timeout=seconds + 8)
        except subprocess.TimeoutExpired:
            p.kill()
            raw, _ = p.communicate()
        if len(raw) < frames * 4 * 0.9:
            raise RuntimeError(f"Zu wenig Audiodaten: {len(raw)} von {frames * 4} Byte")
        s = np.frombuffer(raw, dtype="<i2")
        s = s[: len(s) // 2 * 2].reshape(-1, 2)
        i = s[:, 0].astype(np.float64) / 32768.0
        q = s[:, 1].astype(np.float64) / 32768.0
        return i - i.mean(), q - q.mean()

    def capture_slot(self, slot_s, lead_s=2.0):
        """Genau einen FT8/FT4-Takt aufnehmen, beginnend auf der UTC-Taktgrenze.
        arecord startet vorher (braucht am Pi > 1 s); der Anfang wird über die Ankunftszeit
        der ersten Daten bestimmt und passend abgeschnitten. Rückgabe (i, q, taktbeginn_unix)."""
        T = math.ceil((time.time() + lead_s) / slot_s) * slot_s
        cmd = ["arecord", "-q", "-D", f"plughw:{self.card},{self.dev}", "-f", "S16_LE",
               "-r", str(self.rate), "-c", "2", "-t", "raw"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        block = int(self.rate) // 20                       # 50 ms
        try:
            first = p.stdout.read(block * 4)
            if not first:
                raise RuntimeError("arecord liefert keine Daten")
            t0 = time.time() - (len(first) // 4) / self.rate   # Zeit des ersten Abtastwerts
            if t0 > T - 0.2:
                # Soundkarte zu spät offen – nächsten Takt nehmen
                T += slot_s
            parts = [first]
            total = len(first) // 4
            end = T + slot_s + 0.3
            while t0 + total / self.rate < end:
                b = p.stdout.read(block * 4)
                if not b:
                    raise RuntimeError("arecord abgebrochen")
                parts.append(b)
                total += len(b) // 4
        finally:
            p.terminate()
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
        s = np.frombuffer(b"".join(parts), dtype="<i2")
        s = s[: len(s) // 2 * 2].reshape(-1, 2)
        k = int(round((T - t0) * self.rate))
        s = s[k: k + int(slot_s * self.rate)]
        i = s[:, 0].astype(np.float64) / 32768.0
        q = s[:, 1].astype(np.float64) / 32768.0
        return i - i.mean(), q - q.mean(), T

    def record(self, center, seconds, band, rf_hz, mode=None):
        """Aufnahme auf der Scan-Mitte (die Aufnahme deckt das ganze 48-kHz-Fenster ab)."""
        self.tune(center)
        RECDIR.mkdir(parents=True, exist_ok=True)
        path = RECDIR / recording_name(band, rf_hz)
        cmd = ["arecord", "-q", "-D", f"plughw:{self.card},{self.dev}", "-f", "S16_LE",
               "-r", str(self.rate), "-c", "2", "-d", str(int(math.ceil(seconds))), str(path)]
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if r.returncode != 0:
            raise RuntimeError("Trefferaufnahme fehlgeschlagen: " + r.stderr.strip())
        return str(path), int(center)

    def rerecord(self, path, center, seconds, rf_hz, mode=None):
        Path(path).unlink(missing_ok=True)
        self.tune(center)
        cmd = ["arecord", "-q", "-D", f"plughw:{self.card},{self.dev}", "-f", "S16_LE",
               "-r", str(self.rate), "-c", "2", "-d", str(int(math.ceil(seconds))), str(path)]
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if r.returncode != 0:
            raise RuntimeError("Langaufnahme fehlgeschlagen: " + r.stderr.strip())
        return int(center)

    def close(self):
        try:
            os.close(self.fifo_fd)
        except Exception:
            pass


# ------------------------------------------------------------------ RTL-Hilfen

RTL_USB_IDS = {("0bda", "2838"), ("0bda", "2832")}
USBDEVFS_RESET = ord("U") << 8 | 20


def _applog(level, comp, event, **kw):
    try:
        from applog import log
        log(level, comp, event, **kw)
    except Exception:
        pass


def rtl_holders():
    """Andere Prozesse, die typischerweise den Stick belegen."""
    names = ("rtl_fm", "rtl_sdr", "rtl_tcp", "rtl_test", "rtl_power", "rtl_433", "dump1090",
             "readsb", "SoapySDRServer", "gqrx", "openwebrx")
    out = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit() or int(d.name) == os.getpid():
            continue
        try:
            cmd = (d / "cmdline").read_bytes().split(b"\0")
            exe = os.path.basename(cmd[0].decode(errors="replace")) if cmd and cmd[0] else ""
        except Exception:
            continue
        if exe in names:
            try:
                state = (d / "stat").read_text().rsplit(")", 1)[1].split()[0]
            except Exception:
                state = "?"
            out.append(f"{exe} (PID {d.name}{', hängt im Kernel' if state == 'D' else ''})")
    return out


def kill_orphan_rtl():
    """Verwaiste rtl_sdr/rtl_fm (Elternprozess weg, z. B. nach abgebrochenem Scan) beenden."""
    killed = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            cmd = (d / "cmdline").read_bytes().split(b"\0")
            exe = os.path.basename(cmd[0].decode(errors="replace")) if cmd and cmd[0] else ""
            if exe not in ("rtl_sdr", "rtl_fm"):
                continue
            st = (d / "stat").read_text()
            ppid = int(st.rsplit(")", 1)[1].split()[1])
            if ppid != 1 or d.stat().st_uid != os.getuid():
                continue
            os.kill(int(d.name), signal.SIGINT)
            killed.append(f"{exe} (PID {d.name})")
        except Exception:
            continue
    if killed:
        time.sleep(1.5)
    return killed


def rtl_diagnose(stderr, hung, got, expected, index=0):
    """Aus der rtl_sdr-Ausgabe eine verständliche Ursache machen."""
    e = stderr or ""
    if "No supported devices" in e:
        return "kein Stick gefunden (USB-Kabel/Port prüfen, lsusb)"
    if "usb_claim_interface" in e or "Failed to open" in e:
        h = rtl_holders()
        return "Stick belegt" + (f" von {', '.join(h)}" if h else " (anderes Programm oder DVB-Kerneltreiber)")
    if "Kernel driver is active" in e:
        return "DVB-Kerneltreiber aktiv – dvb_usb_rtl28xxu sperren"
    if hung and "async mode" in e:
        return (f"Stick geöffnet, aber USB-Datenstrom bleibt stehen ({got} von {expected} Bytes) – "
                "typisch für hängenden Stick, Stromversorgung oder USB-Port")
    if hung:
        h = rtl_holders()
        return ("rtl_sdr hängt schon beim Öffnen" + (f"; außerdem laufen {', '.join(h)}" if h else "")
                + (f" – Ausgabe: {e.strip().splitlines()[-1]}" if e.strip() else ""))
    return f"nur {got} von {expected} Bytes" + (f" – {e.strip().splitlines()[-1]}" if e.strip() else "")


def usb_reset_rtl(index=0):
    """RTL-Stick per USBDEVFS_RESET neu anmelden (wie Ab- und Anstecken).
    Braucht Schreibrecht auf /dev/bus/usb/… – die udev-Regel des Pakets rtl-sdr
    gibt das der Gruppe plugdev, in der der Pi-Benutzer normalerweise ist."""
    found = []
    for d in sorted(Path("/sys/bus/usb/devices").glob("*")):
        try:
            vid = (d / "idVendor").read_text().strip()
            pid = (d / "idProduct").read_text().strip()
        except Exception:
            continue
        if (vid, pid) in RTL_USB_IDS:
            bus = int((d / "busnum").read_text())
            dev = int((d / "devnum").read_text())
            found.append(f"/dev/bus/usb/{bus:03d}/{dev:03d}")
    if not found:
        return False, "kein RTL-Stick im USB-Baum gefunden"
    node = found[min(index, len(found) - 1)]
    try:
        fd = os.open(node, os.O_WRONLY)
        try:
            fcntl.ioctl(fd, USBDEVFS_RESET, 0)
        finally:
            os.close(fd)
        return True, node
    except PermissionError:
        return False, f"keine Rechte auf {node} (Benutzer in Gruppe plugdev?)"
    except OSError as ex:
        return False, f"{node}: {ex}"


# ------------------------------------------------------------------ RTL-SDR

class RtlDevice:
    name = "rtlsdr"

    def __init__(self, cfg):
        self.cfg = cfg
        self.rate = int(cfg.get("sample_rate", 1024000))
        rtl = cfg.get("rtlsdr", {}) if isinstance(cfg.get("rtlsdr"), dict) else {}
        self.binary = rtl.get("binary", "rtl_sdr")
        self.index = int(rtl.get("device_index", 0))
        self.gain = rtl.get("gain", 0)            # 0 = automatisch
        self.ppm = int(rtl.get("ppm", 0))
        self.bias_t = bool(rtl.get("bias_t", False))
        self.center = None
        if shutil.which(self.binary) is None:
            raise RuntimeError(f"'{self.binary}' nicht gefunden – Paket rtl-sdr installieren (sudo apt install rtl-sdr)")
        killed = kill_orphan_rtl()
        if killed:
            _applog("WARN", "scanner", "rtl_orphans_killed", processes=killed)

    def tune(self, center):
        self.center = int(center)   # rtl_sdr stimmt beim Start ab

    def _run_once(self, cmd, n, timeout):
        """rtl_sdr einmal starten. Rückgabe (rohdaten, stderr, hing_fest)."""
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            raw, err = p.communicate(timeout=timeout)
            return raw, err.decode(errors="replace"), False
        except subprocess.TimeoutExpired:
            pass
        # Erst SIGINT: rtl_sdr bricht den USB-Datenstrom dann sauber ab und gibt den
        # Stick frei. Ein hartes SIGKILL (wie bisher über subprocess.run) lässt den
        # RTL2832U gern in einem Zustand zurück, in dem auch der nächste Start hängt.
        raw, err = b"", b""
        for sig, wait in ((signal.SIGINT, 3), (signal.SIGTERM, 2), (signal.SIGKILL, 2)):
            try:
                p.send_signal(sig)
                raw, err = p.communicate(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                continue
            except Exception:
                break
        return raw or b"", (err or b"").decode(errors="replace"), True

    def _grab(self, center, seconds):
        n = int(self.rate * seconds)
        cmd = [self.binary, "-f", str(int(center)), "-s", str(self.rate), "-n", str(n),
               "-d", str(self.index), "-p", str(self.ppm)]
        if self.gain:
            cmd += ["-g", str(self.gain)]
        if self.bias_t:
            cmd += ["-T"]
        cmd.append("-")
        # Normal braucht rtl_sdr ~1 s zum Öffnen + die Aufnahmezeit
        timeout = seconds * 1.5 + 6
        problems = []
        for attempt in (1, 2):
            raw, err, hung = self._run_once(cmd, n, timeout)
            if len(raw) >= n * 2 * 0.5:
                if attempt > 1:
                    _applog("WARN", "scanner", "rtl_recovered", attempt=attempt, problems=problems)
                # float32 reicht für 8-Bit-Daten und halbiert den Speicher (wichtig am Pi)
                u = np.frombuffer(raw, dtype=np.uint8)[: len(raw) // 2 * 2].reshape(-1, 2)
                i = u[:, 0].astype(np.float32); i -= 127.5; i /= 127.5
                q = u[:, 1].astype(np.float32); q -= 127.5; q /= 127.5
                del raw, u
                i -= i.mean(); q -= q.mean()
                return i, q
            diag = rtl_diagnose(err, hung, len(raw), n * 2, self.index)
            problems.append(diag)
            _applog("WARN", "scanner", "rtl_no_data", attempt=attempt, freq_hz=int(center),
                    hung=hung, bytes=len(raw), expected=n * 2, diagnosis=diag,
                    stderr=" | ".join(err.strip().splitlines()[-6:]))
            if diag.startswith("Stick belegt von"):
                break   # ein lebender Prozess hält ihn – ein Reset würde dem nur den Stick wegziehen
            if attempt == 1:
                ok, how = usb_reset_rtl(self.index)
                _applog("INFO" if ok else "WARN", "scanner", "rtl_usb_reset", ok=ok, detail=how)
                time.sleep(2.0 if ok else 0.5)
        raise RuntimeError("RTL-SDR liefert keine Daten: " + problems[-1])

    def capture(self, seconds):
        return self._grab(self.center, seconds)

    def _slice(self, i, q, center, rf_hz, out_rate):
        """48-kHz-Ausschnitt (oder breiter) um rf_hz aus dem breiten Fenster herausrechnen.
        Stückweise mit Überlappung, damit auch 10-s-Aufnahmen am Pi nicht den Speicher sprengen."""
        shift = float(rf_hz) - float(center)
        g = math.gcd(int(out_rate), int(self.rate))
        up, down = int(out_rate) // g, int(self.rate) // g
        pad = 20 * down                       # >= halbe Filterlänge von resample_poly (10*max(up,down))
        chunk = max(down, (262144 // down) * down)
        n_total = len(i)
        out = []
        for s0 in range(0, n_total, chunk):
            a = max(0, s0 - pad)
            b = min(n_total, s0 + chunk + pad)
            n = np.arange(a, b, dtype=np.float64)
            z = (i[a:b].astype(np.complex64) + 1j * q[a:b].astype(np.complex64)) * \
                np.exp(-2j * np.pi * shift * n / self.rate).astype(np.complex64)
            y = resample_poly(z, up, down)
            lo = (s0 - a) * up // down
            hi = lo + (min(n_total, s0 + chunk) - s0) * up // down
            out.append(y[lo:hi].astype(np.complex64))
        z = np.concatenate(out) if out else np.zeros(0, np.complex64)
        peak = float(np.max(np.abs(z))) if len(z) else 0.0
        z = z / (peak or 1.0) * 0.8
        return z.real.astype(np.float64), z.imag.astype(np.float64)

    def record(self, center, seconds, band, rf_hz, mode=None):
        """Breite Aufnahme, dann Ausschnitt um den Treffer; Aufnahme-Mitte = rf_hz."""
        out_rate = SLICE_RATE.get(mode or "", 48000)
        i, q = self._grab(center, seconds)
        ri, rq = self._slice(i, q, center, rf_hz, out_rate)
        RECDIR.mkdir(parents=True, exist_ok=True)
        path = RECDIR / recording_name(band, rf_hz)
        wav_write_iq(path, ri, rq, out_rate)
        return str(path), int(round(rf_hz))

    def rerecord(self, path, center, seconds, rf_hz, mode=None):
        out_rate = SLICE_RATE.get(mode or "", 48000)
        i, q = self._grab(center, seconds)
        ri, rq = self._slice(i, q, center, rf_hz, out_rate)
        Path(path).unlink(missing_ok=True)
        wav_write_iq(path, ri, rq, out_rate)
        return int(round(rf_hz))

    def close(self):
        pass


def effective_config(base_cfg, band):
    """Gerätespezifische Vorgaben + Band-Overrides über die globale Konfiguration legen."""
    device = str(band.get("device", "pmsdr"))
    cfg = dict(base_cfg)
    for k, v in DEVICE_DEFAULTS.get(device, {}).items():
        if v is None:
            continue
        if device != "pmsdr":
            cfg[k] = v
    cfg.update(band.get("overrides", {}) or {})
    cfg["device"] = device
    return cfg


def open_device(name, cfg):
    if name == "rtlsdr":
        lk = BASE / "data" / "live.lock"
        if lk.exists() and (time.time() - lk.stat().st_mtime) < 120:
            raise RuntimeError("Stick belegt: Live-Empfang läuft – erst im Dashboard unter Live stoppen")
        return RtlDevice(cfg)
    return PmsdrDevice(cfg)


# ------------------------------------------------------------------ angesteckte Hardware

USB_IDS = {
    "rtlsdr": {("0bda", "2838"), ("0bda", "2832")},     # RTL2832U-Sticks
    "pmsdr_usb": {("04d8", "000c")},                     # PMSDR USB Board (IW3AUT)
    "aureon": {("0ccd", "00b1")},                        # TerraTec Aureon 7.1 USB (I/Q der PMSDR)
}


def _unit_state(unit):
    try:
        r = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=3)
        return r.stdout.strip() or "unbekannt"
    except Exception:
        return "unbekannt"


def _pmsdr_audio_cfg():
    """(USB-Kennungen der PMSDR-Soundkarte oder None für den eingebauten Standard, ALSA-Name)."""
    try:
        cfg = json.loads((BASE / "config" / "scanner.json").read_text(encoding="utf-8"))
    except Exception:
        return None, ""
    v = cfg.get("pmsdr_soundcard_usb")
    if v is None:
        return None, cfg.get("alsa_name", "")
    return {tuple(x.lower().split(":")) for x in v if ":" in x}, cfg.get("alsa_name", "")


def detect_hardware():
    """Welche Geräte stecken gerade? Erkennung über USB-Kennung, unabhängig vom Port."""
    seen = {k: [] for k in USB_IDS}
    fake = os.environ.get("PMSDR_FAKE_HW")          # nur zum Testen ohne Hardware: "rtlsdr,pmsdr"
    if fake is not None:
        f = {x.strip() for x in fake.split(",")}
        seen = {"rtlsdr": ["test"] if "rtlsdr" in f else [], "pmsdr_usb": ["test"] if "pmsdr" in f else [],
                "aureon": ["test"] if "pmsdr" in f else []}
    # Soundkarte der PMSDR: USB-Kennungen aus config/scanner.json (pmsdr_soundcard_usb, z. B. ["0ccd:00b1"]);
    # ohne Eintrag zählt jede ALSA-Karte, deren Name alsa_name enthält
    ids_cfg, alsa_name = _pmsdr_audio_cfg()
    usb_ids = dict(USB_IDS)
    if ids_cfg is not None:
        usb_ids["aureon"] = ids_cfg
    for d in ([] if fake is not None else Path("/sys/bus/usb/devices").glob("*")):
        try:
            vid = (d / "idVendor").read_text().strip()
            pid = (d / "idProduct").read_text().strip()
        except Exception:
            continue
        for k, ids in usb_ids.items():
            if (vid, pid) in ids:
                seen[k].append(d.name)
    if fake is None and not seen["aureon"] and ids_cfg is not None and not ids_cfg and alsa_name:
        try:
            r = subprocess.run(["env", "LC_ALL=C", "arecord", "-l"], capture_output=True, text=True, timeout=3)
            if alsa_name.lower() in r.stdout.lower():
                seen["aureon"].append("alsa")
        except Exception:
            pass
    return {
        "rtlsdr": {"present": bool(seen["rtlsdr"]), "port": ", ".join(seen["rtlsdr"])},
        "pmsdr": {"present": bool(seen["pmsdr_usb"]) and bool(seen["aureon"]),
                  "usb": bool(seen["pmsdr_usb"]), "audio": bool(seen["aureon"]),
                  "control": (_unit_state("pmsdr-control") if seen["pmsdr_usb"] and fake is None
                              else ("active" if seen["pmsdr_usb"] else "inactive")),
                  "port": ", ".join(seen["pmsdr_usb"] + seen["aureon"])},
    }


def resolve_device(choice, hw=None):
    """Gewähltes Gerät, solange es steckt – sonst automatisch das andere, falls das steckt."""
    hw = hw or detect_hardware()
    choice = "rtlsdr" if choice == "rtlsdr" else "pmsdr"
    other = "pmsdr" if choice == "rtlsdr" else "rtlsdr"
    if hw[choice]["present"] or not hw[other]["present"]:
        return choice
    return other
