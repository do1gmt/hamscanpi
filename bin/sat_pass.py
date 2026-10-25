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


def remember_interference(rf_hz, sat_name):
    """Örtliche Störung aus dem Satelliten-Wasserfall in die Liste der Störlinien (Reiter Stationen)."""
    try:
        db = sqlite3.connect(DB)
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("""CREATE TABLE IF NOT EXISTS interference (id INTEGER PRIMARY KEY, band TEXT, rf_hz REAL,
            first TEXT, last TEXT, n INTEGER, source TEXT, ignored INTEGER DEFAULT 0)""")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        band = "2m" if rf_hz < 200e6 else "70cm"
        row = db.execute("SELECT id FROM interference WHERE band=? AND ABS(rf_hz-?)<=3000", (band, rf_hz)).fetchone()
        if row:
            db.execute("UPDATE interference SET last=?, n=n+1 WHERE id=?", (now, row[0]))
        else:
            db.execute("INSERT INTO interference (band, rf_hz, first, last, n, source) VALUES (?,?,?,?,?,?)",
                       (band, rf_hz, now, now, 1, f"Satelliten-Wasserfall ({sat_name})"))
        db.commit()
        db.close()
    except Exception:
        pass


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

    def __init__(self, tune_hz, cfg, test=None, rate=None):
        self.test = test
        self.rate = int(rate or IN_RATE)
        self.nbytes = self.rate // 10 * 2                     # 0,1 s uint8-I/Q
        if test:
            self.f = open(test, "rb")
            return
        rtl = cfg.get("rtlsdr", {})
        import sticks as STK
        sp = STK.rtl_params(rtl)                    # Haupt-Stick (Seriennummer), eigener ppm/Verstärkung
        cmd = [rtl.get("binary", "rtl_sdr"), "-d", sp["d"], "-f", str(int(tune_hz)),
               "-s", str(self.rate), "-p", str(int(sp["ppm"]))]
        if sp["gain"]:
            cmd += ["-g", str(sp["gain"])]
        cmd.append("-")
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.f = self.p.stdout

    def read(self):
        b = self.f.read(self.nbytes)
        return b if len(b) == self.nbytes else None

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
        self.lp = butter(6, 42000 if (self.beacon_offs or sat.get("kind") == "probe") else 32000, fs=IN_RATE, output="sos")
        self.zi = (sosfilt_zi(self.lp) * 0).astype(np.complex128)
        self.lp2 = butter(6, 21000, fs=IN_RATE // 10, output="sos")
        self.zi2 = (sosfilt_zi(self.lp2) * 0).astype(np.complex128)
        self.n2 = np.arange(BLOCK // 10)
        self.phase = 0.0
        self.kind = sat["kind"]
        self.tmp = RECDIR / f".sat_{sat['id']}_{tag}.raw"
        self.tmpf = open(self.tmp, "wb") if self.kind == "fm" else None
        # Ton (24 kHz) während der Aufnahme auf die Karte statt in den Speicher: bei 20 min sind das
        # ~110 MB je FM-Kanal, die sonst bis zur Auswertung im RAM liegen (zwei Kanäle -> Swap am Pi)
        self.aud = RECDIR / f".sat_{sat['id']}_{tag}.aud"
        self.audf = open(self.aud, "wb") if self.kind == "fm" else None
        self.aud_n = 0
        self.chunk, self.chunk_t = [], None
        self.hits = []
        self.audio_parts, self.snr_track = [], []
        self.ch_lp = butter(6, 9000, fs=OUT_RATE, output="sos")
        self.ch_zi = (sosfilt_zi(self.ch_lp) * 0).astype(np.complex128)
        self.fm_last = 0j
        self.n = np.arange(BLOCK)

    def feed(self, z, t):
        if self.kind == "probe":
            # Prüfsatellit (breitbandig, z. B. Meteor-LRPT ~120 kHz): Leistung um die Kanalmitte gegen
            # ein freies Stück desselben 960-kHz-Fensters – unabhängig davon, wie stark er am Horizont schon ist
            n = 16384
            S = np.abs(np.fft.fftshift(np.fft.fft(z[:n] * np.hanning(n)))) ** 2
            fr = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / IN_RATE)) - self.base_off
            sig = float(np.mean(S[np.abs(fr) < 55000]))
            ref_m = (np.abs(fr) > 150000) & (np.abs(fr + self.base_off) > 20000) & (np.abs(fr + self.base_off) < IN_RATE * 0.42)
            ref = float(np.median(S[ref_m])) / 0.693 if ref_m.any() else 0.0
            self._probe_acc = getattr(self, "_probe_acc", 0.0) + (sig / ref if ref > 0 else 1.0)
            self._probe_n = getattr(self, "_probe_n", 0) + 1
            if self._probe_n >= 10:                          # je Sekunde ein Wert
                self.snr_track.append(self._probe_acc / self._probe_n)
                self._probe_acc, self._probe_n = 0.0, 0
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
        n = min(len(y96), 8192)
        S_full = np.abs(np.fft.fftshift(np.fft.fft(y96[:n] * self._win(n)))) ** 2
        self._wf_feed(S_full, n, t, dop)
        y, self.zi2 = sosfilt(self.lp2, y, zi=self.zi2)
        bb = y[::2]                                           # 48 kHz, Kanal bei 0 Hz
        if self.chunk_t is None:
            self.chunk_t = t
        if self.kind == "probe":
            return                                           # Pegel oben, Wasserfall schon erfasst
        if self.kind == "linear":
            # Pegel für die Signalprüfung: stärkste Spektrallinie (Bake, SSB-Station) gegen den Median,
            # über 1 s gemittelt; reines Rauschen ergibt dabei ~4 dB, das wird abgezogen
            # gemessen bei 96 kHz: Transponder (±20 kHz) und die Bake(n) (±2,5 kHz), Rauschen aus ±40 kHz
            S_ = S_full.copy()
            fr = self._freqs(n)
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
        a24 = resample_poly(d, 1, 2).astype(np.float32)
        self.audf.write(a24.tobytes())
        self.aud_n += len(a24)
        # Rauschabstand: Kanalleistung gegen das Rauschen 14–22 kHz daneben
        S_ = np.abs(np.fft.fft(bb[:4096] * np.hanning(4096))) ** 2
        fr = np.abs(np.fft.fftfreq(4096, 1.0 / OUT_RATE))
        dens = float(np.median(S_[(fr > 14000) & (fr < 22000)])) / 0.693 / np.sum(np.hanning(4096) ** 2)
        pw = float(np.mean(np.abs(c) ** 2))
        self.snr_track.append(pw / max(dens * 18000.0 / OUT_RATE, 1e-20))

    # ------------------------------------------------------------ Wasserfall und Einordnung
    WF_BINS = 512

    def _win(self, n):
        if getattr(self, "_win_n", 0) != n:
            self._win_cache, self._win_n = np.hanning(n).astype(np.float32), n
        return self._win_cache

    def _freqs(self, n):
        if getattr(self, "_fr_n", 0) != n:
            self._fr_cache, self._fr_n = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / (IN_RATE // 10))), n
        return self._fr_cache

    def _wf_feed(self, S, n, t, dop):
        """Je Sekunde eine Wasserfall-Zeile (dopplerkorrigiert) und die stärkste Linie im Kanal merken."""
        if not hasattr(self, "wf_rows"):
            self.wf_rows, self.lines, self._wf_acc, self._wf_n = [], [], None, 0
            self.wf_span = 40000.0 if self.kind in ("linear", "probe") else 25000.0
        self._wf_acc = S if self._wf_acc is None or len(self._wf_acc) != len(S) else self._wf_acc + S
        self._wf_n += 1
        if self._wf_n < 10:
            return
        P = self._wf_acc / self._wf_n
        self._wf_acc, self._wf_n = None, 0
        fr = self._freqs(n)
        m = np.abs(fr) < self.wf_span
        Pm, fm = P[m], fr[m]
        k = len(Pm) // self.WF_BINS * self.WF_BINS
        row = Pm[:k].reshape(self.WF_BINS, -1).mean(axis=1)
        self.wf_rows.append(10 * np.log10(row + 1e-20).astype(np.float32))
        # stärkste Linie dort, wo der Satellit senden kann (Transponder/Kanal und Baken)
        near = np.abs(fm) < (20000 if self.kind == "linear" else 9000)
        for bo in self.beacon_offs:
            near |= np.abs(fm - bo) < 2500
        if near.any():
            med = float(np.median(Pm[near]))
            i = int(np.argmax(np.where(near, Pm, 0)))
            self.lines.append((t, float(fm[i]), 10 * np.log10(Pm[i] / max(med, 1e-30)) - 4.2, float(dop)))

    def classify_line(self):
        """Steht die stärkste Linie nach der Dopplerkorrektur fest (= Satellit) oder wandert sie genau
        gegen den Doppler (= Sender auf der Erde, örtliche Störung)?"""
        L = [x for x in getattr(self, "lines", []) if x[2] >= 6.0]
        if len(L) < 15:
            return None
        f = np.array([x[1] for x in L])
        d = np.array([x[3] for x in L])
        res = {"seconds": len(L), "offset_hz": round(float(np.median(f))), "doppler_span_hz": round(float(np.ptp(d)))}
        if np.ptp(d) < 1500:
            res["kind"] = "unclear"                          # zu wenig Doppler zum Unterscheiden
            return res
        s_sat, s_loc = float(np.std(f)), float(np.std(f + d))
        if s_sat < 0.35 * s_loc and s_sat < 800:
            res["kind"] = "satellite"
            res["beacon"] = any(abs(res["offset_hz"] - b) < 1500 for b in self.beacon_offs)
        elif s_loc < 0.35 * s_sat and s_loc < 800:
            res["kind"] = "local"
            res["rf_mhz"] = round((self.f0 + float(np.median(f + d))) / 1e6, 4)
        else:
            res["kind"] = "unclear"
        return res

    def save_waterfall(self, t0, name):
        """Wasserfallbild (Zeit nach unten, Frequenz relativ zur Kanalmitte, dopplerkorrigiert)."""
        rows = getattr(self, "wf_rows", [])
        if len(rows) < 5:
            return None
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            return None
        A = np.array(rows)
        if len(A) > 900:                                     # höchstens 900 Zeilen
            g = int(np.ceil(len(A) / 900))
            A = A[: len(A) // g * g].reshape(-1, g, A.shape[1]).mean(axis=1)
        lo = float(np.median(A)) - 2
        v = np.clip((A - lo) / 16.0, 0, 1)                   # 16 dB Spanne: schwache Linien gut sichtbar
        stops = np.array([[0, 0, 40], [0, 60, 160], [0, 170, 200], [240, 220, 40], [255, 60, 30]], float)
        x = v * (len(stops) - 1)
        i = np.minimum(x.astype(int), len(stops) - 2)
        fr = (x - i)[..., None]
        rgb = (stops[i] * (1 - fr) + stops[i + 1] * fr).astype(np.uint8)
        W = self.WF_BINS * 2
        img = Image.fromarray(rgb).resize((W, max(len(A), 300)), Image.NEAREST)   # kurze Aufnahmen strecken
        top, bottom = 34, 22
        canvas = Image.new("RGB", (W, img.height + top + bottom), (12, 18, 28))
        canvas.paste(img, (0, top))
        dr = ImageDraw.Draw(canvas)
        span = self.wf_span
        xpos = lambda off: int((off + span) / (2 * span) * W)
        for off in ([-20000, 20000] if self.kind == "linear" else [-9000, 9000]):   # Kanal/Transponder
            dr.line([(xpos(off), top), (xpos(off), top + img.height)], fill=(90, 110, 130))
        for bo in self.beacon_offs:                                                  # Bake
            for yy in range(top, top + img.height, 8):
                dr.line([(xpos(bo), yy), (xpos(bo), yy + 4)], fill=(45, 212, 191))
            dr.text((xpos(bo) + 3, top + 2), "Bake", fill=(45, 212, 191))
        dr.text((6, 4), f"{name} {self.f0/1e6:.3f} MHz - dopplerkorrigiert - "
                        f"{time.strftime('%d.%m.%Y %H:%M UTC', time.gmtime(t0))}", fill=(230, 230, 230))
        dr.text((6, 18), f"{len(rows)} s, oben Anfang, unten Ende", fill=(150, 160, 170))
        for k in range(-int(span // 10000), int(span // 10000) + 1):
            off = k * 10000
            dr.text((max(0, xpos(off) - 12), top + img.height + 5), f"{off/1000:+.0f}k", fill=(150, 160, 170))
        out = REPORTS / (stem_for(self.sat, t0) + "_waterfall.png")
        canvas.save(out, optimize=True)
        return out.name

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
        if self.audf:
            self.audf.close()
            self.audf = None

    def cleanup(self):
        for f in (self.tmp, getattr(self, "aud", None)):
            if f:
                f.unlink(missing_ok=True)

    # ------------------------------------------------ Auswertung (nach Freigabe des Sticks)
    def evaluate(self, t0):
        sat = self.sat
        if self.kind != "fm" or not self.aud_n:
            self.cleanup()
            return self.hits
        # Ton von der Karte: für APRS nur lesend eingeblendet (bleibt Seitencache, belegt keinen festen
        # Speicher); SSTV braucht eine eigene Kopie, die es während der Tonanalyse überschreibt
        audio = np.memmap(self.aud, dtype=np.float32, mode="r")
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
            box = [np.fromfile(self.aud, dtype=np.float32)]
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
        self.cleanup()
        return self.hits

    def _aprs(self, audio, iq, n_iq, t0, pw_all):
        mm = shutil.which("multimon-ng")
        if not mm:
            print("   APRS: multimon-ng fehlt (sudo apt install multimon-ng)")
            return
        # blockweise 24000 -> 22050 Hz (früher der ganze Überflug auf einmal, als float64: ~200 MB bei 20 min)
        mx = 1e-9
        for j in range(0, len(audio), 24000 * 60):
            mx = max(mx, float(np.max(np.abs(audio[j:j + 24000 * 60]))))
        n_a = len(audio) * 147 // 160
        win, hop = int(12 * 22050), int(10 * 22050)
        packets, first = [], {}
        with tempfile.TemporaryDirectory() as td:
            for i0 in range(0, max(1, n_a - int(2 * 22050)), hop):
                raw = Path(td) / "a.raw"
                j0 = i0 * 160 // 147
                seg = np.asarray(audio[j0:j0 + win * 160 // 147 + 480], dtype=np.float32)
                a = resample_poly(seg, 147, 160).astype(np.float32)[:win]
                a = np.clip(a / mx * 0.9, -1, 1)
                (a * 32767).astype("<i2").tofile(raw)
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


# ---------------------------------------------------------------- Prüfsatelliten (Meteor-LRPT)
PROBE_RATE = 2048000          # 2,048 MS/s: 137,100 und 137,900 MHz passen mit Rand gemeinsam ins Fenster
PROBE_N = 32768               # FFT je 0,1 s (62,5 Hz je Linie), nur der Anfang jedes Blocks wird umgerechnet
PROBE_SPAN = 150000.0         # Wasserfall ±150 kHz um jede Frequenz: Kanten des LRPT-Signals sichtbar
PROBE_CORE = 40000.0          # Leistung im Kern des Signals (LRPT ~120 kHz breit)
LRPT_HALF = 60000.0           # Markierung der Signalbreite im Bild


def probe_tuning(chans):
    """Mittenfrequenz für Prüfsatelliten: zwischen allen Frequenzen (137,1 + 137,9 -> 137,5 MHz)."""
    fs = [float(f) for c in chans for f in [c["center_hz"]] + list(c.get("alt_hz", []))]
    return int((min(fs) + max(fs)) / 2)


class ProbeChannel:
    """Prüfsatellit: misst je Sekunde die Leistung auf jeder möglichen LRPT-Frequenz gegen das freie
    Band dazwischen und zeichnet einen breiten Wasserfall (±150 kHz) plus Leistungskurve.
    Keine Dekodierung, kein Speicher, der mit der Überflugdauer wächst (außer ein paar kB je Minute)."""
    kind = "probe"
    WF_BINS = 400

    def __init__(self, sat, tune_hz, tracker, tag):
        self.sat, self.tr = sat, tracker
        self.f0 = float(sat["center_hz"])
        self.freqs = sorted({self.f0, *[float(f) for f in sat.get("alt_hz", [])]})
        self.offs = [f - tune_hz for f in self.freqs]
        fr = np.fft.fftshift(np.fft.fftfreq(PROBE_N, 1.0 / PROBE_RATE))
        self.win = np.hanning(PROBE_N).astype(np.float32)
        self.core = [np.abs(fr - o) < PROBE_CORE for o in self.offs]
        busy = np.zeros(PROBE_N, bool)
        for o in self.offs:
            busy |= np.abs(fr - o) < 100000
        # Rauschbezug: freies Band zwischen/neben den Frequenzen, ohne Gleichanteil und Fensterränder
        self.ref = ~busy & (np.abs(fr) > 20000) & (np.abs(fr) < PROBE_RATE * 0.4)
        # Wasserfall: genau symmetrisch um jede Frequenz, ganze Zahl FFT-Linien je Bildspalte
        binw = PROBE_RATE / PROBE_N
        per = max(1, int(round(2 * PROBE_SPAN / binw / self.WF_BINS)))
        half = per * self.WF_BINS // 2
        self.span = half * binw
        self.panel = [np.arange(int(np.argmin(np.abs(fr - o))) - half, int(np.argmin(np.abs(fr - o))) + half)
                      for o in self.offs]
        self.tracks = [[] for _ in self.freqs]
        self.rows = [[] for _ in self.freqs]
        self._acc, self._n = None, 0
        self.hits, self.snr_track, self.stats = [], [], None
        self.tmp = RECDIR / f".sat_{sat['id']}_{tag}.none"      # gibt es nie (für die Fehlerbehandlung in main)

    def feed(self, z, t):
        S = np.abs(np.fft.fftshift(np.fft.fft(z[:PROBE_N] * self.win))) ** 2
        self._acc = S if self._acc is None else self._acc + S
        self._n += 1
        if self._n < 10:
            return
        P = self._acc / self._n
        self._acc, self._n = None, 0
        ref = float(np.median(P[self.ref])) / 0.967 if self.ref.any() else 1.0   # Median über 10 gemittelte Spektren
        for k in range(len(self.freqs)):
            self.tracks[k].append(float(np.mean(P[self.core[k]])) / max(ref, 1e-30))
            row = P[self.panel[k]].reshape(self.WF_BINS, -1).mean(axis=1)
            self.rows[k].append((10 * np.log10(row + 1e-20)).astype(np.float32))

    def close_recording(self):
        pass

    def evaluate(self, t0):
        per = {f"{f/1e6:.3f}": signal_stats(np.array(tr), 1.0) for f, tr in zip(self.freqs, self.tracks)}
        valid = [k for k, v in per.items() if v["max_db"] is not None]
        best = max(valid, key=lambda k: (per[k]["p90_db"], per[k]["max_db"])) if valid else f"{self.f0/1e6:.3f}"
        self.stats = dict(per[best], freq_mhz=best, probe=per)
        i = [f"{f/1e6:.3f}" for f in self.freqs].index(best)
        self.snr_track = self.tracks[i]
        return []

    def classify_line(self):
        return None

    def save_waterfall(self, t0, name):
        if not self.rows or len(self.rows[0]) < 5:
            return None
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            return None
        As = [np.array(r) for r in self.rows]
        g = int(np.ceil(len(As[0]) / 900)) if len(As[0]) > 900 else 1
        if g > 1:
            As = [A[: len(A) // g * g].reshape(-1, g, A.shape[1]).mean(axis=1) for A in As]
        lo = float(np.median(np.concatenate([A.ravel() for A in As]))) - 2
        stops = np.array([[0, 0, 40], [0, 60, 160], [0, 170, 200], [240, 220, 40], [255, 60, 30]], float)
        H = max(len(As[0]), 300)
        PW, CW, gap, top, bottom = self.WF_BINS * 2, 240, 8, 50, 22
        W = len(As) * (PW + gap) + CW
        canvas = Image.new("RGB", (W, H + top + bottom), (12, 18, 28))
        dr = ImageDraw.Draw(canvas)
        colors = [(255, 160, 60), (90, 220, 240), (200, 140, 255)]
        for k, A in enumerate(As):
            v = np.clip((A - lo) / 12.0, 0, 1)               # 12 dB Spanne: breitbandiger Anstieg gut sichtbar
            x = v * (len(stops) - 1)
            i = np.minimum(x.astype(int), len(stops) - 2)
            fr_ = (x - i)[..., None]
            rgb = (stops[i] * (1 - fr_) + stops[i + 1] * fr_).astype(np.uint8)
            img = Image.fromarray(rgb).resize((PW, H), Image.NEAREST)
            x0 = k * (PW + gap)
            canvas.paste(img, (x0, top))
            xpos = lambda off, x0=x0: x0 + int((off + self.span) / (2 * self.span) * PW)
            for off in (-LRPT_HALF, LRPT_HALF):                # erwartete Breite des LRPT-Signals
                for yy in range(top, top + H, 8):
                    dr.line([(xpos(off), yy), (xpos(off), yy + 3)], fill=(150, 160, 170))
            dr.text((x0 + 4, top - 14), f"{self.freqs[k]/1e6:.3f} MHz", fill=colors[k % 3])
            for off in (-100000, -50000, 0, 50000, 100000):
                dr.text((max(x0, xpos(off) - 14), top + H + 5), f"{off/1000:+.0f}k", fill=(150, 160, 170))
        # Leistungskurve: Zeit nach unten wie im Wasserfall, dB nach rechts
        cx0 = len(As) * (PW + gap)
        dbs = [10 * np.log10(np.array(tr) + 1e-12) for tr in self.tracks]
        dmax = max(12.0, max(float(d.max()) for d in dbs if len(d)) + 1)
        dmin = min(-1.0, min(float(d.min()) for d in dbs if len(d)))
        cxp = lambda d: cx0 + 10 + int((d - dmin) / (dmax - dmin) * (CW - 20))
        for d0, lab in ((0, "0 dB"), (6, "6 dB")):
            for yy in range(top, top + H, 6):
                dr.line([(cxp(d0), yy), (cxp(d0), yy + 2)], fill=(80, 90, 100))
            dr.text((cxp(d0) - 10, top + H + 5), lab, fill=(150, 160, 170))
        for k, d in enumerate(dbs):
            if len(d) < 2:
                continue
            pts = [(cxp(float(v)), top + int(j / (len(d) - 1) * (H - 1))) for j, v in enumerate(d)]
            dr.line(pts, fill=colors[k % 3], width=2)
        dr.text((cx0 + 10, top - 14), "Leistung im Kanal", fill=(230, 230, 230))
        dr.text((6, 4), f"{name} - LRPT auf " + " und ".join(f"{f/1e6:.3f}" for f in self.freqs) +
                f" MHz - {time.strftime('%d.%m.%Y %H:%M UTC', time.gmtime(t0))}", fill=(230, 230, 230))
        dr.text((6, 18), f"{len(self.rows[0])} s, oben Anfang, unten Ende; Striche = erwartete Signalbreite "
                         f"(±60 kHz); ohne Dopplerkorrektur (±3 kHz, bei 120 kHz Breite unerheblich)",
                fill=(150, 160, 170))
        out = REPORTS / (stem_for(self.sat, t0) + "_waterfall.png")
        canvas.save(out, optimize=True)
        return out.name


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
    probe = all(c["kind"] == "probe" for c in chans)    # Prüfsatelliten: eigene Abstimmung und Abtastrate
    tune = probe_tuning(chans) if probe else SAT.tuning_for(ids)
    labels = ", ".join(SAT.mhz(f) for c in chans for f in [c["center_hz"]] + list(c.get("alt_hz", [])))
    applog("INFO", "satellite", "pass_start", sat=name, channels=ids, tune_hz=tune,
           freq_mhz=[f"{c['center_hz']/1e6:.3f}" for c in chans],
           until=datetime.fromtimestamp(until, timezone.utc).isoformat(timespec="seconds"))
    print(f"{name} ({labels} MHz): Aufnahme bis {time.strftime('%H:%M:%S', time.gmtime(until))} UTC")
    for k in keys:
        SAT.write_result(k, state="running", channels=ids, started=time.time())

    RECDIR.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    tag = f"{os.getpid()}"
    channels = [(ProbeChannel if probe else Channel)(c, tune, trackers[c["norad"]], tag) for c in chans]
    src = Source(tune, cfg, a.test, PROBE_RATE if probe else IN_RATE)
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
            u = np.frombuffer(raw, dtype=np.uint8, count=2 * PROBE_N if probe else -1).astype(np.float32)
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
            track = np.array(ch.snr_track)
            sig[ch.sat["id"]] = dict(getattr(ch, "stats", None) or signal_stats(
                track, 1.0 if ch.kind in ("linear", "probe") else 0.1))
            try:                                              # Wasserfall und Einordnung der stärksten Linie
                sig[ch.sat["id"]]["waterfall"] = ch.save_waterfall(t0, ch.sat.get("name") or ch.sat["id"])
                sig[ch.sat["id"]]["line"] = ch.classify_line() if ch.kind != "probe" else None
                L = sig[ch.sat["id"]]["line"]
                if L and L.get("kind") == "local" and not a.test:
                    remember_interference(L["rf_mhz"] * 1e6, ch.sat.get("name") or ch.sat["id"])
            except Exception as e:
                applog("WARN", "satellite", "waterfall_failed", sat=ch.sat["id"], error=f"{type(e).__name__}: {e}")
            per_obj[ch.sat["norad"]] += n
            hits_total += n
        applog("INFO", "satellite", "pass_done", sat=name, hits=hits_total, per_channel=per_ch,
               seconds=round(secs), problem=err, signal=sig)
        for cid, st_ in sig.items():
            print(f"   {cid}: Signal max {st_['max_db']} dB, 90 % {st_['p90_db']} dB, {st_['above6_s']} s über 6 dB")
            for fq, v in (st_.get("probe") or {}).items():
                print(f"      {fq} MHz: max {v['max_db']} dB, 90 % {v['p90_db']} dB, {v['above6_s']} s über 6 dB"
                      + ("  <- stärkste" if fq == st_.get("freq_mhz") else ""))
            if st_.get("line"):
                print(f"   {cid}: stärkste Linie {st_['line']}")
            if st_.get("waterfall"):
                print(f"   {cid}: Wasserfall reports/{st_['waterfall']}")
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
            if hasattr(ch, "cleanup"):
                ch.cleanup()
        raise


if __name__ == "__main__":
    main()
