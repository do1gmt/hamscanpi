#!/usr/bin/env python3
"""
SSTV auf Kurzwelle (PMSDR): kurz hineinhören, bei SSTV lange aufnehmen und das Bild dekodieren.

  1. 12 kHz neben die Dialfrequenz abstimmen (Gleichanteil der PMSDR weit weg, wie bei FT8).
  2. Probe von wenigen Sekunden: Liegt im SSTV-Tonbereich (1,1–2,4 kHz über bzw. unter der
     Dialfrequenz) deutlich mehr Leistung als im Rauschen daneben, ist dort etwas los.
  3. Dann bis zu sstv_record_seconds (Standard 120 s) aufnehmen – ein Bild in Scottie 1 oder
     Martin 1 dauert knapp zwei Minuten. Seitenband nach Bandplan (80/40 m LSB, sonst USB).
  4. Dekodieren mit sstv_decode: Verstimmung der Gegenstation wird über den Synchronton
     ausgeglichen; hat die Aufnahme mitten im Bild begonnen, wird der Rest ohne Kopf dekodiert.
     Nur wenn ein Bild herauskommt, entsteht ein Treffer.
"""
import json
import math
import time
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi, resample_poly

BASE = Path(__file__).resolve().parent.parent
REPORTS = BASE / "reports"
AUDIO_RATE = 24000


def ssb_audio(i, q, rate, conjugate, shift_hz, side="USB", bw=3000.0, out_rate=AUDIO_RATE, chunk_s=5.0):
    """I/Q -> Audio eines Seitenbandes, blockweise (wenig Speicher auch bei 2 min Aufnahme).
    shift_hz = Dialfrequenz relativ zur Fenstermitte."""
    n_tot = len(i)
    sgn = 1.0 if side == "USB" else -1.0
    sos = butter(8, bw / 2 + 100, fs=rate, output="sos")
    zr = sosfilt_zi(sos) * 0.0
    zi_ = sosfilt_zi(sos) * 0.0
    f_mix = shift_hz + sgn * bw / 2                      # Mitte des Seitenbandes -> 0 Hz
    step = int(rate * chunk_s)
    g = math.gcd(out_rate, int(rate))
    up, down = out_rate // g, int(rate) // g
    out = []
    mi, mq = float(np.mean(i[: min(n_tot, rate)])), float(np.mean(q[: min(n_tot, rate)]))
    for a in range(0, n_tot, step):
        b = min(n_tot, a + step)
        ii, qq = np.asarray(i[a:b]) - mi, np.asarray(q[a:b]) - mq
        z = (ii - 1j * qq) if conjugate else (ii + 1j * qq)
        n = np.arange(a, b)
        y = z * np.exp(-2j * np.pi * f_mix * n / rate)
        yr, zr = sosfilt(sos, y.real, zi=zr)
        yi, zi_ = sosfilt(sos, y.imag, zi=zi_)
        w = np.exp(2j * np.pi * sgn * (bw / 2) * n / rate)  # zurück: Dialfrequenz bei 0 Hz
        audio = np.real((yr + 1j * yi) * w)
        out.append(resample_poly(audio, up, down).astype(np.float32))
    a = np.concatenate(out) if out else np.zeros(0, np.float32)
    a -= a.mean() if len(a) else 0
    pk = float(np.max(np.abs(a))) if len(a) else 0.0
    return a / pk * 0.8 if pk > 0 else a


def sstv_activity(i, q, rate, conjugate, shift_hz, side):
    """(dB) Leistung im SSTV-Tonbereich gegen das Rauschen 4–8 kHz neben der Dialfrequenz."""
    z = (i - 1j * q) if conjugate else (i + 1j * q)
    z = z - z.mean()
    nf = 4096
    m = len(z) // nf
    if m < 4:
        return 0.0
    P = np.mean(np.abs(np.fft.fft(z[: m * nf].reshape(m, nf) * np.hanning(nf), axis=1)) ** 2, axis=0)
    f = np.fft.fftfreq(nf, 1.0 / rate) - shift_hz         # relativ zur Dialfrequenz
    sgn = 1.0 if side == "USB" else -1.0
    inband = (sgn * f > 1100) & (sgn * f < 2400)
    noise = (np.abs(f) > 4000) & (np.abs(f) < 8000)
    if not inband.any() or not noise.any():
        return 0.0
    return float(10 * np.log10(np.mean(P[inband]) / (np.median(P[noise]) + 1e-20) + 1e-12))


def watch_sstv(dev, band, seg, cfg, log=print):
    """Eine SSTV-Frequenz prüfen; bei Aktivität lange aufnehmen und dekodieren. Rückgabe Trefferliste."""
    import sstv_decode as SD
    from devices import RECDIR, recording_name, wav_write_iq

    dial = float(seg["frequencies_hz"][0])
    side = seg.get("sideband") or ("LSB" if dial < 10e6 else "USB")
    offset = float(cfg.get("digital_tune_offset_hz", 12000.0))
    center = int(round(dial + offset))
    ppm = float(cfg.get("frequency_correction_ppm", 0.0))
    shift = dial - center * (1.0 + ppm * 1e-6)
    conj = bool(cfg.get("iq_conjugate", False))
    probe_s = float(cfg.get("sstv_probe_seconds", 4.0))
    rec_s = float(cfg.get("sstv_record_seconds", 120.0))
    thr = float(cfg.get("sstv_threshold_db", 6.0))

    dev.tune(center)
    i, q = dev.capture(probe_s)
    lvl = sstv_activity(i, q, dev.rate, conj, shift, side)
    if lvl < thr:
        log(f"   SSTV {dial/1e6:.3f} MHz {side}: ruhig ({lvl:.1f} dB)")
        return []
    log(f"   SSTV {dial/1e6:.3f} MHz {side}: Signal ({lvl:.1f} dB) – nehme {rec_s:.0f} s auf …")
    t_start = time.time()
    i2, q2 = dev.capture(rec_s)
    i = np.concatenate([i, i2]); q = np.concatenate([q, q2])
    del i2, q2
    audio = ssb_audio(i, q, dev.rate, conj, shift, side)
    imgs = SD.decode_all(audio, AUDIO_RATE, min_lines=0.2, auto_offset=True, headless=True)
    if not imgs:
        log(f"   SSTV {dial/1e6:.3f} MHz: kein Bild dekodiert")
        return []
    RECDIR.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    hits = []
    for k, img in enumerate(imgs):
        rf = dial + (1900.0 if side == "USB" else -1900.0)  # Bildmitte (Grauwert 1900 Hz)
        path = RECDIR / (Path(recording_name(band["name"], rf)).stem + f"_sstv{k}.wav")
        # I/Q nur um das Bild herum speichern (Abhören/Download wie gewohnt)
        a0 = int(max(0, img["start_s"] - 2) * dev.rate)
        mode = next(m for m in SD.MODES.values() if m["name"] == img["mode"])
        dur = SD.line_ms(mode) / 1000.0 * mode["height"] / mode.get("lines_per", 1) * img["complete"]
        a1 = int(min(len(i), (img["start_s"] + dur + 2) * dev.rate))
        wav_write_iq(path, i[a0:a1], q[a0:a1], dev.rate)
        SD.save_png(img["image"], REPORTS / f"{path.stem}_sstv.png")
        detail = (f"{img['mode']}, {img['complete']*100:.0f} % der Zeilen"
                  + (", ohne Kopf (Aufnahme begann mitten im Bild)" if img.get("headless") else "")
                  + (f", Gegenstation {img['offset_hz']:+d} Hz verstimmt" if img.get("offset_hz") else ""))
        data = {"band": band["name"], "frequency_hz": rf,
                "decoders": [{"type": "SSTV", "confidence": 100, "validated": True,
                              "text": f"SSTV {detail}", "detail": f"Dial {dial/1e6:.3f} MHz {side}"}]}
        (REPORTS / f"{path.stem}_decode.json").write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                                          encoding="utf-8")
        log(f"   SSTV: Bild {img['mode']} ({detail})")
        hits.append({"rf_hz": float(rf), "offset_hz": float(rf - center * (1.0 + ppm * 1e-6)),
                     "level_db": lvl, "snr_db": lvl, "recording_path": str(path),
                     "rec_center_hz": float(center), "segment_id": seg.get("id"), "mode": "sstv",
                     "sideband": side, "note": f"SSTV {img['mode']}", "audio": "ton"})
    return hits
