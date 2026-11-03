#!/usr/bin/env python3
"""Wetterfax (HF-Fax, z. B. DWD Pinneberg 3855 / 7880 / 13882,5 kHz) dekodieren – Wetterkarten als Bild.

Verfahren: Frequenzmodulierter Ton im USB-Empfang (Dial 1,9 kHz unter der angegebenen Frequenz):
1500 Hz = schwarz, 2300 Hz = weiß. 120 Zeilen je Minute (0,5 s je Zeile), Index of Cooperation 576
(Bildbreite 1809 Bildpunkte; hier auf 1200 verkleinert). Ablauf einer Aussendung: Startton (Schwarz/Weiß-Wechsel
mit 300 Hz, 5 s) – Phasing (30 s: je Zeile ein weißer Strich am Zeilenanfang) – Bild – Stoppton (450 Hz, 5 s).

Der Dekoder läuft fortlaufend (Stück für Stück): Ton -> Mischen auf 1900 Hz, Tiefpass, Frequenz-Diskriminator ->
Helligkeit; je 0,5 s eine Zeile. Der Startton beginnt ein neues Bild, das Phasing legt die Zeilenlage fest
(der weiße Strich kommt an den linken Rand), der Stoppton beendet es. Ohne Start (mitten hineingeschaltet)
wird trotzdem gezeichnet, nur ggf. seitlich verschoben.
"""
import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

RATE = 8000
LPM = 120
LINE_S = 60.0 / LPM
WIDTH = 1200
F_BLACK, F_WHITE, F_CENTER = 1500.0, 2300.0, 1900.0


class Decoder:
    def __init__(self, rate=RATE):
        self.rate = rate
        self.n = 0                                       # Abtastwerte insgesamt (für die Mischphase)
        self.sos = butter(4, 600, fs=rate, output="sos")
        self.zi = None
        self.prev = None
        self.buf = np.zeros(0, dtype=np.float32)         # Helligkeit, noch keine ganze Zeile
        self.raw = []                                    # Helligkeit der letzten ~6 s (Start-/Stoppton)
        self.lines = []                                  # Bildzeilen (uint8, WIDTH)
        self.phase = None                                # Verschiebung aus dem Phasing (Bildpunkte)
        self.phasing = []                                # Zeilen während des Phasings
        self.state = "wait"                              # wait | phasing | image | done
        self.events = []                                 # ("start"|"stop", Zeit)

    def _bright(self, audio):
        t = (self.n + np.arange(len(audio))) / self.rate
        self.n += len(audio)
        z = audio.astype(np.float64) * np.exp(-2j * np.pi * F_CENTER * t)
        if self.zi is None:
            self.zi = (sosfilt_zi(self.sos) * 0).astype(np.complex128)
        y, self.zi = sosfilt(self.sos, z, zi=self.zi)
        if self.prev is not None:
            y2 = np.concatenate([[self.prev], y])
        else:
            y2 = np.concatenate([[y[0]], y])
        self.prev = y[-1]
        f = np.angle(y2[1:] * np.conj(y2[:-1])) * self.rate / (2 * np.pi) + F_CENTER
        return np.clip((f - F_BLACK) / (F_WHITE - F_BLACK), 0, 1).astype(np.float32)

    def _tone(self, seg, hz):
        """Anteil der Helligkeitsschwankung bei hz (300 Hz Start, 450 Hz Stopp)."""
        x = seg - seg.mean()
        if len(x) < 2000 or not np.any(x):
            return 0.0
        S = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
        f = np.fft.rfftfreq(len(x), 1 / self.rate)
        band = (f > 50) & (f < 1000)
        tot = S[band].sum() or 1.0
        return float(S[(f > hz - 15) & (f < hz + 15)].sum() / tot)

    def feed(self, audio):
        """Ton (float, RATE) hinzufügen. Rückgabe: Liste neuer Ereignisse ("start", "stop", "line")."""
        ev = []
        b = self._bright(audio)
        self.raw.append(b)
        tot = sum(len(x) for x in self.raw)
        while tot > self.rate * 6 and len(self.raw) > 1:
            tot -= len(self.raw.pop(0))
        seg = np.concatenate(self.raw)[-int(self.rate * 2):]
        if self.state in ("wait", "done", "image") and self._tone(seg, 300) > 0.35:
            if self.state != "phasing":
                self.lines, self.phasing, self.phase, self.state = [], [], None, "phasing"
                ev.append("start")
        elif self.state in ("image", "phasing") and self._tone(seg, 450) > 0.35 and len(self.lines) > 20:
            self.state = "done"
            self.lines = self.lines[:-4]                     # die Zeilen mit dem Stoppton weglassen
            ev.append("stop")
        self.buf = np.concatenate([self.buf, b])
        spl = int(round(self.rate * LINE_S))
        while len(self.buf) >= spl:
            line, self.buf = self.buf[:spl], self.buf[spl:]
            px = np.interp(np.linspace(0, spl - 1, WIDTH), np.arange(spl), line)
            px = (px * 255).astype(np.uint8)
            if self.state == "phasing":
                if self.phase is not None:
                    r = np.roll(px, -self.phase).astype(np.float32)
                    if r[: int(WIDTH * 0.04)].mean() > 160 and r[int(WIDTH * 0.1):].mean() < 60:
                        continue                             # noch eine Phasing-Zeile
                    self.state = "image"                     # das Bild beginnt
                    self.lines.append(r.astype(np.uint8))
                    ev.append("line")
                    continue
                self.phasing.append(px)
                if len(self.phasing) >= 12 and self._tone(seg, 300) < 0.1:
                    m = np.mean(np.stack(self.phasing[-20:]).astype(np.float32), axis=0)
                    k = np.ones(int(WIDTH * 0.05)) / int(WIDTH * 0.05)
                    sm = np.convolve(np.concatenate([m, m]), k, mode="same")[:WIDTH]
                    self.phase = int(np.argmax(sm)) - int(WIDTH * 0.025)   # Strichmitte -> Zeilenanfang
                continue
            if self.state == "wait":
                self.state = "image"                         # mitten hineingeschaltet: trotzdem zeichnen
            if self.state == "image":
                self.lines.append(np.roll(px, -self.phase) if self.phase else px)
                ev.append("line")
        return ev

    def image(self):
        return np.stack(self.lines) if self.lines else None


def synth(img, with_tones=True, rate=RATE):
    """Probesignal aus einem Bild (Graustufen 0..1, beliebige Breite): Start, Phasing, Bild, Stopp (für Tests)."""
    spl = int(round(rate * LINE_S))
    segs = []

    def fm(bright):
        f = F_BLACK + np.asarray(bright) * (F_WHITE - F_BLACK)
        return f

    if with_tones:
        t = np.arange(int(rate * 5)) / rate
        segs.append(fm((np.sign(np.sin(2 * np.pi * 300 * t)) + 1) / 2))
        for _ in range(int(30 / LINE_S)):
            ln = np.zeros(spl)
            ln[: int(spl * 0.05)] = 1.0
            segs.append(fm(ln))
    for row in img:
        segs.append(fm(np.interp(np.linspace(0, len(row) - 1, spl), np.arange(len(row)), row)))
    if with_tones:
        t = np.arange(int(rate * 5)) / rate
        segs.append(fm((np.sign(np.sin(2 * np.pi * 450 * t)) + 1) / 2))
    f = np.concatenate(segs)
    ph = 2 * np.pi * np.cumsum(f) / rate
    return (0.5 * np.sin(ph)).astype(np.float32)


def save_png(img, path):
    from PIL import Image
    Image.fromarray(img).save(path, optimize=True)
