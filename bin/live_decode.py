#!/usr/bin/env python3
"""Dekoder für Live: bekommen den demodulierten Ton (24 kHz, s16 mono), den auch der Browser hört.

  FT8 / FT4   Ton im UTC-Takt (15 s / 7,5 s) sammeln, nach jedem Takt ft8_lib (wie beim Scan) – in USB auf
              der Dial-Frequenz hören (z. B. 7,074 MHz USB).
  APRS/Packet multimon-ng läuft dauerhaft mit (AFSK1200, APRS-Format mit „*“ für weitergesendete Pakete);
  POCSAG      dasselbe multimon-ng mit POCSAG 512/1200/2400 – in FM hören.
  CW / RTTY   alle 8 s der letzte Abschnitt mit den Dekodern der Trefferauswertung (decode_recordings) –
              laufender Text; CW: stärkster Ton 300–1200 Hz, RTTY: Tonpaar im Durchlass.
  SSTV        der Ton der letzten 150 s; ist das Band 1100–2400 Hz belegt, wird alle 15 s dekodiert – das Bild
              wächst mit (reports/live_sstv_*.png), ein neues Bild ab dem nächsten VIS-Kopf.
  Stationen   FT8/FT4- und APRS-Absender kommen in die Stationsliste (Tabelle heard) und damit – wenn
              eingeschaltet – zu PSK Reporter (FT8/FT4).
  Mitschnitt  der Ton als WAV unter recordings/live/ (höchstens 10 min), zum Herunterladen.

Welcher Dekoder läuft: „auto“ nach dem Bandplan-Segment der Frequenz (ft8, ft4, aprs, pocsag) oder fest
gewählt. Die Dekoder laufen in eigenen Threads und nur, solange Live läuft; der Ton zum Browser wartet nie
auf sie (Warteschlange, im Zweifel wird ein Stück verworfen).
"""
import collections
import json
import math
import queue
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent.parent
LIVE_DIR = BASE / "recordings" / "live"
RATE = 24000
KINDS = ("auto", "ft8", "ft4", "packet", "pocsag", "cw", "rtty", "sstv", "off")
REPORTS = BASE / "reports"
DB = BASE / "data" / "scanner.db"
REC_MAX_S = 600


def _segment_mode(freq_hz):
    """Betriebsart des Bandplan-Segments an der Frequenz (ft8, ft4, aprs, pocsag …) oder None."""
    try:
        import bandplan as BP
        cfg = BP.load()
    except Exception:
        return None
    best, rng = None, None
    for b in cfg.get("ranges", []):
        if not (b["start_hz"] - 5000 <= freq_hz <= b["stop_hz"] + 5000):
            continue
        for s in b.get("segments", []):
            for f in s.get("frequencies_hz", []):
                d = abs(freq_hz - f)
                if d <= 3000 and (best is None or d < best[0]):
                    best = (d, s.get("mode"))
            for a, z in s.get("ranges", []):
                if min(a, z) <= freq_hz <= max(a, z):
                    w = abs(z - a)
                    if rng is None or w < rng[0]:
                        rng = (w, s.get("mode"))
    return best[1] if best else (rng[1] if rng else None)


def band_of(freq_hz):
    try:
        import bandplan as BP
        for b in BP.load().get("ranges", []):
            if b["start_hz"] <= freq_hz <= b["stop_hz"] and b.get("name") != "Satelliten":
                return b["name"]
    except Exception:
        pass
    return "Live"


def auto_kind(freq_hz, mode):
    m = _segment_mode(int(freq_hz))
    if m in ("ft8", "ft4") and mode in ("usb", "lsb"):
        return m
    if m in ("aprs",) and mode in ("nfm", "am"):
        return "packet"
    if m == "pocsag" and mode == "nfm":
        return "pocsag"
    if m == "cw" and mode in ("usb", "lsb"):
        return "cw"
    if m == "digital" and mode in ("usb", "lsb"):
        return "rtty"
    if m == "sstv":
        return "sstv"
    return None


class StreamResampler:
    """24 kHz -> anderer Rate blockweise ohne Sprünge an den Blockgrenzen (Überlappung wie in follow_qso)."""

    def __init__(self, rate_in, rate_out):
        from math import gcd
        g = gcd(rate_in, rate_out)
        self.up, self.down = rate_out // g, rate_in // g
        self.pad = 20 * self.down
        self.tail = None

    def __call__(self, x):
        from scipy.signal import resample_poly
        xx = x if self.tail is None else np.concatenate([self.tail, x])
        y = resample_poly(xx, self.up, self.down)
        skip = 0 if self.tail is None else len(self.tail) * self.up // self.down
        self.tail = x[-self.pad:] if len(x) >= self.pad else xx[-self.pad:]
        return y[skip:skip + len(x) * self.up // self.down]


class LiveDecoders:
    def __init__(self):
        self.kind = "auto"                    # gewählt im Dashboard
        self.active = None                    # gerade laufender Dekoder (ft8, ft4, packet, pocsag)
        self.freq_hz, self.mode = 0, "nfm"
        self.events = collections.deque(maxlen=200)
        self.seq = 0
        self.q = queue.Queue(maxsize=100)     # 100 Blöcke à 20–100 ms
        self.lock = threading.Lock()
        self.running = True
        self.note = ""
        self._mm = None
        self._mm_rs = None
        self._ring = collections.deque()      # (t_ende, samples) für FT8
        self._ring_n = 0
        self._next_slot = None
        self.rec = None                       # (wave-Datei, Pfad, Start)
        self._blk, self._blk_n, self._busy = [], 0, False         # CW/RTTY-Abschnitte, SSTV-Puffer
        self._sstv_last = 0.0
        self._sstv_img = None                                     # (Bild-Start, Datei, Ereignis-seq)
        self.recordings = []
        threading.Thread(target=self._worker, daemon=True).start()

    # ---------------- vom Live-Empfang
    def feed(self, pcm_bytes, freq_hz, mode):
        self.freq_hz, self.mode = int(freq_hz or 0), mode
        try:
            self.q.put_nowait((time.time(), pcm_bytes))
        except queue.Full:
            pass                                               # Dekoder hinkt: lieber ein Stück auslassen

    def set_kind(self, kind):
        if kind not in KINDS:
            raise ValueError("Dekoder: " + ", ".join(KINDS))
        self.kind = kind

    def stop(self):
        self.running = False
        self._stop_mm()
        self.record(False)

    # ---------------- Ergebnisse
    def _add(self, typ, text, **kw):
        with self.lock:
            self.seq += 1
            self.events.appendleft(dict(seq=self.seq, t=time.time(), type=typ, text=text,
                                        freq_hz=self.freq_hz, **kw))

    def status(self, since=0):
        with self.lock:
            ev = [e for e in self.events if e["seq"] > since][:60]
        return {"kind": self.kind, "active": self.active, "note": self.note, "seq": self.seq, "events": ev,
                "recording": bool(self.rec), "rec_s": round(time.time() - self.rec[2]) if self.rec else 0,
                "recordings": self.recordings[-5:]}

    # ---------------- Mitschnitt
    def record(self, on):
        if on and not self.rec:
            LIVE_DIR.mkdir(parents=True, exist_ok=True)
            p = LIVE_DIR / f"live_{time.strftime('%Y%m%d_%H%M%S', time.gmtime())}_{self.freq_hz / 1e6:.4f}MHz_{self.mode}.wav"
            w = wave.open(str(p), "wb")
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            self.rec = (w, p, time.time())
        elif not on and self.rec:
            w, p, t0 = self.rec
            self.rec = None
            try:
                w.close()
            except Exception:
                pass
            self.recordings.append({"name": p.name, "seconds": round(time.time() - t0), "size": p.stat().st_size})
            try:
                from applog import log
                log("INFO", "live", "live_recorded", file=p.name, seconds=round(time.time() - t0))
            except Exception:
                pass

    # ---------------- Arbeitsschleife
    def _worker(self):
        while self.running:
            try:
                t, pcm = self.q.get(timeout=0.5)
            except queue.Empty:
                self._ft8_tick(time.time())
                continue
            if self.rec:
                try:
                    self.rec[0].writeframes(pcm)
                    if time.time() - self.rec[2] > REC_MAX_S:
                        self.record(False)
                except Exception:
                    self.record(False)
            want = self.kind if self.kind != "auto" else auto_kind(self.freq_hz, self.mode)
            if want in (None, "off"):
                want = None
            if want != self.active:
                self._switch(want)
            if not self.active:
                continue
            x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
            if self.active in ("packet", "pocsag"):
                self._mm_feed(x)
            elif self.active in ("ft8", "ft4"):
                self._ft8_feed(t, x)
            else:
                self._block_feed(t, x)

    def _switch(self, kind):
        self._stop_mm()
        self._ring.clear()
        self._ring_n = 0
        self._next_slot = None
        self._blk, self._blk_n = [], 0
        self._sstv_img = None
        self.active, self.note = kind, ""
        if kind == "cw":
            self.note = "CW: alle 8 s wird der letzte Abschnitt dekodiert (stärkster Ton 300–1200 Hz)"
        elif kind == "rtty":
            self.note = "RTTY: alle 8 s wird der letzte Abschnitt dekodiert (45,45/50/75 Bd)"
        elif kind == "sstv":
            self.note = "SSTV: wartet auf ein Bild – es baut sich alle 15 s weiter auf"
        if kind in ("packet", "pocsag"):
            self._start_mm(kind)
        elif kind in ("ft8", "ft4"):
            import ft8_watch as FW
            try:
                cfg = json.loads((BASE / "config" / "scanner.json").read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
            self._decoder = Path(cfg.get("ft8_decoder", str(FW.DEFAULT_DECODER))).expanduser()
            if not self._decoder.exists():
                self.note = f"FT8-Dekoder fehlt ({self._decoder}) – siehe System → Hinweise"
            else:
                self.note = f"{kind.upper()}: Ton im {FW.SLOT_S[kind]:g}-s-Takt (UTC), Dekodierung nach jedem Takt"

    # ---------------- multimon-ng (APRS/Packet, POCSAG)
    def _start_mm(self, kind):
        mm = shutil.which("multimon-ng")
        if not mm:
            self.note = "multimon-ng fehlt (sudo apt install multimon-ng)"
            return
        args = [mm, "-t", "raw", "-q"] + (["-a", "AFSK1200", "-A"] if kind == "packet" else
                                          ["-a", "POCSAG512", "-a", "POCSAG1200", "-a", "POCSAG2400"]) + ["-"]
        try:
            self._mm = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=False, bufsize=0)
        except Exception as e:
            self.note = f"multimon-ng startet nicht: {e}"
            return
        self._mm_rs = StreamResampler(RATE, 22050)
        self.note = "APRS/Packet 1200 Bd: jedes Paket erscheint sofort" if kind == "packet" else \
                    "POCSAG 512/1200/2400: Meldungen erscheinen sofort"
        threading.Thread(target=self._mm_read, args=(self._mm,), daemon=True).start()

    def _mm_feed(self, x):
        p = self._mm
        if not p or p.poll() is not None:
            return
        y = np.clip(self._mm_rs(x) * 0.9, -1, 1)
        try:
            p.stdin.write((y * 32767).astype("<i2").tobytes())
        except Exception:
            self._stop_mm()

    def _mm_read(self, p):
        from sat_pass import aprs_tnc2, aprs_text
        pend = None
        for raw in iter(p.stdout.readline, b""):
            line = raw.decode(errors="replace").strip()
            if line.startswith("APRS:"):
                txt = aprs_tnc2(line[5:].strip())
                self._add("APRS", txt)
                try:                                                  # Absender mit Position in die Stationsliste
                    import stations as ST
                    self._heard([(c, g, None, t_, "APRS", self.freq_hz) for c, g, _s, t_ in ST.aprs_senders(txt)],
                                time.time())
                except Exception:
                    pass
            elif line.startswith("AFSK1200:"):
                pend = line.split(":", 1)[1].strip()               # Nutzlast folgt in der nächsten Zeile
            elif pend is not None:
                self._add("Packet", aprs_text(pend, line))
                pend = None
            elif line.startswith("POCSAG"):
                self._add("POCSAG", line.split(":", 1)[1].strip() if ":" in line else line)

    def _stop_mm(self):
        p, self._mm = self._mm, None
        if p:
            try:
                p.stdin.close()
            except Exception:
                pass
            try:
                p.terminate()
                p.wait(timeout=2)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    # ---------------- Stationsliste (Tabelle heard): peak_id negativ = aus Live (ein Eintrag je Takt/Paket)
    def _heard(self, rows, t):
        if not rows:
            return
        import sqlite3
        from datetime import datetime, timezone
        ts = datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")
        band = band_of(self.freq_hz)
        try:
            db = sqlite3.connect(DB, timeout=10)
            try:
                db.execute("""CREATE TABLE IF NOT EXISTS heard (
                    id INTEGER PRIMARY KEY, peak_id INTEGER, time TEXT, call TEXT, band TEXT, freq_hz REAL,
                    mode TEXT, grid TEXT, snr REAL, info TEXT, UNIQUE(peak_id, call))""")   # wie stations.py
                for call, grid, snr, text, typ, f in rows:
                    db.execute("""INSERT OR IGNORE INTO heard (peak_id, time, call, band, freq_hz, mode, grid, snr, info)
                                  VALUES (?,?,?,?,?,?,?,?,?)""",
                               (-int(t), ts, call, band, float(f or self.freq_hz), typ, grid, snr,
                                ("Live: " + str(text or ""))[:200]))
                db.commit()
            finally:
                db.close()
        except Exception as e:
            try:
                from applog import log
                log("WARN", "live", "live_heard_failed", error=f"{type(e).__name__}: {e}")
            except Exception:
                pass

    # ---------------- CW / RTTY (Abschnitte) und SSTV (wachsendes Bild)
    def _block_feed(self, t, x):
        self._blk.append(x)
        self._blk_n += len(x)
        if self.active == "sstv":
            while self._blk_n > RATE * 150 and len(self._blk) > 1:
                self._blk_n -= len(self._blk.pop(0))
            if t - self._sstv_last >= 15 and not self._busy:
                self._sstv_last = t
                a = np.concatenate(self._blk)
                self._busy = True
                threading.Thread(target=self._sstv_run, args=(a, t), daemon=True).start()
            return
        if self._blk_n < RATE * 8 or self._busy:
            if self._blk_n > RATE * 30:                               # Dekoder zu langsam: ältestes verwerfen
                self._blk, self._blk_n = self._blk[-50:], sum(len(b) for b in self._blk[-50:])
            return
        a = np.concatenate(self._blk)
        cut = len(a)
        if self.active == "cw":
            # an einer Pause schneiden (zwischen Wörtern), damit kein Zeichen zerteilt wird; Rest bleibt
            k = int(RATE * 0.02)
            env = np.abs(a[: len(a) // k * k]).reshape(-1, k).max(axis=1)
            thr = 0.2 * float(np.percentile(env, 95))
            quiet = env < thr
            best = None
            i = len(quiet) - 1
            while i > int(5 / 0.02):
                if quiet[i]:
                    j = i
                    while j > 0 and quiet[j - 1]:
                        j -= 1
                    if (i - j + 1) * 0.02 >= 0.25:
                        best = (i + j) // 2
                        break
                    i = j - 1
                else:
                    i -= 1
            if best is not None:
                cut = best * k
        rest = a[cut:]
        self._blk, self._blk_n = ([rest], len(rest)) if len(rest) else ([], 0)
        self._busy = True
        threading.Thread(target=self._text_run, args=(a[:cut], self.active), daemon=True).start()

    def _text_run(self, a, kind):
        try:
            from scipy.signal import resample_poly, hilbert
            import decode_recordings as DR
            r = 8000
            y = resample_poly(a.astype(np.float64), 1, 3)
            if np.max(np.abs(y)) < 1e-4:
                return
            n = np.arange(len(y)) / r
            if kind == "cw":                                          # stärkster Ton 300–1200 Hz -> 0 Hz
                S = np.abs(np.fft.rfft(y * np.hanning(len(y))))
                f = np.fft.rfftfreq(len(y), 1 / r)
                m = (f >= 300) & (f <= 1200)
                f0 = float(f[m][np.argmax(S[m])])
                res = DR.decode_cw(y * np.exp(-2j * np.pi * f0 * n), r)
            else:                                                     # RTTY: Durchlass um 1500 Hz -> 0 Hz
                z = hilbert(y) * np.exp(-2j * np.pi * 1500.0 * n)
                env = np.convolve(np.abs(z), np.ones(160) / 160, mode="same")
                on = np.flatnonzero(env > 0.3 * float(np.percentile(env, 95)))
                if len(on) < r:                                       # weniger als 1 s Signal
                    return
                z = z[max(0, on[0] - 80):on[-1] + 80]                 # nur der Teil mit Signal (sonst Zeichensalat)
                res = DR.decode_rtty(z, r)
            if res and res.get("text") and int(res.get("confidence", 100)) >= 60:   # Rauschen ergibt „??H??“
                self._add(res["type"], res["text"], detail=res.get("detail"))
        except Exception as e:
            self.note = f"{kind.upper()}-Dekoder: {type(e).__name__}: {e}"
        finally:
            self._busy = False

    def _sstv_run(self, a, t):
        try:
            band = a[-RATE * 15:]
            S = np.abs(np.fft.rfft(band * np.hanning(len(band)))) ** 2
            f = np.fft.rfftfreq(len(band), 1 / RATE)
            share = float(S[(f >= 1100) & (f <= 2400)].sum() / max(S[(f >= 200) & (f <= 4000)].sum(), 1e-12))
            if share < 0.55 and not self._sstv_img:
                return                                                # kein SSTV-Ton: nichts tun
            import sstv_decode as SD
            imgs = SD.decode_all(a, RATE, min_lines=0.05, auto_offset=True)
            if not imgs:
                return
            im = imgs[-1]
            start = t - (len(a) / RATE) + im["start_s"]
            if not self._sstv_img or abs(self._sstv_img[0] - start) > 5:  # neues Bild
                name = f"live_sstv_{time.strftime('%Y%m%d_%H%M%S', time.gmtime(start))}.png"
                self._sstv_img = (start, name, None)
            name = self._sstv_img[1]
            REPORTS.mkdir(parents=True, exist_ok=True)
            SD.save_png(im["image"], REPORTS / name)
            text, img = f"{im['mode']} – {round(im['complete'] * 100)} % der Zeilen", f"/files/{name}?v={int(t)}"
            ev = None
            with self.lock:
                if self._sstv_img[2] is not None:                      # dasselbe Bild: Eintrag auffrischen
                    ev = next((e for e in self.events if e["seq"] == self._sstv_img[2]), None)
                if ev:
                    ev.update(text=text, img=img, t=time.time())
                    self.seq += 1
            if not ev:
                self._add("SSTV", text, img=img)
                self._sstv_img = (self._sstv_img[0], name, self.seq)
        except Exception as e:
            self.note = f"SSTV-Dekoder: {type(e).__name__}: {e}"
        finally:
            self._busy = False

    # ---------------- FT8 / FT4
    def _ft8_feed(self, t_end, x):
        self._ring.append((t_end, x))
        self._ring_n += len(x)
        while self._ring_n > RATE * 20 and self._ring:            # 20 s reichen für einen Takt
            self._ring_n -= len(self._ring.popleft()[1])
        self._ft8_tick(t_end)

    def _ft8_tick(self, now):
        if self.active not in ("ft8", "ft4") or not self._ring:
            return
        import ft8_watch as FW
        slot = FW.SLOT_S[self.active]
        if self._next_slot is None:
            self._next_slot = math.ceil(now / slot) * slot             # erster voller Takt
            return
        T = self._next_slot
        if now < T + slot + 0.4:                                      # Takt noch nicht ganz im Puffer
            return
        self._next_slot = T + slot
        # Abtastwerte des Takts aus dem Puffer: jeder Block endet zu seiner Ankunftszeit
        parts = []
        for t_end, x in self._ring:
            t0 = t_end - len(x) / RATE
            if t_end <= T or t0 >= T + slot:
                continue
            a = max(0, int(round((T - t0) * RATE)))
            b = min(len(x), int(round((T + slot - t0) * RATE)))
            parts.append(x[a:b])
        if not parts:
            return
        audio = np.concatenate(parts)
        if len(audio) < 0.8 * slot * RATE:
            return                                                    # Lücke (Neustart, Abstimmen)
        if not getattr(self, "_decoder", None) or not self._decoder.exists():
            return
        proto, dial = self.active, self.freq_hz
        from scipy.signal import resample_poly
        threading.Thread(target=self._ft8_run, args=(resample_poly(audio, 1, 2).astype(np.float32), proto, T, dial),
                         daemon=True).start()

    def _ft8_run(self, audio12, proto, T, dial):
        import ft8_watch as FW
        try:
            msgs = FW.decode(audio12, proto, self._decoder)
        except Exception as e:
            self.note = f"{proto.upper()}-Dekoder: {e}"
            return
        stamp = time.strftime("%H:%M:%S", time.gmtime(T))
        for m in sorted(msgs, key=lambda m: -m["snr"]):
            self._add(proto.upper(), m["text"], snr=round(m["snr"]), audio_hz=m["audio_hz"], slot=stamp,
                      rf_hz=dial + m["audio_hz"])
        try:                                                          # Stationsliste und PSK Reporter
            import stations as ST
            self._heard([(c, g, sn, txt, proto.upper(), dial + (ah or 0))
                         for c, g, sn, txt, ah in ST.ft8_senders(msgs)], T)
        except Exception:
            pass
        if not msgs:
            self.note = f"{proto.upper()} Takt {stamp} UTC: keine Dekodierung"
        else:
            self.note = f"{proto.upper()} Takt {stamp} UTC: {len(msgs)} Nachrichten"
