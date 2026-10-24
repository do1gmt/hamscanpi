#!/usr/bin/env python3
"""
Einen Satellitenüberflug mit dem RTL-SDR aufnehmen (vom Scheduler unter der Scan-Sperre gestartet).

  rtl_sdr 960 kS/s auf eine Mittenfrequenz, die alle Kanäle der Gruppe abdeckt
  -> je Kanal ein Mischer, der dem Doppler folgt (alle 0,1 s aus der Bahn, phasenstetig)
  -> Tiefpass, ÷20 -> 48 kHz I/Q (Kanal bei 0 Hz, Doppler ist heraus)

Mehrere Kanäle gleichzeitig, wenn sie in ein Fenster passen (ISS 437,550 SSTV + 437,800 Relais,
ISS 145,800 + 145,825 APRS).

Lineare Transponder: 30-s-Abschnitte mit SSB-Aktivität werden Treffer (Abstimmen/DNR wie Kurzwelle).
FM-Kanäle: Sprachdurchgänge werden Treffer; SSTV wird gesucht und dekodiert (ISS);
APRS (145,825) wird mit multimon-ng dekodiert, alle Pakete stehen auf einer Trefferkarte.

Nach der Aufnahme gibt das Programm den Stick sofort frei (die Auswertung läuft in einem
abgetrennten Prozess weiter), damit ein direkt folgender Überflug oder Scan starten kann.

Aufruf: sat_pass.py --sats iss_sstv70,iss_rpt --until UNIXZEIT [--key SCHLÜSSEL]
        sat_pass.py --sats iss --test DATEI --start UNIXZEIT     (Prüfung mit uint8-I/Q-Datei)
"""
import argparse
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi, resample_poly

sys.path.insert(0, str(Path(__file__).resolve().parent))
import satellites as SAT                                  # noqa: E402
from applog import log as applog                          # noqa: E402

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "scanner.db"
RECDIR = BASE / "recordings"
REPORTS = BASE / "reports"
STATUS = BASE / "data" / "runtime_status.json"
IN_RATE, OUT_RATE = 960000, 48000
BLOCK = IN_RATE // 10                                     # 0,1 s
START_ISO = None
REC_PID = BASE / "data" / "sat_rec.pid"        # PID der laufenden Aufnahme (zum Abbrechen aus dem Dashboard)
_stop = {"flag": False}


def _on_term(signum, frame):
    """Abbruch (Dashboard): Aufnahme beenden, Stick freigeben, bisher Aufgenommenes auswerten."""
    _stop["flag"] = True


def status(state, msg, progress, only_if_mine=False):
    if only_if_mine:
        try:                                              # nach der Freigabe: keinen Scan überschreiben
            if json.loads(STATUS.read_text(encoding="utf-8")).get("phase") != "satellite":
                return
        except Exception:
            pass
    tmp = STATUS.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps({"state": state, "phase": "satellite", "progress": int(progress),
                               "message": msg, "run_id": None, "started_at": START_ISO,
                               "updated_at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    os.replace(tmp, STATUS)


def write_iq(path, z, rate):
    z = np.asarray(z)
    pk = float(np.max(np.abs(z))) if len(z) else 1.0
    pk = pk or 1.0
    inter = np.empty(len(z) * 2, dtype="<i2")
    inter[0::2] = np.clip(z.real / pk * 0.8 * 32767, -32768, 32767)
    inter[1::2] = np.clip(z.imag / pk * 0.8 * 32767, -32768, 32767)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(int(rate))
        w.writeframes(inter.tobytes())


def linear_activity(z, rate, beacons=()):
    """SSB-Aktivität in einem Abschnitt: (aktiv?, Versatz der stärksten Station in Hz, Pegel dB)."""
    nf = 1024
    n = len(z) // nf
    if n < 20:
        return False, 0.0, 0.0
    P = np.abs(np.fft.fftshift(np.fft.fft(z[: n * nf].reshape(n, nf) * np.hanning(nf), axis=1), axes=1)) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(nf, 1.0 / rate))
    per_bin = np.median(P, axis=0)
    floor = float(np.percentile(per_bin, 25))
    line = 10 * np.log10(per_bin / floor + 1e-12) > 8.0             # Baken, Träger ausblenden
    line = np.convolve(line.astype(float), np.ones(5), mode="same") > 0
    for bo in beacons:                                                # Baken sind keine Aktivität
        line |= np.abs(f - bo) < 4000
    P[:, line | (np.abs(f) > rate / 2 * 0.92)] = floor
    box = int(2400 / (rate / nf))
    best = (0.0, 0.0, 0.0)
    for b0 in range(0, nf - box, box // 4):
        ch = 10 * np.log10(P[:, b0:b0 + box].mean(axis=1) / floor + 1e-12)
        act = np.convolve(ch, np.ones(3) / 3, mode="same") >= 6.0
        frac = float(act.mean())
        if frac > best[0]:
            best = (frac, float(f[b0 + box // 2]), float(np.median(ch[act])) if act.any() else 0.0)
    return best[0] >= 0.05, best[1], best[2]


def db_save(sat, hits):
    if not hits:
        return 0
    db = sqlite3.connect(DB)
    db.execute("PRAGMA busy_timeout=5000")
    # frische Installation: Tabellen anlegen (sonst macht das erst der Webserver bzw. der erste Scan)
    db.execute("""CREATE TABLE IF NOT EXISTS scan (id INTEGER PRIMARY KEY, time TEXT NOT NULL, band TEXT NOT NULL,
        center_hz INTEGER NOT NULL, noise_db REAL NOT NULL, rms_i REAL NOT NULL, rms_q REAL NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS peak (id INTEGER PRIMARY KEY, scan_id INTEGER NOT NULL,
        rf_hz REAL NOT NULL, offset_hz REAL NOT NULL, level_db REAL NOT NULL, snr_db REAL NOT NULL,
        recording_path TEXT, protected INTEGER NOT NULL DEFAULT 0, segment_id TEXT, mode TEXT, sideband TEXT,
        carrier_delta_hz REAL, note TEXT, rec_center_hz REAL, audio TEXT, audio_score REAL)""")
    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute("INSERT INTO scan (time, band, center_hz, noise_db, rms_i, rms_q) VALUES (?,?,?,?,?,?)",
                     (now, "Satelliten", int(sat["center_hz"]), 0.0, 0.0, 0.0))
    sid = cur.lastrowid
    cols = {r[1] for r in db.execute("PRAGMA table_info(peak)")}
    for h in hits:
        info = {"type": h.get("dec_type") or ("SSTV" if h["mode"] == "sstv" else "Satellit"), "confidence": 100,
                "validated": True, "text": h.get("note", ""), "detail": sat["label"]}
        (REPORTS / f"{Path(h['path']).stem}_decode.json").write_text(
            json.dumps({"band": "Satelliten", "frequency_hz": h["rf_hz"], "decoders": [info]}, ensure_ascii=False),
            encoding="utf-8")
        rec = {"scan_id": sid, "rf_hz": h["rf_hz"], "offset_hz": h["rf_hz"] - sat["center_hz"],
               "level_db": h.get("snr_db", 0.0), "snr_db": h.get("snr_db", 0.0),
               "recording_path": h["path"], "mode": h["mode"], "sideband": h.get("sideband"),
               "rec_center_hz": sat["center_hz"], "segment_id": sat["id"], "audio": h.get("audio")}
        rec = {k: v for k, v in rec.items() if k in cols}
        db.execute(f"INSERT INTO peak ({','.join(rec)}) VALUES ({','.join('?' * len(rec))})", list(rec.values()))
    db.commit()
    db.close()
    return len(hits)


def stem_for(sat, t):
    return time.strftime("%Y%m%d_%H%M%S", time.gmtime(t)) + f"_Satelliten_{sat['id']}"


class Source:
    """Rohdaten vom Stick (oder aus einer Testdatei: uint8-I/Q mit 960 kS/s)."""

    def __init__(self, tune_hz, cfg, test=None):
        self.test = test
        if test:
            self.f = open(test, "rb")
            return
        rtl = cfg.get("rtlsdr", {})
        cmd = [rtl.get("binary", "rtl_sdr"), "-d", str(int(rtl.get("device_index", 0))), "-f", str(int(tune_hz)),
               "-s", str(IN_RATE), "-p", str(int(rtl.get("ppm", 0)))]
        if rtl.get("gain"):
            cmd += ["-g", str(rtl["gain"])]
        cmd.append("-")
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.f = self.p.stdout

    def read(self):
        b = self.f.read(BLOCK * 2)
        return b if len(b) == BLOCK * 2 else None

    def close(self):
        if self.test:
            self.f.close()
            return
        try:
            self.p.send_signal(signal.SIGINT)
            self.p.wait(timeout=3)
        except Exception:
            self.p.kill()
        try:
            self.f.close()                                    # Rohr zu rtl_sdr schließen, bevor detach() die
        except Exception:                                     # Dateinummern schließt (sonst „Bad file descriptor“)
            pass


def signal_stats(pw, step_s=0.1):
    """Kurzfassung des Kanalpegels über den Überflug (je 0,1 s gemessen, Abstand zum Rauschen):
    höchster Wert, 90-%-Wert und Sekunden über 6 dB. Zeigt, ob der Satellit überhaupt zu hören war."""
    if pw is None or not len(pw):
        return {"max_db": None, "p90_db": None, "above6_s": 0}
    db = 10 * np.log10(np.asarray(pw, dtype=np.float64) + 1e-12)
    return {"max_db": round(float(db.max()), 1), "p90_db": round(float(np.percentile(db, 90)), 1),
            "above6_s": round(float((db > 6.0).sum()) * step_s)}


class Channel:
    """Ein Kanal in der gemeinsamen Aufnahme: Mischer mit Doppler, 48 kHz, Auswertung."""

    def __init__(self, sat, tune_hz, tracker, tag):
        self.sat, self.tr = sat, tracker
        self.f0 = float(sat["center_hz"])
        self.base_off = self.f0 - tune_hz
        # Stufe 1 (960 kS/s): fester Mischer aus einer Tabelle (kein exp je Block), Tiefpass 32 kHz, ÷10
        # Stufe 2 (96 kS/s): Doppler-Mischer, Tiefpass 21 kHz, ÷2 -> 48 kHz. Spart am Pi rund die Hälfte.
        n = np.arange(BLOCK)
        self.tab = np.exp(-2j * np.pi * self.base_off * n / IN_RATE).astype(np.complex64)
        self.tab_step = np.exp(-2j * np.pi * self.base_off * BLOCK / IN_RATE)
        self.rot = 1.0 + 0j
        # lineare Satelliten: Stufe 1 breiter, damit die Bake neben dem Transponder mit hineinpasst
        self.beacon_offs = [b - self.f0 for b in sat.get("beacons", []) if abs(b - self.f0) < 40000]
        self.lp = butter(6, 42000 if self.beacon_offs else 32000, fs=IN_RATE, output="sos")
        self.zi = (sosfilt_zi(self.lp) * 0).astype(np.complex128)
        self.lp2 = butter(6, 21000, fs=IN_RATE // 10, output="sos")
        self.zi2 = (sosfilt_zi(self.lp2) * 0).astype(np.complex128)
        self.n2 = np.arange(BLOCK // 10)
        self.phase = 0.0
        self.kind = sat["kind"]
        self.tmp = RECDIR / f".sat_{sat['id']}_{tag}.raw"
        self.tmpf = open(self.tmp, "wb") if self.kind == "fm" else None
        self.chunk, self.chunk_t = [], None
        self.hits = []
        self.audio_parts, self.snr_track = [], []
        self.ch_lp = butter(6, 9000, fs=OUT_RATE, output="sos")
        self.ch_zi = (sosfilt_zi(self.ch_lp) * 0).astype(np.complex128)
        self.fm_last = 0j
        self.n = np.arange(BLOCK)

    def feed(self, z, t):
        y = z * (self.tab * self.rot)
        self.rot *= self.tab_step
        self.rot /= abs(self.rot)                             # Rundungsfehler nicht aufsummieren
        y, self.zi = sosfilt(self.lp, y, zi=self.zi)
        y = y[::10]                                           # 96 kHz
        dop = self.tr.doppler_hz(self.f0, t + 0.05)
        inc = 2 * np.pi * dop / (IN_RATE / 10)
        y = y * np.exp(-1j * (self.phase + inc * self.n2))
        self.phase = float((self.phase + inc * len(self.n2)) % (2 * np.pi))
        y96 = y                                               # 96 kHz, Doppler korrigiert (für die Bakenmessung)
        y, self.zi2 = sosfilt(self.lp2, y, zi=self.zi2)
        bb = y[::2]                                           # 48 kHz, Kanal bei 0 Hz
        if self.chunk_t is None:
            self.chunk_t = t
        if self.kind == "linear":
            # Pegel für die Signalprüfung: stärkste Spektrallinie (Bake, SSB-Station) gegen den Median,
            # über 1 s gemittelt; reines Rauschen ergibt dabei ~4 dB, das wird abgezogen
            # gemessen bei 96 kHz: Transponder (±20 kHz) und die Bake(n) (±2,5 kHz), Rauschen aus ±40 kHz
            n = min(len(y96), 8192)
            S_ = np.abs(np.fft.fftshift(np.fft.fft(y96[:n] * np.hanning(n)))) ** 2
            fr = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / (IN_RATE // 10)))
            near = np.abs(fr) < 20000
            for bo in self.beacon_offs:
                near |= np.abs(fr - bo) < 2500
            S_ = np.where(near, S_, 0.0)[np.abs(fr) < 40000] if self.beacon_offs else S_[near]
            self._lin_acc = S_ if getattr(self, "_lin_acc", None) is None or len(self._lin_acc) != len(S_) else self._lin_acc + S_
            self._lin_n = getattr(self, "_lin_n", 0) + 1
            if self._lin_n >= 10:
                acc = self._lin_acc
                med = np.median(acc[acc > 0]) if np.any(acc > 0) else 1e-30
                ratio = float(acc.max() / max(med, 1e-30)) / 10 ** 0.42
                self.snr_track.append(max(ratio, 1e-3))
                self._lin_acc, self._lin_n = None, 0
            self.chunk.append(bb.astype(np.complex64))
            if len(self.chunk) >= 300:                        # 30 s
                self._linear_chunk()
                self.chunk, self.chunk_t = [], t + 0.1
            return
        c, self.ch_zi = sosfilt(self.ch_lp, bb, zi=self.ch_zi)
        c24 = c[::2]
        self.tmpf.write(np.stack([c24.real, c24.imag], 1).astype(np.float32).tobytes())
        prev = np.concatenate(([self.fm_last], c[:-1]))
        self.fm_last = c[-1]
        d = np.angle(c * np.conj(prev)) * OUT_RATE / (2 * np.pi) / 5000.0
        self.audio_parts.append(resample_poly(d, 1, 2).astype(np.float32))
        # Rauschabstand: Kanalleistung gegen das Rauschen 14–22 kHz daneben
        S_ = np.abs(np.fft.fft(bb[:4096] * np.hanning(4096))) ** 2
        fr = np.abs(np.fft.fftfreq(4096, 1.0 / OUT_RATE))
        dens = float(np.median(S_[(fr > 14000) & (fr < 22000)])) / 0.693 / np.sum(np.hanning(4096) ** 2)
        pw = float(np.mean(np.abs(c) ** 2))
        self.snr_track.append(pw / max(dens * 18000.0 / OUT_RATE, 1e-20))

    def _linear_chunk(self):
        zc = np.concatenate(self.chunk)
        act, off_hz, lvl = linear_activity(zc, OUT_RATE, self.beacon_offs)
        if act:
            p = RECDIR / (stem_for(self.sat, self.chunk_t) + ".wav")
            write_iq(p, zc, OUT_RATE)
            rf = self.f0 + off_hz - 1500.0                    # USB: Sprache 0,3–2,7 kHz über dem Träger
            self.hits.append({"rf_hz": rf, "path": str(p), "mode": "voice", "sideband": "USB",
                              "snr_db": lvl, "note": f"{self.sat['name']} Transponder"})
            print(f"   {time.strftime('%H:%M:%S', time.gmtime(self.chunk_t))}  {self.sat['id']}: "
                  f"Aktivität ~{rf/1e6:.4f} MHz ({lvl:.0f} dB)")

    def close_recording(self):
        if self.kind == "linear" and len(self.chunk) >= 100:  # Rest ab 10 s auch auswerten
            self._linear_chunk()
        self.chunk = []
        if self.tmpf:
            self.tmpf.close()
            self.tmpf = None

    # ------------------------------------------------ Auswertung (nach Freigabe des Sticks)
    def evaluate(self, t0):
        sat = self.sat
        if self.kind != "fm" or not self.audio_parts:
            self.tmp.unlink(missing_ok=True)
            return self.hits
        # zusammensetzen und dabei die Teilstücke freigeben (sonst liegt der Ton kurz doppelt im Speicher)
        parts = self.audio_parts
        self.audio_parts = []
        audio = np.empty(sum(len(x) for x in parts), dtype=np.float32)
        end = len(audio)
        while parts:
            x = parts.pop()
            audio[end - len(x):end] = x
            end -= len(x)
        del parts
        raw_iq = np.memmap(self.tmp, dtype=np.float32, mode="r").reshape(-1, 2)

        def iq(a, b):
            x = np.asarray(raw_iq[a:b])
            return x[:, 0] + 1j * x[:, 1]
        n_iq = len(raw_iq)
        pw_all = np.array(self.snr_track)
        used = np.zeros(len(pw_all) + 1, bool)                 # je 0,1 s (statt je Abtastwert: 20 min = 29 MB)
        self.stats = signal_stats(pw_all)
        if sat.get("aprs"):                                      # APRS braucht den Ton – vor SSTV, das ihn freigibt
            self._aprs(audio, iq, n_iq, t0, pw_all)
        if sat.get("sstv"):
            import sstv_decode as SD
            box = [audio]
            del audio                                            # decode_all gibt den Ton nach der Tonanalyse frei
            for k, img in enumerate(SD.decode_all(box, 24000, headless=True)):
                s0 = int(max(0, img["start_s"] - 1) * 24000)
                dur = SD.MODES[[v for v, m in SD.MODES.items() if m["name"] == img["mode"]][0]]
                T = sum(d for _, d in dur["segs"]) / 1000.0 * dur["height"] / dur.get("lines_per", 1)
                s1 = min(n_iq, int((img["start_s"] + T * img["complete"] + 1) * 24000))
                stem = stem_for(sat, t0 + s0 / 24000)
                p = RECDIR / f"{stem}_{k}.wav"
                write_iq(p, iq(s0, s1), 24000)
                SD.save_png(img["image"], REPORTS / f"{stem}_{k}_sstv.png")
                used[int(s0 / 2400):int(s1 / 2400) + 1] = True
                seg_pw = pw_all[int(s0 / 2400):max(int(s0 / 2400) + 1, int(s1 / 2400))]
                snr_img = float(10 * np.log10(np.mean(seg_pw) + 1e-12)) if len(seg_pw) else 0.0
                self.hits.append({"rf_hz": self.f0, "path": str(p), "mode": "sstv", "sideband": None, "snr_db": snr_img,
                                  "audio": "ton", "note": f"SSTV {img['mode']} ({sat['name']}), "
                                                          f"{img['complete']*100:.0f} % der Zeilen"})
                print(f"   SSTV {img['mode']}: Bild gespeichert ({img['complete']*100:.0f} % der Zeilen)")
        else:
            del audio
        # Sprachdurchgänge (Kanal deutlich über dem Rauschen, ≥ 3 s, nicht schon SSTV)
        pw = pw_all
        if len(pw) and not sat.get("aprs"):
            on = 10 * np.log10(pw + 1e-12) > 6.0
            k = 0
            while k < len(on):
                if on[k]:
                    e = k
                    while e < len(on) and on[e:e + 20].any():
                        e += 1
                    a0, a1 = int(k * 0.1 * 24000), int(e * 0.1 * 24000)
                    if e - k >= 30 and not used[k:e].mean() > 0.5:
                        p = RECDIR / (stem_for(sat, t0 + k * 0.1) + "_fm.wav")
                        write_iq(p, iq(a0, a1), 24000)
                        self.hits.append({"rf_hz": self.f0, "path": str(p), "mode": "fm", "sideband": None,
                                          "snr_db": float(10 * np.log10(pw[k:e].mean())),
                                          "note": f"{sat['name']} FM, {(e - k) / 10:.0f} s"})
                    k = e
                k += 1
        del raw_iq
        self.tmp.unlink(missing_ok=True)
        return self.hits

    def _aprs(self, audio, iq, n_iq, t0, pw_all):
        mm = shutil.which("multimon-ng")
        if not mm:
            print("   APRS: multimon-ng fehlt (sudo apt install multimon-ng)")
            return
        a = resample_poly(audio, 147, 160)                    # 24000 -> 22050 Hz
        a = np.clip(a / (np.max(np.abs(a)) + 1e-9) * 0.9, -1, 1)
        win, hop = int(12 * 22050), int(10 * 22050)
        packets, first = [], {}
        with tempfile.TemporaryDirectory() as td:
            for i0 in range(0, max(1, len(a) - int(2 * 22050)), hop):
                raw = Path(td) / "a.raw"
                (a[i0:i0 + win] * 32767).astype("<i2").tofile(raw)
                try:
                    r = subprocess.run([mm, "-t", "raw", "-a", "AFSK1200", "-q", str(raw)],
                                       capture_output=True, text=True, timeout=60)
                except Exception:
                    continue
                lines = r.stdout.splitlines()
                for j, l in enumerate(lines):
                    l = l.strip()
                    if not l.startswith("AFSK1200:"):
                        continue
                    txt = aprs_text(l.split(":", 1)[1].strip(),
                                    lines[j + 1].strip() if j + 1 < len(lines) and not lines[j + 1].startswith("AFSK1200:") else "")
                    if txt and txt not in first:
                        first[txt] = i0 / 22050
                        packets.append(txt)
        if not packets:
            return
        ts = sorted(first.values())
        s0 = int(max(0, ts[0] - 1) * 24000)
        s1 = min(n_iq, int((ts[-1] + 13) * 24000), s0 + 120 * 24000)   # höchstens 2 min Aufnahme
        p = RECDIR / (stem_for(self.sat, t0 + s0 / 24000) + "_aprs.wav")
        write_iq(p, iq(s0, s1), 24000)
        seg = pw_all[int(s0 / 2400):max(int(s0 / 2400) + 1, int(s1 / 2400))] if len(pw_all) else pw_all
        self.hits.append({"rf_hz": self.f0, "path": str(p), "mode": "aprs", "sideband": None,
                          "snr_db": float(10 * np.log10(np.mean(seg) + 1e-12)) if len(seg) else 0.0,
                          "dec_type": "APRS", "note": "\n".join(packets[:60])})
        print(f"   APRS: {len(packets)} Pakete")


def aprs_text(head, payload):
    """multimon-ng: 'fm DL1ABC-0 to APRS-0 via ARISS-0 UI  pid=F0' + Nutzlast -> lesbare Zeile."""
    import re
    m = re.match(r"fm (\S+) to (\S+)(?: via (\S+))?", head)
    if not m:
        return (head + " " + payload).strip()
    cl = lambda c: re.sub(r"-0$", "", c or "")
    via = f" über {','.join(cl(x) for x in m.group(3).split(','))}" if m.group(3) else ""
    return f"{cl(m.group(1))} → {cl(m.group(2))}{via}: {payload}".strip()


def detach():
    """Stick ist zu: Elternprozess beendet sich (gibt die Scan-Sperre frei), Kind wertet aus."""
    try:
        pid = os.fork()
    except OSError:
        return
    if pid > 0:
        os._exit(0)
    os.setsid()
    os.closerange(3, 4096)                                    # auch die geerbte Sperrdatei schließen
    try:
        os.nice(10)                                           # Auswertung stört den nächsten Überflug nicht
    except OSError:
        pass


def main():
    global START_ISO
    ap = argparse.ArgumentParser()
    ap.add_argument("--sats", help="Kanäle, mit Komma getrennt (auch verschiedener Satelliten)")
    ap.add_argument("--sat", help="(alt) ein Kanal")
    ap.add_argument("--until", type=float, default=0, help="Aufnahmeende (Unixzeit)")
    ap.add_argument("--los", type=float, default=0, help="(alt) wie --until")
    ap.add_argument("--keys", default="", help="Überflug-Schlüssel (für die Liste im Dashboard)")
    ap.add_argument("--key", default="", help="(alt) ein Schlüssel")
    ap.add_argument("--test", help="uint8-I/Q-Datei statt Stick (Prüfung)")
    ap.add_argument("--start", type=float, default=0, help="Startzeit für --test")
    a = ap.parse_args()
    START_ISO = datetime.now(timezone.utc).isoformat()
    ids = [x for x in (a.sats or a.sat or "").split(",") if x in SAT.BY_ID]
    if not ids:
        sys.exit("kein gültiger Kanal")
    keys = [k for k in (a.keys or a.key or "").split(",") if k]
    chans = [SAT.BY_ID[i] for i in ids]
    objs = list(dict.fromkeys(c["norad"] for c in chans))
    name = " + ".join(SAT.OBJECTS[o] for o in objs)
    cfg = json.loads((BASE / "config" / "scanner.json").read_text(encoding="utf-8"))
    ws = json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
    lat, lon = SAT.locator_to_latlon(ws.get("my_locator", ""))
    tles, _ = SAT.load_tles(fetch=False)
    missing = [SAT.OBJECTS[o] for o in objs if o not in tles]
    if missing:
        applog("ERROR", "satellite", "pass_failed", sat=name, error="keine Bahndaten: " + ", ".join(missing))
        for k in keys:
            SAT.write_result(k, state="error", error="keine Bahndaten")
        sys.exit(1)
    trackers = {o: SAT.Tracker(tles[o], lat, lon) for o in objs}     # je Satellit eigene Bahn und Doppler
    tr = trackers[objs[0]]
    t0 = a.start or time.time()
    until = a.until or a.los or (t0 + 900)
    tune = SAT.tuning_for(ids)
    labels = ", ".join(SAT.mhz(c["center_hz"]) for c in chans)
    applog("INFO", "satellite", "pass_start", sat=name, channels=ids, tune_hz=tune,
           freq_mhz=[f"{c['center_hz']/1e6:.3f}" for c in chans],
           until=datetime.fromtimestamp(until, timezone.utc).isoformat(timespec="seconds"))
    print(f"{name} ({labels} MHz): Aufnahme bis {time.strftime('%H:%M:%S', time.gmtime(until))} UTC")
    for k in keys:
        SAT.write_result(k, state="running", channels=ids, started=time.time())

    RECDIR.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    tag = f"{os.getpid()}"
    channels = [Channel(c, tune, trackers[c["norad"]], tag) for c in chans]
    src = Source(tune, cfg, a.test)
    nblk, err = 0, None
    signal.signal(signal.SIGTERM, _on_term)
    try:
        REC_PID.write_text(str(os.getpid()))
    except OSError:
        pass
    try:
        while True:
            t = t0 + nblk * 0.1
            if t > until:
                break
            if _stop["flag"]:
                err = f"vom Benutzer abgebrochen nach {nblk / 10:.0f} s"
                applog("WARN", "satellite", "pass_cancelled", sat=name, seconds=round(nblk / 10))
                break
            raw = src.read()
            if raw is None:
                if not a.test:
                    err = "Stick liefert keine Daten mehr"
                break
            u = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
            z = ((u[0::2] - 127.5) + 1j * (u[1::2] - 127.5)) / 127.5
            for ch in channels:
                ch.feed(z, t)
            nblk += 1
            if nblk % 50 == 0:
                els = ", ".join(f"{SAT.OBJECTS[o].split(' ')[0]} {trackers[o].look(t)[0]:.0f}°" for o in objs)
                status("running", f"Satellit {name}: Aufnahme {labels} MHz, Elevation {els}",
                       min(99, 100 * (t - t0) / max(1, until - t0)))
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    finally:
        src.close()
        for ch in channels:
            ch.close_recording()
        REC_PID.unlink(missing_ok=True)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    secs = nblk / 10
    if err:
        applog("WARN", "satellite", "pass_recording_problem", sat=name, error=err, seconds=round(secs))
    status("running", f"Satellit {name}: Auswertung", 99)
    if not a.test:
        detach()                                              # ab hier ist der Stick frei
    hits_total, per_ch, per_obj = 0, {}, {o: 0 for o in objs}
    sig = {}
    for k in keys:                                            # bricht die Auswertung ab, sieht man es im Dashboard
        SAT.write_result(k, state="evaluating", seconds=round(secs))
    try:
        for ch in channels:
            hits = ch.evaluate(t0)
            n = db_save(ch.sat, hits)
            per_ch[ch.sat["id"]] = n
            sig[ch.sat["id"]] = getattr(ch, "stats", None) or signal_stats(
                np.array(ch.snr_track), 1.0 if ch.kind == "linear" else 0.1)
            per_obj[ch.sat["norad"]] += n
            hits_total += n
        applog("INFO", "satellite", "pass_done", sat=name, hits=hits_total, per_channel=per_ch,
               seconds=round(secs), problem=err, signal=sig)
        for cid, st_ in sig.items():
            print(f"   {cid}: Signal max {st_['max_db']} dB, 90 % {st_['p90_db']} dB, {st_['above6_s']} s über 6 dB")
        state = "cancelled" if _stop["flag"] else ("done" if not err or secs > 60 else "error")
        for k in keys:
            o = next((x for x in objs if k.startswith(f"{x}_")), objs[0])
            SAT.write_result(k, state=state, hits=per_obj.get(o, 0), per_channel=per_ch,
                             seconds=round(secs), error=err, shared=len(objs) > 1,
                             signal={c: v for c, v in sig.items() if SAT.BY_ID[c]["norad"] == o})
        if not a.test:
            for o in objs:
                SAT.record_stats(o, t0, per_obj[o], secs)
        status("done", f"Satellit {name}: {hits_total} Treffer", 100, only_if_mine=True)
        print(f"{name}: fertig, {hits_total} Treffer ({secs:.0f} s aufgenommen)")
    except Exception as e:
        applog("ERROR", "satellite", "pass_eval_failed", sat=name, error=f"{type(e).__name__}: {e}")
        for k in keys:
            SAT.write_result(k, state="error", error=f"Auswertung: {type(e).__name__}: {e}")
        for ch in channels:
            ch.tmp.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    main()
