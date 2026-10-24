#!/usr/bin/env python3
"""
Live-Empfang (Betriebsmodus 2): Frequenz waehlen, sofort mithoeren, durchkurbeln,
Kanal-Suchlauf. Audio wird als PCM-Strom an den Browser geschickt.

RTL-SDR: rtl_fm uebernimmt Demodulation (fm/wbfm/am/usb/lsb), Suchlauf per
kurzer rtl_sdr-Weitbandaufnahme + Kanal-Pegel.
PMSDR:   FIFO-Abstimmung + arecord, Demodulation in Python (zustandsbehaftet,
         ohne Blockgrenzen-Knacken).
"""
import json
import math
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
from scipy.signal import butter, ellip, sosfilt, sosfilt_zi

BASE = Path(__file__).resolve().parent.parent
LIVE_LOCK = BASE / "data" / "live.lock"
OUT_RATE = 24000          # Audio zum Browser (s16le mono)


class LiveSession:
    def __init__(self, cfg, device, freq_hz, mode, squelch_db=0.0, scan=None, on_status=None):
        self.cfg = cfg
        self.device = device
        self.freq_hz = int(freq_hz)
        self.mode = mode
        self.squelch_db = float(squelch_db)
        self.scan = scan            # None oder dict(name, channels_hz, hold_s)
        self.subscribers = []
        self.lock = threading.Lock()
        self.running = False
        self.proc = None
        self.level_db = -120.0
        self.active_hz = self.freq_hz
        self.wf = {"seq": 0, "row": [], "center_hz": 0, "span_hz": 0}     # letzte Wasserfall-Zeile
        self.rds = None                                                   # RDS des UKW-Senders (Live)
        self._agc_gain = 29.7                                             # eigene Regelung bei „automatisch“
        self._agc_hold = 0.0
        self._wf_tuned = 0
        self.status_text = "bereit"
        self.thread = None
        self.pmsdr = None
        self.restart_wanted = threading.Event()

    # ---------------- Abonnenten (HTTP-Streams)
    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def _publish(self, pcm_bytes):
        self._beat = getattr(self, "_beat", 0) + 1
        if self._beat % 200 == 0:          # Heartbeat fuer scheduler/scan-now
            try:
                os.utime(LIVE_LOCK)
            except Exception:
                pass
        with self.lock:
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put_nowait(pcm_bytes)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(pcm_bytes)
                except Exception:
                    pass

    def _wf_row(self, z, rate, rf_center, span_hz, bins=256):
        """Eine Wasserfall-Zeile: Leistungsspektrum um rf_center (±span/2), 0–255 skaliert."""
        n = 4096 if len(z) >= 4096 else 1 << int(np.log2(max(256, len(z))))
        seg = z[-n:] * np.hanning(n)
        P = np.abs(np.fft.fftshift(np.fft.fft(seg))) ** 2
        f = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / rate))
        m = np.abs(f - (rf_center - self._wf_tuned)) <= span_hz / 2
        if m.sum() < 8:
            return
        p = 10 * np.log10(P[m] + 1e-12)
        p = np.interp(np.linspace(0, len(p) - 1, bins), np.arange(len(p)), p)
        lo = float(np.percentile(p, 20))                     # Rauschen unten, 50 dB Bereich
        row = np.clip((p - lo + 5) / 50 * 255, 0, 255).astype(int).tolist()
        self.wf = {"seq": self.wf["seq"] + 1, "row": row, "center_hz": int(rf_center), "span_hz": int(span_hz)}

    def _level(self, pcm_bytes):
        a = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float64) / 32768.0
        if len(a):
            self.level_db = float(20 * np.log10(np.sqrt(np.mean(a * a)) + 1e-9))

    # ---------------- Steuerung
    def start(self):
        self.running = True
        LIVE_LOCK.write_text(str(os.getpid()))
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.restart_wanted.set()
        self._kill()
        if self.thread:
            self.thread.join(timeout=5)
        if self.pmsdr:
            self.pmsdr.close()
        LIVE_LOCK.unlink(missing_ok=True)

    def tune(self, freq_hz=None, mode=None, squelch_db=None, gain=None):
        if gain is not None:
            self.gain = gain
        if freq_hz is not None:
            self.freq_hz = int(freq_hz)
        if mode is not None:
            self.mode = mode
        if squelch_db is not None:
            self.squelch_db = float(squelch_db)
        self.scan = None
        self.restart_wanted.set()

    def start_scan(self, scan):
        self.scan = scan
        self.restart_wanted.set()

    def _kill(self):
        p = self.proc
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=2)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        self.proc = None

    def status(self):
        return {
            "running": self.running, "device": self.device, "freq_hz": self.freq_hz,
            "active_hz": self.active_hz, "mode": self.mode, "squelch_db": self.squelch_db,
            "level_db": round(self.level_db, 1), "scan": self.scan["name"] if self.scan else None,
            "snr_db": getattr(self, "snr_db", None), "clip_pct": getattr(self, "clip_pct", None),
            "rds": self.rds if (self.mode == "wfm" and self.rds and self.rds.get("freq_hz") == self.active_hz) else None,
            "gain": self._rtl_gain() if self.device == "rtlsdr" else None,
            "text": self.status_text, "listeners": len(self.subscribers), "rate": OUT_RATE,
        }

    # ---------------- Hauptschleife
    def _run(self):
        failures = 0
        idle_since = None
        try:
            while self.running:
                # Ohne Zuhoerer (Browser weg) nach 90 s von selbst beenden
                if not self.subscribers:
                    idle_since = idle_since or time.time()
                    if time.time() - idle_since > 90:
                        self.status_text = "beendet – kein Zuhörer mehr"
                        break
                else:
                    idle_since = None
                self.restart_wanted.clear()
                LIVE_LOCK.write_text(str(os.getpid()))
                t0 = time.time()
                if self.device == "rtlsdr":
                    if self.scan and self.scan.get("kind") == "seek":
                        self._rtl_seek()
                    elif self.scan:
                        self._rtl_scan()
                    else:
                        self._rtl_listen(self.freq_hz)
                else:
                    self._pmsdr_listen()
                # Prozess sofort wieder weg? Nicht endlos neu starten.
                if not self.restart_wanted.is_set() and time.time() - t0 < 2.0:
                    failures += 1
                    if failures >= 3:
                        if not self.status_text.startswith("Fehler"):
                            self.status_text = "Fehler: " + self.status_text
                        break
                    time.sleep(1.0)
                else:
                    failures = 0
        except Exception as e:
            self.status_text = f"Fehler: {e}"
        finally:
            self._kill()
            self.running = False
            LIVE_LOCK.unlink(missing_ok=True)

    # ---------------- RTL-SDR: hoeren
    def _rtl_fm_cmd(self, freq_hz):
        rtl = self.cfg.get("rtlsdr", {}) if isinstance(self.cfg.get("rtlsdr"), dict) else {}
        gain = rtl.get("gain", 0)
        ppm = int(rtl.get("ppm", 0))
        m = {"nfm": "fm", "wfm": "wbfm", "am": "am", "usb": "usb", "lsb": "lsb"}.get(self.mode, "fm")
        cmd = ["rtl_fm", "-d", str(int(rtl.get("device_index", 0))), "-M", m, "-f", str(int(freq_hz)), "-p", str(ppm)]
        if m == "wbfm":
            cmd += ["-s", "200000", "-r", str(OUT_RATE)]
        elif m in ("usb", "lsb"):
            cmd += ["-s", "24000", "-r", str(OUT_RATE)]
        else:
            cmd += ["-s", "24000", "-r", str(OUT_RATE), "-E", "deemp"] if m == "fm" else ["-s", "24000", "-r", str(OUT_RATE)]
        if gain:
            cmd += ["-g", str(gain)]
        if self.squelch_db > 0 and m in ("fm", "am"):
            cmd += ["-l", str(int(self.squelch_db * 10))]   # rtl_fm-Squelch ist eine Rohzahl, grob skaliert
        cmd.append("-")
        return cmd

    RTL_GAINS = [0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7, 20.7, 22.9, 25.4, 28.0, 29.7,
                 32.8, 33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6]

    def _agc_step(self, gain, clip, peak):
        """Nächste Verstärkungsstufe: bei Übersteuerung (> 0,3 % der Werte am Anschlag) zwei Stufen runter
        (und 30 s nicht wieder rauf), bei sehr leisem Signal (Spitzen < 25 % des Bereichs) eine rauf."""
        g = self.RTL_GAINS
        i = min(range(len(g)), key=lambda k: abs(g[k] - gain))
        if clip > 0.003 and i > 0:
            self._agc_hold = time.time() + 30
            return g[max(0, i - 2)]
        if peak < 32 and i < len(g) - 1 and time.time() > self._agc_hold:
            return g[i + 1]
        return gain

    def _rtl_gain(self):
        rtl = self.cfg.get("rtlsdr", {}) if isinstance(self.cfg.get("rtlsdr"), dict) else {}
        g = getattr(self, "gain", None)
        return rtl.get("gain", 0) if g is None else g

    def _rtl_listen(self, freq_hz, hold_until_silent=False, silence_s=3.0, max_s=None, hold_thr=None):
        """Hören mit eigener Demodulation (live_dsp): rtl_sdr-Rohdaten -> PCM.
        Zeigt Übersteuerung und Rauschabstand an; hold_until_silent beendet nach silence_s
        ohne Signal (für den Suchlauf)."""
        import live_dsp as DSP
        rtl = self.cfg.get("rtlsdr", {}) if isinstance(self.cfg.get("rtlsdr"), dict) else {}
        self.active_hz = int(freq_hz)
        gain = self._rtl_gain()
        # „automatisch“: nicht die Regelung im Stick (übersteuert bei starken Ortssendern), sondern eine
        # eigene: feste Stufen, runter bei Übersteuerung, rauf wenn das Signal sehr leise ist
        agc = not gain
        if agc:
            gain = self._agc_gain
        def spawn(g):
            cmd = [rtl.get("binary", "rtl_sdr"), "-d", str(int(rtl.get("device_index", 0))),
                   "-f", str(int(freq_hz) + DSP.TUNE_OFFSET), "-s", str(DSP.IN_RATE), "-p", str(int(rtl.get("ppm", 0)))]
            if g:
                cmd += ["-g", str(g)]
            cmd.append("-")
            return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.proc = spawn(gain)
        demod = DSP.RtlDemod(self.mode)
        rds = None
        if self.mode == "wfm":
            import rds as RDS
            # bei Neustart auf derselben Frequenz (z. B. neue Verstärkungsstufe) den Stand behalten
            if getattr(self, "_rds_dec", None) is not None and getattr(self, "_rds_freq", None) == int(freq_hz):
                rds = self._rds_dec
            else:
                rds = RDS.RdsDecoder(DSP.MID_RATE)
                self._rds_dec, self._rds_freq = rds, int(freq_hz)
                self.rds = None
        blk = 9600 * 2 * 2                    # 20 ms Rohdaten (Vielfaches von 120 Werten)
        per_pub = 5                           # alle 100 ms an den Browser
        buf, nblk = [], 0
        t_start = time.time()
        last_signal = time.time()
        idle_t = None
        base = f"{freq_hz/1e6:.4f} MHz ({self.mode})"
        agc_clip, agc_peak, agc_n = 0.0, 0.0, 0
        self.status_text = f"Empfang {base}"
        while self.running and not self.restart_wanted.is_set():
            if max_s is not None and time.time() - t_start > max_s:
                break
            if not self.subscribers:
                idle_t = idle_t or time.time()
                if time.time() - idle_t > 90:
                    self.running = False
                    self.status_text = "beendet – kein Zuhörer mehr"
                    break
            else:
                idle_t = None
            raw = self.proc.stdout.read(blk)
            if not raw:
                if self.proc.poll() is not None:
                    self.status_text = "rtl_sdr beendet – Stick frei? Läuft ein Scan?"
                    break
                time.sleep(0.02)
                continue
            buf.append(demod.process(raw, self.squelch_db))
            if rds is not None and getattr(demod, "mpx", None) is not None:
                try:
                    inf = rds.feed(demod.mpx)
                    if inf["groups"]:
                        self.rds = dict(inf, freq_hz=int(freq_hz))
                except Exception:
                    rds = None
            nblk += 1
            if nblk % per_pub:
                continue
            try:                                         # Wasserfall: ±100 kHz (WFM ±250 kHz) um die Frequenz
                u = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
                zz = ((u[0::2] - 127.5) + 1j * (u[1::2] - 127.5)) / 127.5
                self._wf_tuned = int(freq_hz) + DSP.TUNE_OFFSET
                self._wf_row(zz, DSP.IN_RATE, int(freq_hz), 500000 if self.mode == "wfm" else 200000)
            except Exception:
                pass
            pcm = b"".join(buf)
            buf = []
            if agc:
                agc_clip += demod.clip_frac
                u = np.frombuffer(raw, dtype=np.uint8)
                agc_peak = max(agc_peak, float(np.percentile(np.abs(u.astype(np.int16) - 127), 99.5)))
                agc_n += 1
                if agc_n >= 10:                                  # ~1 s auswerten
                    clip, peak = agc_clip / agc_n, agc_peak
                    agc_clip, agc_peak, agc_n = 0.0, 0.0, 0
                    new = self._agc_step(gain, clip, peak)
                    if new != gain:
                        # nur rtl_sdr mit neuer Stufe neu starten – Empfang, Suchlauf-Halt und RDS laufen weiter
                        self._agc_gain = gain = new
                        try:
                            self.proc.terminate(); self.proc.wait(timeout=2)
                        except Exception:
                            self.proc.kill()
                        self.proc = spawn(gain)
                        last_signal = time.time()
                        continue
            self.snr_db = round(demod.snr_db, 1)
            self.clip_pct = round(demod.clip_frac * 100, 2)
            self.level_db = demod.snr_db - 60.0        # Balken: 0 dB Abstand = leer, 50 dB = voll
            warn = ""
            if demod.clip_frac > 0.002 and not agc:
                warn = f" · ÜBERSTEUERT ({demod.clip_frac*100:.1f} %) – Verstärkung senken"
            elif agc:
                warn = f" · Verstärkung automatisch {gain:g} dB"
            self.status_text = f"Empfang {base} · {demod.snr_db:.0f} dB über Rauschen{warn}"
            self._publish(pcm)
            if hold_until_silent:
                if demod.snr_db >= (hold_thr if hold_thr is not None else 8.0):
                    last_signal = time.time()
                elif time.time() - last_signal > silence_s:
                    break
        self._kill()

    # ---------------- RTL-SDR: Suchlauf ab der eingestellten Frequenz
    SEEK_BW = {"nfm": 10000, "am": 8000, "wfm": 150000, "usb": 3000, "lsb": 3000}
    RTL_MIN, RTL_MAX = 24_000_000, 1_766_000_000

    def _channel_level(self, P, f_axis, off, bw):
        """dB über Rauschen, belegte Breite (Hz) und Seitenband-Pegel (AM) eines Kanals."""
        m = (f_axis >= off - bw / 2) & (f_axis <= off + bw / 2)
        if not m.any():
            return -99.0, 0.0, -99.0
        noise = float(np.median(P))
        seg = 10 * np.log10(P[m] / noise + 1e-20)
        lvl = float(10 * np.log10(P[m].mean() / noise + 1e-20))
        width = float((seg >= max(6.0, float(seg.max()) - 20.0)).sum() * (f_axis[1] - f_axis[0]))
        rel = np.abs(f_axis - off)
        sb = (rel >= 1500) & (rel <= 3500)
        side = float(10 * np.log10(P[sb].mean() / noise + 1e-20)) if sb.any() else -99.0
        return lvl, width, side

    def _spectrum(self, dev, center, seconds=0.15, nfft=4096):
        from scipy.signal.windows import blackmanharris
        i, q = dev._grab(int(center), seconds)
        n = len(i) // nfft
        # Blackman-Harris: kaum Leckage, damit eine starke Störlinie nicht „breit“ aussieht
        win = blackmanharris(nfft).astype(np.float32)
        z = (i[: n * nfft] + 1j * q[: n * nfft]).astype(np.complex64).reshape(n, nfft) * win
        S = np.fft.fftshift(np.fft.fft(z, axis=1), axes=1)
        P = (S.real ** 2 + S.imag ** 2).mean(axis=0)
        f = np.fft.fftshift(np.fft.fftfreq(nfft, 1.0 / dev.rate))
        return P, f

    def _is_signal(self, meas, thr, bw):
        lvl, width, side = meas
        if lvl < thr:
            return False
        if bw <= 3000:                       # SSB darf schmal sein
            return True
        if self.mode == "am":                # AM: Träger + Seitenbänder (Sprache) statt einzelner Linie
            return width > 2500 or side >= 3.0
        return width > 2500                  # FM: echte Signale sind breiter als eine Störlinie

    def _rtl_seek(self):
        from devices import RtlDevice
        sc = self.scan
        d = 1 if sc.get("dir", 1) >= 0 else -1
        bw = self.SEEK_BW.get(self.mode, 10000)
        step = float(sc.get("step", 12500))
        if 8300 <= step <= 8340:
            step = 25000.0 / 3.0                     # 8,33-kHz-Flugfunkraster exakt
        if self.mode == "wfm":
            step = max(step, 100000.0)
        thr = float(sc.get("threshold_db", 10.0))
        hold = float(sc.get("hold_s", 3.0))
        dev = RtlDevice({"sample_rate": 1024000, "rtlsdr": self.cfg.get("rtlsdr", {})})
        span = 800000 if self.mode != "wfm" else 900000
        arrow = "▲" if d > 0 else "▼"
        # wie am Funkgerät: auf das Raster einrasten, dann den nächsten Kanal in Suchrichtung nehmen
        f = round(self.freq_hz / step) * step + d * step
        idle_t = None
        while self.running and not self.restart_wanted.is_set():
            if not self.subscribers:
                idle_t = idle_t or time.time()
                if time.time() - idle_t > 90:
                    self.running = False
                    self.status_text = "beendet – kein Zuhörer mehr"
                    return
            else:
                idle_t = None
            if f > self.RTL_MAX:
                f = self.RTL_MIN
            if f < self.RTL_MIN:
                f = self.RTL_MAX
            blocked = sc.get("blocked") or []
            jumped = False
            for a, b in blocked:                          # gesperrtes Band: darüber hinwegspringen
                if a - 5000 <= f <= b + 5000:
                    # hinter das Band springen und wieder aufs Kanalraster einrasten
                    f = (math.floor((b + 5000) / step) + 1) * step if d > 0 else (math.ceil((a - 5000) / step) - 1) * step
                    jumped = True
            if jumped:
                continue
            n = max(1, int(span // step))
            chans = [int(round(f + d * k * step)) for k in range(n) if self.RTL_MIN <= f + d * k * step <= self.RTL_MAX]
            chans = [c for c in chans if not any(a - 5000 <= c <= b + 5000 for a, b in blocked)]
            if not chans and blocked:
                f = f + d * n * step
                continue
            if not chans:
                f = self.RTL_MIN if d > 0 else self.RTL_MAX
                continue
            # Mitte leicht neben das Kanalraster, damit kein Kanal auf dem Gleichanteil (DC) liegt
            center = (chans[0] + chans[-1]) / 2 + 2500
            self.active_hz = int(chans[0])
            self.status_text = f"Suchlauf {arrow} {chans[0]/1e6:.4f} MHz …"
            try:
                P, fa = self._spectrum(dev, center)
            except Exception as e:
                self.status_text = f"Suchlauf-Fehler: {e}"
                time.sleep(1)
                continue
            hit = None
            for k, c in enumerate(chans):
                meas = self._channel_level(P, fa, c - center, bw)
                if self._is_signal(meas, thr, bw):
                    # Flanke erwischt? Solange der nächste Kanal stärker ist, weiter zur Signalmitte
                    best = (c, meas[0])
                    for c2 in chans[k + 1:]:
                        m2 = self._channel_level(P, fa, c2 - center, bw)
                        if m2[0] > best[1] + 0.5:
                            best = (c2, m2[0])
                        else:
                            break
                    hit = best
                    break
            self.level_db = -120.0
            if not hit:
                f = chans[-1] + d * step
                continue
            c, lvl = hit
            self.freq_hz = int(c)
            self.status_text = f"Suchlauf {arrow}: Signal auf {c/1e6:.4f} MHz ({lvl:.0f} dB)"
            # hören, solange der Kanal belegt ist (plus Nachlauf), dann weitersuchen
            self._rtl_listen(c, hold_until_silent=True, silence_s=max(hold, 2.0), hold_thr=max(4.0, thr - 4.0))
            if not self.running or self.restart_wanted.is_set():
                return
            f = c + d * step

    # ---------------- RTL-SDR: Kanal-Suchlauf
    def _rtl_scan(self):
        from devices import RtlDevice
        scan = self.scan
        chans = sorted(int(c) for c in scan["channels_hz"])
        thr = float(scan.get("threshold_db", 10.0))
        dev = RtlDevice({"sample_rate": 1024000, "rtlsdr": self.cfg.get("rtlsdr", {})})
        # Fenster von 900 kHz ueber die Kanalliste legen
        windows = []
        cur = []
        for c in chans:
            if cur and c - cur[0] > 900000:
                windows.append(cur)
                cur = []
            cur.append(c)
        if cur:
            windows.append(cur)
        self.status_text = f"Suchlauf {scan['name']}: {len(chans)} Kanäle"
        while self.running and not self.restart_wanted.is_set():
            for w in windows:
                if not self.running or self.restart_wanted.is_set():
                    return
                center = (w[0] + w[-1]) // 2
                dev.tune(center)
                try:
                    i, q = dev.capture(0.2)
                except Exception as e:
                    self.status_text = f"Suchlauf-Fehler: {e}"
                    time.sleep(1)
                    continue
                z = i + 1j * q
                nfft = 32768
                P = np.abs(np.fft.fftshift(np.fft.fft(z[:nfft] * np.hanning(nfft)))) ** 2
                f = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / 1024000.0))
                pdb = 10 * np.log10(P + 1e-20)
                noise = float(np.median(pdb))
                best = None
                for c in w:
                    off = c - center
                    m = (f > off - 6000) & (f < off + 6000)
                    if not m.any():
                        continue
                    lvl = float(np.max(pdb[m])) - noise
                    if lvl > thr and (best is None or lvl > best[1]):
                        best = (c, lvl)
                self.level_db = -120.0
                self.active_hz = center
                if best:
                    self.status_text = f"Suchlauf {scan['name']}: aktiv {best[0]/1e6:.4f} MHz ({best[1]:.0f} dB)"
                    self._rtl_listen(best[0], hold_until_silent=True, silence_s=float(scan.get("hold_s", 3.0)))
                    if not self.running or self.restart_wanted.is_set():
                        return
                else:
                    self.status_text = f"Suchlauf {scan['name']}: suche …"
                time.sleep(0.05)

    # ---------------- PMSDR: hoeren (Python-Demodulation, zustandsbehaftet)
    def _pmsdr_init_dsp(self, rate):
        self._rate = rate
        self._phase = 0.0
        self._sos_w = ellip(8, 0.3, 70, 1500, btype="low", fs=rate, output="sos")   # Weaver-Tiefpass, steil
        self._zi_wr = sosfilt_zi(self._sos_w)
        self._zi_wi = sosfilt_zi(self._sos_w)
        self._sos_a = butter(4, [250, 3000], btype="bandpass", fs=rate, output="sos")
        self._zi_a = sosfilt_zi(self._sos_a)
        self._k0 = 0
        self._dec = max(1, rate // OUT_RATE)
        self._agc = 1.0

    def _pmsdr_process(self, z, offset):
        """Ein Block komplexes Basisband (Signal bei 'offset' Hz) -> PCM-Bytes."""
        rate = self._rate
        n = len(z)
        k = self._k0 + np.arange(n)
        self._k0 += n
        z = z * np.exp(-2j * np.pi * offset * k / rate)
        if self.mode in ("usb", "lsb"):
            side = 1.0 if self.mode == "usb" else -1.0
            w = z * np.exp(-2j * np.pi * side * 1500.0 * k / rate)
            wr, self._zi_wr = sosfilt(self._sos_w, w.real, zi=self._zi_wr)
            wi, self._zi_wi = sosfilt(self._sos_w, w.imag, zi=self._zi_wi)
            c = np.cos(2 * np.pi * 1500.0 * k / rate)
            sn = np.sin(2 * np.pi * 1500.0 * k / rate)
            audio = (wr * c - side * wi * sn) * 2.0
        elif self.mode == "am":
            audio, self._zi_a = sosfilt(self._sos_a, np.abs(z), zi=self._zi_a)
        else:  # nfm
            d = np.angle(z[1:] * np.conj(z[:-1]))
            audio, self._zi_a = sosfilt(self._sos_a, np.concatenate([[0.0], d]), zi=self._zi_a)
        audio = audio[::self._dec]
        # langsame AGC statt Normierung pro Block (sonst Pegelspruenge an Blockgrenzen)
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        target = 0.7 / max(peak, 1e-4)
        self._agc = min(target, self._agc * 1.05) if target > self._agc else target   # schnell runter, langsam rauf
        audio = audio * self._agc
        return (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()

    def _pmsdr_listen(self):
        from devices import PmsdrDevice
        if self.pmsdr is None:
            self.pmsdr = PmsdrDevice(self.cfg)
        dev = self.pmsdr
        rate = dev.rate
        self.active_hz = self.freq_hz
        center = self.freq_hz + 10000          # DC-Spike meiden
        dev.tune(center)
        self.status_text = f"Empfang {self.freq_hz/1e6:.4f} MHz ({self.mode})"
        ppm = float(self.cfg.get("frequency_correction_ppm", 0.0))
        offset = self.freq_hz - center * (1 + ppm * 1e-6)
        conj = bool(self.cfg.get("iq_conjugate", False))
        self._pmsdr_init_dsp(rate)
        cmd = ["arecord", "-q", "-D", f"plughw:{dev.card},{dev.dev}", "-f", "S16_LE", "-r", str(rate), "-c", "2", "-t", "raw"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        chunk_frames = rate // 10
        while self.running and not self.restart_wanted.is_set():
            raw = self.proc.stdout.read(chunk_frames * 4)
            if not raw:
                if self.proc.poll() is not None:
                    self.status_text = "arecord beendet"
                    break
                continue
            s = np.frombuffer(raw, dtype="<i2")
            s = s[: len(s) // 2 * 2].reshape(-1, 2).astype(np.float64) / 32768.0
            i, q = s[:, 0], s[:, 1]
            z = (i - 1j * q) if conj else (i + 1j * q)
            pcm = self._pmsdr_process(z, offset)
            try:                                         # Wasserfall: ganzes 48-kHz-Fenster der PMSDR
                self._wf_tuned = center
                self._wf_row(z, rate, center, rate * 0.9)
            except Exception:
                pass
            self._level(pcm)
            self._publish(pcm)
        self._kill()


def live_active():
    try:
        return LIVE_LOCK.exists() and (time.time() - LIVE_LOCK.stat().st_mtime) < 120
    except Exception:
        return False
