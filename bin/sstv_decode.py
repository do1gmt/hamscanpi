#!/usr/bin/env python3
"""
SSTV-Dekoder für Tonaufnahmen (FM-demoduliert, z. B. ISS 145,800 MHz).

  1. Momentanfrequenz des Tons (Hilbert-Transformation, 1–2,6 kHz Bandpass)
  2. VIS-Kopf suchen: 1900 Hz Vorton, 1200 Hz Startbit, 7 Datenbits + Parität (1100/1300 Hz)
  3. Zeilen dekodieren; die Zeilenanfänge werden über die 1200-Hz-Synchronimpulse nachgeführt
     (lineare Ausgleichsgerade) – das gleicht Taktabweichungen der Soundkarte und Doppler-Reste aus.

Unterstützt: Martin 1/2, Scottie 1/2/DX, Robot 36/72, PD 90/120/160/180/240/290
(die ISS sendet meist PD120 oder PD180).

Aufruf:  python3 sstv_decode.py ton.wav [ausgabe_präfix]
"""
import sys
from pathlib import Path

import numpy as np
from scipy.signal import butter, hilbert, sosfiltfilt

F_SYNC, F_BLACK, F_WHITE = 1200.0, 1500.0, 2300.0


def _martin(scan, width, vis, name):
    g = 0.572
    segs = [("sync", 4.862), ("gap", g), ("G", scan), ("gap", g), ("B", scan), ("gap", g), ("R", scan), ("gap", g)]
    return dict(name=name, vis=vis, width=width, height=256, segs=segs, color="rgb")


def _scottie(scan, width, vis, name):
    g = 1.5
    segs = [("gap", g), ("G", scan), ("gap", g), ("gap", g), ("B", scan), ("gap", g),
            ("sync", 9.0), ("gap", g), ("R", scan), ("gap", g)]
    return dict(name=name, vis=vis, width=width, height=256, segs=segs, color="rgb")


def _pd(pixel, width, height, vis, name):
    t = pixel * width
    segs = [("sync", 20.0), ("gap", 2.08), ("Y0", t), ("Cr", t), ("Cb", t), ("Y1", t)]
    return dict(name=name, vis=vis, width=width, height=height, segs=segs, color="pd", lines_per=2)


MODES = {m["vis"]: m for m in [
    _martin(146.432, 320, 0x2C, "Martin 1"),
    _martin(73.216, 160, 0x28, "Martin 2"),
    _scottie(138.24 - 1.5, 320, 0x3C, "Scottie 1"),
    _scottie(88.064 - 1.5, 160, 0x38, "Scottie 2"),
    _scottie(345.6 - 1.5, 320, 0x4C, "Scottie DX"),
    dict(name="Robot 36", vis=0x08, width=320, height=240, color="robot36",
         segs=[("sync", 9.0), ("gap", 3.0), ("Y", 88.0), ("gap", 4.5), ("gap", 1.5), ("C", 44.0)]),
    dict(name="Robot 72", vis=0x0C, width=320, height=240, color="robot72",
         segs=[("sync", 9.0), ("gap", 3.0), ("Y", 138.0), ("gap", 4.5), ("gap", 1.5), ("V", 69.0),
               ("gap", 4.5), ("gap", 1.5), ("U", 69.0)]),
    _pd(0.532, 320, 256, 0x63, "PD 90"),
    _pd(0.19, 640, 496, 0x5F, "PD 120"),
    _pd(0.382, 512, 400, 0x62, "PD 160"),
    _pd(0.286, 640, 496, 0x60, "PD 180"),
    _pd(0.382, 640, 496, 0x61, "PD 240"),
    _pd(0.286, 800, 616, 0x5E, "PD 290"),
]}


def tone_frequency(audio, rate, chunk_s=30.0, pad_s=1.0):
    """Momentanfrequenz in Hz je Abtastwert (float32).

    Blockweise (30 s mit 1 s Überlappung): ein ISS-Überflug hat 10–11 min Ton; in einem Stück
    brauchten Filter und Hilbert-Transformation rund 1,4 GB Speicher – auf dem Pi wurde der
    Auswerteprozess dabei vom System beendet, ohne dass etwas im Protokoll stand."""
    sos = butter(4, [1000, 2600], btype="bandpass", fs=rate, output="sos")
    n = len(audio)
    out = np.empty(n, dtype=np.float32)
    C, P = int(chunk_s * rate), int(pad_s * rate)
    k = max(1, int(rate * 0.00025))                    # kurze Glättung (~0,25 ms) gegen Rauschspitzen
    for s0 in range(0, max(n, 1), C):
        a0, a1 = max(0, s0 - P), min(n, s0 + C + P)
        if a1 - a0 < 32:
            out[s0:min(n, s0 + C)] = 0.0
            continue
        seg = np.asarray(audio[a0:a1], dtype=np.float64)
        padlen = min(3 * (2 * len(sos) + 1), len(seg) - 1)
        a = sosfiltfilt(sos, seg, padlen=padlen)
        ph = np.unwrap(np.angle(hilbert(a)))
        f = np.diff(ph) * rate / (2 * np.pi)
        f = np.concatenate([f, f[-1:]])
        f = np.convolve(f, np.ones(k) / k, mode="same")
        out[s0:min(n, s0 + C)] = f[s0 - a0:s0 - a0 + min(C, n - s0)]
    return out


def _mean(f, a, b):
    a, b = int(max(0, a)), int(min(len(f), b))
    return float(np.median(f[a:b])) if b > a else 0.0


def find_vis(f, rate, start=0, chunk_s=60.0):
    """Sucht den nächsten VIS-Kopf ab Abtastwert start. Rückgabe (vis, bild_beginn) oder None.
    Blockweise (60 s), damit der Speicherbedarf nicht mit der Aufnahmelänge wächst."""
    n = len(f)
    C = int(chunk_s * rate)
    pre, post = int(0.2 * rate), int(0.4 * rate)
    c0 = int(start)
    while c0 < n - int(0.32 * rate):
        a = max(0, c0 - pre)
        b = min(n, c0 + C + post)
        r = _find_vis_block(f[a:b], rate, c0 - a, min(C, n - c0))
        if r:
            return r[0], r[1] + a
        c0 += C
    return None


def _find_vis_block(f, rate, start, length):
    """find_vis innerhalb eines Blocks: Kandidaten nur ab start, höchstens length Abtastwerte weit."""
    ms = rate / 1000.0
    k = max(1, int(5 * ms))
    sm = np.convolve(f, (np.ones(k) / k).astype(np.float32), mode="same")
    is1200 = np.abs(sm - 1200) < 70
    is1900 = np.abs(sm - 1900) < 70
    # Startbit: ≥ 20 ms 1200 Hz direkt nach ≥ 150 ms 1900 Hz
    i = start
    n = min(len(f), start + length + int(320 * ms))
    step = int(2 * ms)
    while i < n - int(320 * ms):
        if is1200[i] and np.mean(is1900[max(0, i - int(150 * ms)):i - int(2 * ms)]) > 0.85 \
                and np.mean(is1200[i:i + int(20 * ms)]) > 0.8:
            # genauen Anfang des Startbits suchen
            j = i
            while j > 0 and is1200[j - 1]:
                j -= 1
            bits = []
            for b in range(8):
                c = j + (30 + 30 * b) * ms
                bits.append(1 if _mean(sm, c + 5 * ms, c + 25 * ms) < 1200 else 0)
            stop = _mean(sm, j + 270 * ms + 5 * ms, j + 270 * ms + 25 * ms)
            vis = sum(bit << b for b, bit in enumerate(bits[:7]))
            parity_ok = (sum(bits[:7]) % 2) == bits[7]
            if parity_ok and abs(stop - 1200) < 120 and vis in MODES:
                return vis, int(round(j + 300 * ms))
            i = j + int(30 * ms)
        i += step
    return None


def decode_image(f, rate, mode, t0):
    """Bild ab Abtastwert t0 dekodieren. Rückgabe (RGB-Array, Anteil gefundener Syncs)."""
    ms = rate / 1000.0
    segs = mode["segs"]
    T = sum(d for _, d in segs) * ms                       # Zeilendauer in Abtastwerten
    sync_off = 0.0
    for name, d in segs:
        if name == "sync":
            break
        sync_off += d
    sync_len = dict(segs)["sync"] * ms
    nlines = mode["height"] // mode.get("lines_per", 1)

    # Sync-Impulse suchen: Mitte eines 1200-Hz-Abschnitts nahe der erwarteten Lage
    k = max(1, int(sync_len * 0.6))
    # nur den Zeitraum dieses Bildes betrachten (früher die ganze Aufnahme: bei 11 min ~450 MB)
    lo = max(0, int(t0 - T))
    hi = min(len(f), int(t0 + (nlines + 2) * T))
    ind = np.convolve((np.abs(f[lo:hi] - F_SYNC) < 150).astype(np.float32),
                      (np.ones(k) / k).astype(np.float32), mode="same")
    found = []
    win = int(min(T * 0.05, 15 * ms))
    drift = lambda L: 0.0                                  # laufende Schätzung der Abweichung
    for L in range(nlines):
        exp = t0 + L * T + sync_off * ms + sync_len / 2 + drift(L)
        a, b = int(exp - win), int(exp + win)
        if b >= hi:
            break
        if a < lo:
            continue
        seg = ind[a - lo:b - lo]
        mx = seg.max()
        if mx > 0.6:
            top = np.flatnonzero(seg >= mx - 0.02)              # Mitte des Plateaus, nicht sein Anfang
            found.append((L, a + float(np.mean(top)) - exp + drift(L)))
            # Abweichung nachführen (Taktfehler wächst linear mit der Zeilenzahl)
            if len(found) >= 6:
                Ls = np.array([x[0] for x in found[-30:]], float)
                ds = np.array([x[1] for x in found[-30:]], float)
                c1, c0 = np.polyfit(Ls, ds, 1)
                drift = (lambda c0, c1: (lambda L: c0 + c1 * L))(c0, c1)
    frac = len(found) / max(1, nlines)
    # Ausgleichsgerade über die gefundenen Syncs (robust: zweimal mit Ausreißerfilter)
    a0, slope = 0.0, 0.0
    if len(found) >= 8:
        L = np.array([x[0] for x in found], float)
        d = np.array([x[1] for x in found], float)
        for _ in range(2):
            A = np.vstack([np.ones_like(L), L]).T
            coef, *_ = np.linalg.lstsq(A, d, rcond=None)
            res = d - A @ coef
            keep = np.abs(res) < max(2 * ms, 3 * np.median(np.abs(res)) + 1)
            L, d = L[keep], d[keep]
            if len(L) < 8:
                break
        a0, slope = coef

    W = mode["width"]
    chans = {}

    # nur der Zeitraum des Bildes (lo..hi), float64 – die kumulierte Summe wird groß
    fc = np.clip(np.asarray(f[lo:hi], dtype=np.float64), 1000, 2600)
    cs = np.concatenate([[0.0], np.cumsum(fc)])

    def sample(start, dur):
        # Mittelwert je Pixel über kumulierte Summe (schnell); 10 % Rand je Seite auslassen
        edges = (start - lo) + np.linspace(0, dur, W + 1)
        w = edges[1:] - edges[:-1]
        a = np.clip(np.round(edges[:-1] + 0.1 * w).astype(int), 0, len(fc))
        b = np.clip(np.round(edges[1:] - 0.1 * w).astype(int), 0, len(fc))
        b = np.maximum(b, a + 1)
        b = np.minimum(b, len(fc))
        out = (cs[b] - cs[a]) / np.maximum(b - a, 1)
        return np.clip((out - F_BLACK) / (F_WHITE - F_BLACK) * 255.0, 0, 255)

    rows = {}
    for L in range(nlines):
        s = t0 + L * T + a0 + slope * L
        if s + T > len(f):
            break
        pos = s
        line = {}
        for name, d in segs:
            if name not in ("sync", "gap"):
                line[name] = sample(pos, d * ms)
            pos += d * ms
        rows[L] = line
    H = mode["height"]
    img = np.zeros((H, W, 3))
    if mode["color"] == "rgb":
        for L, ln in rows.items():
            img[L] = np.stack([ln["R"], ln["G"], ln["B"]], axis=1)
    elif mode["color"] == "pd":
        for L, ln in rows.items():
            for k_, yk in ((0, "Y0"), (1, "Y1")):
                img[2 * L + k_] = _ycc(ln[yk], ln["Cb"], ln["Cr"])
    elif mode["color"] == "robot72":
        for L, ln in rows.items():
            img[L] = _ycc(ln["Y"], ln["U"], ln["V"])
    elif mode["color"] == "robot36":
        for L in range(0, len(rows) - 1, 2):
            cr, cb = rows[L]["C"], rows[L + 1]["C"]          # gerade Zeile R-Y, ungerade B-Y
            img[L] = _ycc(rows[L]["Y"], cb, cr)
            img[L + 1] = _ycc(rows[L + 1]["Y"], cb, cr)
    n_rows = len(rows)
    if found:                                              # nach dem letzten Sync nur noch Rauschen
        last = found[-1][0] + 2
        if last < n_rows:
            img[last * mode.get("lines_per", 1):] = 0
            n_rows = last
    return np.clip(img, 0, 255).astype(np.uint8), frac, n_rows / max(1, nlines)


def _ycc(y, cb, cr):
    r = y + 1.402 * (cr - 128)
    g = y - 0.344136 * (cb - 128) - 0.714136 * (cr - 128)
    b = y + 1.772 * (cb - 128)
    return np.stack([r, g, b], axis=1)


def tone_offset(f):
    """Ablage der Töne gegen die Norm (Hz), aus den 1200-Hz-Synchronimpulsen geschätzt.
    Auf Kurzwelle ist eine SSB-Station oft um einige 10 Hz verstimmt; das verfälscht Farben
    und lässt Sync und VIS kippen. 0, wenn kein klarer Synchronton erkennbar ist."""
    x = f[(f > 1000) & (f < 1400)]
    if len(x) < 2000:
        return 0.0
    h, e = np.histogram(x, bins=80, range=(1000, 1400))
    hs = np.convolve(h, np.ones(3), mode="same")
    # nur echte Spitzen im Inneren: am oberen Rand läuft sonst der Schwarzpegel (1500 Hz) hinein
    cand = [k for k in range(3, 77) if hs[k] >= hs[k - 1] and hs[k] >= hs[k + 1] and hs[k] > 0]
    if not cand:
        return 0.0
    k = max(cand, key=lambda j: hs[j])
    peak = (e[k] + e[k + 1]) / 2
    sel = x[np.abs(x - peak) < 25]
    if len(sel) < 1000:
        return 0.0
    off = float(np.median(sel)) - F_SYNC
    return off if 10 <= abs(off) <= 180 else 0.0


def line_ms(mode):
    return sum(d for _, d in mode["segs"])


def decode_headless(f, rate, min_lines=40):
    """Bild ohne VIS-Kopf (Aufnahme hat mitten im Bild begonnen): Modus über den Abstand der
    Synchronimpulse bestimmen, ab dem ersten Impuls dekodieren. Rückgabe dict oder None."""
    ms = rate / 1000.0
    k = max(1, int(4 * ms))
    ind = np.convolve((np.abs(f - F_SYNC) < 120).astype(np.float32), (np.ones(k) / k).astype(np.float32), mode="same") > 0.7
    edges = np.flatnonzero(np.diff(ind.astype(np.int8)) == 1)
    if len(edges) < min_lines:
        return None
    best = None
    for mode in MODES.values():
        T = line_ms(mode) * ms
        sync_len = dict(mode["segs"])["sync"]
        # nur Impulse passender Länge (Robot/Martin ~5–9 ms, PD 20 ms)
        d = np.diff(edges)
        k_ = np.round(d / T)
        ok = (k_ >= 1) & (k_ <= 3) & (np.abs(d - k_ * T) < 0.004 * T + 1.5 * ms)
        n_ok = int(ok.sum())
        if n_ok >= min_lines and (best is None or n_ok > best[0]):
            first = int(edges[np.flatnonzero(ok)[0]])
            best = (n_ok, mode, first, sync_len)
    if not best:
        return None
    n_ok, mode, first, _ = best
    sync_off = 0.0
    for name, d in mode["segs"]:
        if name == "sync":
            break
        sync_off += d
    t0 = first - sync_off * ms
    if t0 < 0:
        t0 += line_ms(mode) * ms
    img, sync, complete = decode_image(f, rate, mode, t0)
    if sync < 0.5 * complete or complete < 0.15:
        return None
    return dict(mode=mode["name"], image=img, sync=round(sync, 2), complete=round(complete, 2),
                start_s=round(t0 / rate, 1), headless=True)


def decode_all(audio, rate, min_lines=0.3, auto_offset=False, headless=False):
    """Alle SSTV-Bilder einer Aufnahme. Rückgabe Liste dict(mode, image, sync, complete, start_s).
    auto_offset: Verstimmung (Kurzwelle) aus dem Synchronton ausgleichen.
    headless: zusätzlich ein Bild ohne VIS-Kopf suchen (Aufnahme begann mitten im Bild)."""
    audio = np.asarray(audio, dtype=np.float32)
    if rate > 24000:                                       # Speicher (Pi: 1 GB) – 24 kHz reichen
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(24000, int(rate))
        audio = resample_poly(audio, 24000 // g, int(rate) // g).astype(np.float32)
        rate = 24000
    f = tone_frequency(audio, rate).astype(np.float32)
    del audio
    off = tone_offset(f) if auto_offset else 0.0
    if off:
        f -= off
    out, pos = [], 0
    while True:
        v = find_vis(f, rate, pos)
        if not v:
            break
        vis, t0 = v
        mode = MODES[vis]
        img, sync, complete = decode_image(f, rate, mode, t0)
        if complete >= min_lines:
            out.append(dict(mode=mode["name"], image=img, sync=round(sync, 2),
                            complete=round(complete, 2), start_s=round(t0 / rate, 1)))
        T = sum(d for _, d in mode["segs"]) * rate / 1000.0
        pos = int(t0 + T * complete * mode["height"] / mode.get("lines_per", 1)) + 1
    if headless:
        first_vis = min((int(o["start_s"] * rate) for o in out), default=len(f))
        if first_vis > 20 * rate:                           # vor dem ersten Kopf war noch ein Bildrest
            h = decode_headless(f[:first_vis], rate)
            if h and h["complete"] >= 0.15:
                out.insert(0, h)
    for o in out:
        o["offset_hz"] = round(off)
    return out


def save_png(img, path):
    from PIL import Image
    Image.fromarray(img).save(str(path))


if __name__ == "__main__":
    import soundfile as sf
    a, r = sf.read(sys.argv[1])
    if a.ndim > 1:
        a = a[:, 0]
    pre = sys.argv[2] if len(sys.argv) > 2 else Path(sys.argv[1]).with_suffix("").as_posix()
    res = decode_all(a, r)
    for k, d in enumerate(res):
        p = f"{pre}_sstv{k + 1}.png"
        save_png(d["image"], p)
        print(f"{d['mode']}: {p}  (ab {d['start_s']} s, {d['complete']*100:.0f} % der Zeilen, Sync {d['sync']*100:.0f} %)")
    if not res:
        print("kein SSTV-Bild gefunden")
