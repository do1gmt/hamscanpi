#!/usr/bin/env python3

import json
import sys
import sqlite3
from pathlib import Path

import numpy as np
import soundfile as sf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import butter, sosfiltfilt, resample_poly

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "scanner.db"
CFG = BASE / "config" / "scanner.json"
OUT = BASE / "reports"

OUT.mkdir(parents=True, exist_ok=True)

with open(CFG, "r", encoding="utf-8") as f:
    cfg = json.load(f)
try:                                                      # Bandplan des gewählten Landes
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bandplan as _BP
    cfg = _BP.apply(cfg)
except Exception:
    pass

RATE = int(cfg.get("sample_rate", 48000))
PPM = float(cfg.get("frequency_correction_ppm", 0.0))
CONJ = bool(cfg.get("iq_conjugate", False))


def corrected_center(center_hz):
    return center_hz * (1.0 + PPM * 1e-6)


def load_iq(path):
    data, rate = sf.read(path, always_2d=True)

    if data.shape[1] < 2:
        raise RuntimeError("Keine Stereo-I/Q-Datei")

    i = data[:, 0]
    q = data[:, 1]

    z = i + 1j * q

    if CONJ:
        z = np.conj(z)

    z -= np.mean(z)

    return z, rate


def shift_to_baseband(z, rate, offset_hz):
    n = np.arange(len(z))
    osc = np.exp(
        -2j * np.pi * offset_hz * n / rate
    )
    return z * osc


def demod_ssb(bb, rate, mode):
    """SSB aus dem Basisband (Träger bei 0 Hz, 12 kHz): Durchlass 250–3000 Hz auf der richtigen Seite als
    komplexer Bandpass (in die Mitte schieben, Tiefpass, zurückschieben), Realteil = Ton. Braucht nur
    wenige Kopien der 12-kHz-Daten statt vieler in voller Aufnahmerate."""
    lo, hi = 250.0, 3000.0
    sgn = 1.0 if mode == "USB" else -1.0
    n = np.arange(len(bb), dtype=np.float64)
    rot = np.exp(-2j * np.pi * sgn * (lo + hi) / 2 * n / rate).astype(np.complex64)
    sos = butter(6, (hi - lo) / 2, fs=rate, output="sos")
    x = sosfiltfilt(sos, bb * rot).astype(np.complex64) * np.conj(rot)
    audio = np.real(x).astype(np.float32)
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak > 0:
        audio = audio / peak * 0.85
    return audio.astype(np.float32), rate


def make_spectrum(z, rate, rf_center, rf_hit, outfile):
    """Spektrumbild ±6 kHz um den Treffer. z ist das Basisband mit dem Treffer bei 0 Hz (12 kHz Abtastrate) –
    früher die volle Aufnahmerate: bei Stick-Aufnahmen (bis 2,4 MS/s) waren das 5 s mit Millionen Punkten in
    doppelter Genauigkeit plus Kurve mit Millionen Punkten – 450–620 MB für ein einziges Bild."""
    n = min(len(z), int(rate * 5))
    z = z[:n].astype(np.complex64)
    z = z - z.mean()
    win = np.hanning(len(z)).astype(np.float32)
    spec = np.fft.fftshift(np.fft.fft(z * win))
    freq = np.fft.fftshift(np.fft.fftfreq(len(z), 1 / rate))
    keep = np.abs(freq) <= 6000
    spec, freq = spec[keep], freq[keep]
    if len(spec) > 6000:                               # für die Kurve reichen ~6000 Punkte (Höchstwert je Gruppe)
        k = len(spec) // 6000
        m = (len(spec) // k) * k
        mag = 20 * np.log10(np.maximum(np.abs(spec[:m]).reshape(-1, k).max(axis=1), 1e-12))
        freq = freq[:m].reshape(-1, k).mean(axis=1)
    else:
        mag = 20 * np.log10(np.maximum(np.abs(spec), 1e-12))

    rf = (rf_hit + freq) / 1e6

    fig = plt.figure(figsize=(10, 5))
    ax = fig.add_subplot(111)

    ax.plot(rf, mag)
    ax.axvline(
        rf_hit / 1e6,
        linestyle="--"
    )

    ax.set_xlim(
        (rf_hit - 6000) / 1e6,
        (rf_hit + 6000) / 1e6
    )

    ax.set_xlabel("MHz")
    ax.set_ylabel("SNR (dB)")
    ax.set_title(
        f"{rf_hit/1e6:.6f} MHz"
    )
    ax.grid(True)

    fig.tight_layout()
    fig.savefig(outfile, dpi=140)
    plt.close(fig)


db = sqlite3.connect(DB)

rows = db.execute("""
SELECT
    p.id,
    p.rf_hz,
    p.recording_path,
    COALESCE(p.rec_center_hz, s.center_hz),
    s.band,
    COALESCE(p.mode, ''),
    COALESCE(p.sideband, '')
FROM peak p
JOIN scan s ON s.id = p.scan_id
WHERE p.recording_path IS NOT NULL
ORDER BY p.id
""").fetchall()

if not rows:
    print("Keine Aufzeichnungen gefunden.")
    raise SystemExit(0)

count = 0

def _skip(src, pngfile, why):
    """Aufnahme nicht auswertbar: im Protokoll vermerken und ein leeres Spektrumbild hinterlegen, damit sie nicht
    bei jedem Scan erneut versucht wird (und den Speicher wieder füllt)."""
    try:
        from applog import log
        log("WARN", "analyze", "analysis_skipped", file=src.name, reason=why,
            size_mb=round(src.stat().st_size / 1e6, 1))
    except Exception:
        pass
    try:
        if not pngfile.exists():
            fig = plt.figure(figsize=(4, 1))
            fig.text(0.5, 0.5, "nicht ausgewertet", ha="center", va="center")
            fig.savefig(pngfile, dpi=60)
            plt.close(fig)
    except Exception:
        pass


import iqload as IQ  # noqa: E402

for peak_id, rf_hz, recpath, center_hz, band, mode, sideband in rows:
    src = Path(recpath)
    if not src.exists():
        print("FEHLT:", src)
        continue

    stem = src.stem
    usbfile = OUT / f"{stem}_USB.wav"
    lsbfile = OUT / f"{stem}_LSB.wav"
    pngfile = OUT / f"{stem}_spectrum.png"

    # Welche Seitenbaender werden gebraucht?
    # Sprache/SSTV: nur das laut Bandplan richtige.
    # FM/AM/Sat/Daten/Dauertraeger und VHF-Aufnahmen: keins (analyze_fm / analyze_vhf).
    sideband = str(sideband).upper()
    if mode in ("voice", "sstv") and sideband in ("USB", "LSB"):
        wanted = [sideband]
    elif mode in ("fm", "sat", "data", "carrier", "noise", "am", "wfm", "relais", "dv", "aprs", "pocsag") or float(center_hz) >= 30e6:
        wanted = []
    else:
        wanted = ["USB", "LSB"]

    need_usb = "USB" in wanted and not usbfile.exists()
    need_lsb = "LSB" in wanted and not lsbfile.exists()
    if not need_usb and not need_lsb and pngfile.exists():
        continue

    cc = corrected_center(center_hz)
    offset = rf_hz - cc
    try:
        bb = None
        if need_usb or need_lsb or not pngfile.exists():
            # Stückweise laden, auf die Trefferfrequenz mischen, auf 12 kHz – Speicher ~15 MB statt bis 500 MB;
            # dasselbe Basisband dient auch dem Spektrumbild (±6 kHz um den Treffer)
            bb, brate = IQ.baseband(src, offset, 12000, CONJ)
        if need_usb or need_lsb:
            if need_usb:
                usb, arate = demod_ssb(bb, brate, "USB")
                sf.write(usbfile, usb, arate, subtype="PCM_16")
            if need_lsb:
                lsb, arate = demod_ssb(bb, brate, "LSB")
                sf.write(lsbfile, lsb, arate, subtype="PCM_16")
        if not pngfile.exists():
            make_spectrum(bb, brate, cc, rf_hz, pngfile)
        del bb
    except MemoryError:
        print("ZU WENIG SPEICHER, übersprungen:", src.name)
        _skip(src, pngfile, "memory")
        continue
    except Exception as e:
        print("FEHLER bei", src.name, "–", type(e).__name__, e)
        _skip(src, pngfile, f"{type(e).__name__}: {e}")
        continue

    print(
        f"{band:4s}  "
        f"{rf_hz/1e6:10.6f} MHz  "
        f"{mode or '?'}  {' '.join(wanted) or '-'}"
    )

    count += 1

db.close()

print()
print("Neu ausgewertet:", count)
print("Ausgabe:", OUT)
