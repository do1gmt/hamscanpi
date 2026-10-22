#!/usr/bin/env python3
"""
Live-Demodulation für den RTL-SDR in Python – blockweise und zustandsbehaftet
(Filterzustände, Mischerphase und Diskriminator laufen über Blockgrenzen weiter,
deshalb keine Knackser an den Nähten).

Warum nicht mehr rtl_fm: Dort sieht man weder Übersteuerung noch Kanalpegel, und
die Filter lassen sich nicht an unsere Kanäle anpassen. Hier:
  rtl_sdr (960 kS/s, 200 kHz neben der Frequenz abgestimmt, kein DC-Fleck auf dem Signal)
  -> Mischer -> Tiefpass/÷4 (240 kS/s, UKW) -> Kanalfilter/÷10 (24 kS/s)
  -> FM-Diskriminator + Deemphasis | AM-Hüllkurve | SSB (Weaver) -> 24-kHz-PCM

Zusätzlich je Block: Übersteuerung (Anteil der 8-Bit-Werte am Anschlag), Kanalpegel
in dBFS und eine einfache Rauschsperre (Pegel über dem gleitenden Grundrauschen).
"""
import numpy as np
from scipy.signal import butter, lfilter, lfilter_zi, sosfilt, sosfilt_zi

IN_RATE = 960000
MID_RATE = 240000
OUT_RATE = 24000
TUNE_OFFSET = 200000        # Stick 200 kHz neben die Wunschfrequenz, Signal dann digital zurückgemischt
_MIX = np.exp(2j * np.pi * TUNE_OFFSET * np.arange(24) / IN_RATE).astype(np.complex64)


class _Sos:
    """Butterworth als SOS mit Zustand (komplex oder reell)."""

    def __init__(self, order, cutoff, fs, btype="low", complex_=False):
        self.sos = butter(order, cutoff, btype=btype, fs=fs, output="sos")
        zi = sosfilt_zi(self.sos)
        self.zi = (zi * 0).astype(np.complex128 if complex_ else np.float64)

    def __call__(self, x):
        y, self.zi = sosfilt(self.sos, x, zi=self.zi)
        return y


class RtlDemod:
    def __init__(self, mode="nfm"):
        self.mode = mode
        self.n = 0                                   # Abtastzähler für den Mischer (Phase durchgehend)
        self.lp_mid = _Sos(6, 90000, IN_RATE, complex_=True)        # vor ÷4
        ch = {"nfm": 6500, "am": 4500, "usb": 3000, "lsb": 3000}.get(mode, 6500)
        self.lp_ch = _Sos(6, ch, MID_RATE, complex_=True)            # Kanalfilter vor ÷10
        self.last = 1 + 0j                           # letzter Wert für den Diskriminator
        if mode == "wfm":
            a = np.exp(-1.0 / (MID_RATE * 50e-6))
            self.lp_audio = _Sos(6, 15000, MID_RATE)
        else:
            a = np.exp(-1.0 / (OUT_RATE * 530e-6))
        self.de_b, self.de_a = [1 - a], [1, -a]
        self.de_zi = lfilter_zi(self.de_b, self.de_a) * 0
        self.bp = _Sos(4, [250, 3400], OUT_RATE, btype="bandpass")
        self.am_hp = _Sos(2, 120, OUT_RATE, btype="highpass")
        self.ssb_lp = _Sos(6, 1450, OUT_RATE, complex_=True)
        self.ssb_n = 0
        self.agc = 0.1
        self.floor = None                            # gleitendes Grundrauschen (dBFS) für die Sperre
        self.clip_frac = 0.0
        self.level_dbfs = -120.0
        self.snr_db = 0.0

    def process(self, raw, squelch_db=0.0):
        """raw: uint8-I/Q-Bytes von rtl_sdr. Rückgabe: PCM s16le-Bytes bei 24 kHz."""
        u = np.frombuffer(raw, dtype=np.uint8)
        u = u[: len(u) // 80 * 80]                  # ganze 40er-Gruppen (I/Q-Paare)
        if not len(u):
            return b""
        self.clip_frac = float(np.mean((u <= 1) | (u >= 254)))
        x = (u.astype(np.float32) - 127.5) / 127.5
        z = (x[0::2] + 1j * x[1::2]).astype(np.complex64)
        # Mischer: 200 kHz / 960 kHz = 5/24 -> die Mischfolge wiederholt sich alle 24 Werte (Tabelle)
        k0 = self.n % 24
        reps = (len(z) + k0) // 24 + 1
        z = z * np.tile(_MIX, reps)[k0:k0 + len(z)]
        self.n += len(z)
        z = self.lp_mid(z)
        z240 = z[::4]          # Blöcke sind Vielfache von 40 Abtastwerten -> ÷4 und ÷10 bleiben im Takt

        if self.mode == "wfm":
            prev = np.concatenate(([self.last], z240[:-1]))
            self.last = z240[-1] if len(z240) else self.last
            d = np.angle(z240 * np.conj(prev))
            d, self.de_zi = lfilter(self.de_b, self.de_a, d, zi=self.de_zi)
            d = self.lp_audio(d)
            audio = d[::10] * 0.4                                   # ±75 kHz Hub ≈ ±2 rad
            lvl_src = z240
        else:
            zc = self.lp_ch(z240)
            z24 = zc[::10]
            lvl_src = z24
            if self.mode == "nfm":
                prev = np.concatenate(([self.last], z24[:-1]))
                self.last = z24[-1] if len(z24) else self.last
                d = np.angle(z24 * np.conj(prev))                   # rad/Abtastwert
                d, self.de_zi = lfilter(self.de_b, self.de_a, d, zi=self.de_zi)
                audio = self.bp(d) * 1.6                            # 2,5 kHz Hub -> ~0,4 Vollaussteuerung
            elif self.mode == "am":
                env = np.abs(z24)
                a = self.am_hp(env)
                a = self.bp(a)
                audio = self._agc(a)
            else:                                                   # USB/LSB (Weaver)
                k = np.arange(self.ssb_n, self.ssb_n + len(z24))
                self.ssb_n += len(z24)
                sgn = 1 if self.mode == "usb" else -1
                w = 2 * np.pi * 1500 / OUT_RATE
                b = self.ssb_lp(z24 * np.exp(-1j * sgn * w * k))
                audio = self._agc(self.bp(np.real(b * np.exp(1j * sgn * w * k))))

        p = float(np.mean(np.abs(lvl_src) ** 2)) if len(lvl_src) else 1e-12
        self.level_dbfs = 10 * np.log10(p + 1e-12)
        # Rauschen aus dem ganzen 960-kHz-Fenster (Median der Spektrallinien, dort ist fast nur
        # Rauschen) auf die Kanalbreite umgerechnet – so bleibt die Sperre auch bei langen
        # Durchgängen offen, statt das Signal mit der Zeit für Rauschen zu halten
        m = min(len(z) // 2048, 8) * 2048
        if m:
            w = np.hanning(2048)
            fr = (x[0::2][:m] + 1j * x[1::2][:m]).reshape(-1, 2048) * w
            Pk = np.abs(np.fft.fft(fr, axis=1)) ** 2 / np.sum(w * w)
            sigma2 = float(np.median(Pk)) / 0.693                  # Median -> Mittelwert (Exponentialvert.)
            bw = {"wfm": 2 * 90000.0, "nfm": 2 * 6500.0, "am": 2 * 4500.0}.get(self.mode, 2 * 3000.0)
            noise_ch = sigma2 * bw / IN_RATE
            self.floor = noise_ch if self.floor is None else 0.8 * self.floor + 0.2 * noise_ch
        if self.floor:
            self.snr_db = 10 * np.log10(p / self.floor + 1e-12)
        if squelch_db > 0 and self.snr_db < squelch_db:
            audio = np.zeros_like(audio)
        audio = np.clip(audio, -0.98, 0.98)
        return (audio * 32767).astype("<i2").tobytes()

    def _agc(self, a):
        pk = float(np.percentile(np.abs(a), 99)) if len(a) else 0.0
        target = 0.35
        if pk > 1e-9:
            want = target / pk
            # schnell leiser, langsam lauter
            self.agc = want if want < self.agc else self.agc + (want - self.agc) * 0.05
        return a * self.agc
