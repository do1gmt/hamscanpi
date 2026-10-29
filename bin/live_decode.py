#!/usr/bin/env python3
"""Dekoder für Live: bekommen den demodulierten Ton (24 kHz, s16 mono), den auch der Browser hört.

  FT8 / FT4   Ton im UTC-Takt (15 s / 7,5 s) sammeln, nach jedem Takt ft8_lib (wie beim Scan) – in USB auf
              der Dial-Frequenz hören (z. B. 7,074 MHz USB).
  APRS/Packet multimon-ng läuft dauerhaft mit (AFSK1200, APRS-Format mit „*“ für weitergesendete Pakete);
  POCSAG      dasselbe multimon-ng mit POCSAG 512/1200/2400 – in FM hören.
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
KINDS = ("auto", "ft8", "ft4", "packet", "pocsag", "off")
REC_MAX_S = 600


def _segment_mode(freq_hz):
    """Betriebsart des Bandplan-Segments an der Frequenz (ft8, ft4, aprs, pocsag …) oder None."""
    try:
        import bandplan as BP
        cfg = BP.load()
    except Exception:
        return None
    best = None
    for b in cfg.get("ranges", []):
        if not (b["start_hz"] - 5000 <= freq_hz <= b["stop_hz"] + 5000):
            continue
        for s in b.get("segments", []):
            for f in s.get("frequencies_hz", []):
                d = abs(freq_hz - f)
                if d <= 3000 and (best is None or d < best[0]):
                    best = (d, s.get("mode"))
    return best[1] if best else None


def auto_kind(freq_hz, mode):
    m = _segment_mode(int(freq_hz))
    if m in ("ft8", "ft4") and mode in ("usb", "lsb"):
        return m
    if m in ("aprs",) and mode in ("nfm", "am"):
        return "packet"
    if m == "pocsag" and mode == "nfm":
        return "pocsag"
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
            else:
                self._ft8_feed(t, x)

    def _switch(self, kind):
        self._stop_mm()
        self._ring.clear()
        self._ring_n = 0
        self._next_slot = None
        self.active, self.note = kind, ""
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
                self._add("APRS", aprs_tnc2(line[5:].strip()))
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
        if not msgs:
            self.note = f"{proto.upper()} Takt {stamp} UTC: keine Dekodierung"
        else:
            self.note = f"{proto.upper()} Takt {stamp} UTC: {len(msgs)} Nachrichten"
