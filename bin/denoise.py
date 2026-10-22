#!/usr/bin/env python3
"""
Rauschminderung für demodulierten Ton (wie DNR am Transceiver).

Spektrale Subtraktion: Im Kurzzeitspektrum (≈ 40 ms) wird je Frequenz das gleichmäßige
Grundrauschen geschätzt – als unteres Quantil über die Zeit, denn Sprache hat Pausen – und
abgezogen. Die Dämpfung wird über Zeit und Frequenz geglättet und nach unten begrenzt,
damit kein „Blubbern“ (musical noise) entsteht und die Sprache natürlich bleibt.
"""
import numpy as np
from scipy.signal import stft, istft
from scipy.ndimage import uniform_filter

LEVELS = {1: dict(alpha=1.6, floor_db=-14.0), 2: dict(alpha=2.4, floor_db=-20.0)}


def denoise(audio, rate, level=1):
    if level not in LEVELS or len(audio) < rate // 2:
        return audio
    p = LEVELS[level]
    nper = int(2 ** round(np.log2(rate * 0.04)))          # ≈ 40 ms
    f, t, X = stft(audio, fs=rate, nperseg=nper, noverlap=nper * 3 // 4)
    pw = np.abs(X) ** 2
    # Rauschschätzung je Frequenz: 20-%-Quantil über die Zeit, gegen Ausreißer etwas geglättet
    noise = np.percentile(pw, 20, axis=1, keepdims=True) * 1.6
    noise = uniform_filter(noise, size=(5, 1), mode="nearest")
    gain = 1.0 - p["alpha"] * noise / np.maximum(pw, 1e-20)
    floor = 10 ** (p["floor_db"] / 20)
    gain = np.clip(gain, floor ** 2, 1.0)
    gain = np.sqrt(gain)
    gain = uniform_filter(gain, size=(3, 5), mode="nearest")     # 3 Bins × 5 Rahmen glätten
    gain = np.maximum(gain, floor)
    _, y = istft(X * gain, fs=rate, nperseg=nper, noverlap=nper * 3 // 4)
    return y[: len(audio)]
