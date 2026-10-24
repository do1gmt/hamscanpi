#!/usr/bin/env python3
"""
SNR von FT8/FT4-Signalen so, wie WSJT-X und PSK Reporter ihn angeben: Leistung des Signals bezogen
auf das Rauschen in 2500 Hz Bandbreite (typisch −24 … +10 dB).

Der Dekoder (ft8_lib, decode_ft8) gibt in der SNR-Spalte nur eine Sync-Bewertung aus (score/2), keine
echte SNR – daher kamen bisher Werte wie +14 dB für schwache Signale heraus, auch in den Meldungen an
PSK Reporter. Hier wird der SNR aus der Aufnahme selbst geschätzt:

  Tonabstand 6,25 Hz (FT8) bzw. 20,833 Hz (FT4) → Spektrum mit genau dieser Auflösung; je Zeitschritt
  der stärkste der 8 bzw. 4 Töne an der dekodierten Frequenz minus Rauschen, gemittelt über die Sendung,
  geteilt durch das Rauschen je Bin; Umrechnung auf 2500 Hz: −10·log10(2500 / Tonabstand).
"""
import wave

import numpy as np

MODES = {"FT8": (6.25, 8, 12.64), "FT4": (20.833, 4, 4.48)}   # Tonabstand, Töne, Sendedauer


class Spectrogram:
    def __init__(self, path, mode="FT8"):
        self.mode = mode if mode in MODES else "FT8"
        df, ntone, dur = MODES[self.mode]
        with wave.open(str(path), "rb") as w:
            rate, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
            x = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32)
        if ch > 1:
            x = x.reshape(-1, ch)[:, 0]
        self.rate = rate
        nfft = int(round(rate / df))
        hop = nfft // 2
        win = np.hanning(nfft).astype(np.float32)
        frames = [x[a:a + nfft] * win for a in range(0, max(0, len(x) - nfft), hop)]
        self.P = np.abs(np.fft.rfft(np.array(frames), axis=1)) ** 2 if frames else np.zeros((0, nfft // 2 + 1))
        self.df, self.ntone, self.dur, self.hop_s = rate / nfft, ntone, dur, hop / rate
        f = np.fft.rfftfreq(nfft, 1.0 / rate)
        band = (f > 200) & (f < 2900)
        self.noise = float(np.median(self.P[:, band])) if self.P.size and band.any() else 0.0

    def snr(self, audio_hz, dt=0.0):
        """WSJT-X-artiger SNR in dB (2500 Hz) für eine Sendung bei audio_hz (tiefster Ton), dt wie gemeldet."""
        if not self.P.size or self.noise <= 0:
            return None
        k0 = int(round(audio_hz / self.df))
        k1 = k0 + self.ntone
        if k0 < 1 or k1 >= self.P.shape[1]:
            return None
        t0 = 0.5 + float(dt)                                   # FT8/FT4 beginnen nominell 0,5 s nach Slotbeginn
        a = max(0, int(t0 / self.hop_s))
        b = min(len(self.P), int((t0 + self.dur) / self.hop_s))
        if b - a < 4:
            return None
        sig = self.P[a:b, k0 - 1:k1 + 1].max(axis=1) - self.noise   # ±1 Bin Spielraum für Frequenzfehler
        p = float(np.mean(np.maximum(sig, 0.0)))
        if p <= 0:
            return -30.0
        val = 10 * np.log10(p / self.noise) - 10 * np.log10(2500.0 / MODES[self.mode][0])
        return round(float(np.clip(val, -30.0, 30.0)), 0)


def fix_messages(path, messages, mode="FT8"):
    """SNR der Dekoder-Zeilen ersetzen (alter Wert bleibt als „score“ erhalten)."""
    try:
        sp = Spectrogram(path, mode)
    except Exception:
        return messages
    for m in messages:
        s = sp.snr(m.get("audio_hz", 0), m.get("dt", 0.0))
        if s is not None:
            m.setdefault("score", m.get("snr"))
            m["snr"] = s
    return messages
