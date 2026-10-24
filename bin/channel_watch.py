#!/usr/bin/env python3
"""
Kanalwache für kanalisierte Betriebsarten am RTL-SDR (FM, Relais, AM-Flugfunk, APRS …).

Bisher: kurzer Schnappschuss, Bestätigung Minuten später, dann neue Aufnahme. Das
überleben nur Dauerträger (meist Störlinien des Sticks); Sprachdurchgänge sind dann
längst vorbei.

Jetzt: je Abstimmfenster einige Sekunden am Stück aufnehmen, für jeden Kanal den
Pegelverlauf über die Zeit bestimmen und den Ton aus GENAU DIESER Aufnahme schneiden.
Störlinien fallen über die Bandbreite heraus (eine FFT-Linie statt eines Kanals),
und jede Aufnahme wird nach dem Demodulieren bewertet: Ton / nur Träger / Rauschen.
"""
import json
import math
import time
import numpy as np
from pathlib import Path
from scipy.signal import welch, butter, sosfiltfilt

CHANNEL_MODES = {"fm", "relais", "am", "aprs", "dv", "pocsag", "sat"}
# Modi, bei denen ein rauschender Ton trotzdem ein echtes Signal ist (Digitalfunk, Daten)
NOISY_OK = {"dv", "pocsag", "aprs"}

NFFT = 1024          # bei 1,024 MS/s = 1 kHz je Bin, 1 ms je Rahmen
AUDIO_RATE = 48000


def _raster(seg):
    mode = seg.get("mode")
    if mode == "am":
        return 25000.0 / 3.0          # 8,33-kHz-Raster (deckt 25 kHz mit ab)
    return 12500.0


def channel_list(band, selected, lo_hz, hi_hz):
    """Kanäle (rf_hz, segment) im Bereich lo..hi aus den gewählten Kanal-Segmenten."""
    sel = set(selected or ["all"])
    out = {}
    for seg in band.get("segments", []):
        if seg.get("mode") not in CHANNEL_MODES:
            continue
        if "all" not in sel and seg.get("id") not in sel:
            continue
        for f in seg.get("frequencies_hz", []) or []:
            if lo_hz <= f <= hi_hz:
                out[int(round(f))] = seg
        step = _raster(seg)
        for a, b in seg.get("ranges", []) or []:
            a, b = float(min(a, b)), float(max(a, b))
            n0 = int(np.ceil((max(a, lo_hz) - a) / step))
            f = a + n0 * step
            while f <= min(b, hi_hz):
                out.setdefault(int(round(f)), seg)
                f += step
    return sorted(out.items())


def _stft_power(i, q, chunk_frames=256):
    """Leistung je Rahmen und Bin (float32), stückweise gerechnet – spart am Pi Speicher."""
    n = len(i) // NFFT
    win = np.hanning(NFFT).astype(np.float32)
    P = np.empty((n, NFFT), dtype=np.float32)
    for f0 in range(0, n, chunk_frames):
        f1 = min(n, f0 + chunk_frames)
        a, b = f0 * NFFT, f1 * NFFT
        blk = (i[a:b] + 1j * q[a:b]).astype(np.complex64).reshape(-1, NFFT) * win
        S = np.fft.fftshift(np.fft.fft(blk, axis=1), axes=1)
        P[f0:f1] = (S.real ** 2 + S.imag ** 2)
    return P


def audio_quality(zi, zq, rate, mode):
    """Demoduliert grob und bewertet: ('ton'|'traeger'|'rauschen', Kennzahl in dB)."""
    z = zi + 1j * zq
    if len(z) < rate // 4:
        return "rauschen", -99.0
    # Kanalfilter: nur die Kanalbreite demodulieren, sonst dominiert das Rauschen daneben
    sos = butter(6, (4000.0 if mode == "am" else 6500.0) / (rate / 2), output="sos")
    z = sosfiltfilt(sos, z.real) + 1j * sosfiltfilt(sos, z.imag)
    if mode == "am":
        e = np.abs(z)
        m = float(np.mean(e)) or 1.0
        a = e / m - 1.0
        f, P = welch(a, fs=rate, nperseg=2048)
        speech = float(np.mean(P[(f >= 300) & (f <= 2800)]))
        hf = float(np.mean(P[(f >= 4500) & (f <= 9000)])) + 1e-20
        depth = float(np.std(a))
        ratio = 10 * np.log10(speech / hf + 1e-20)
        if depth < 0.03:
            return "traeger", ratio
        return ("ton" if ratio >= 6.0 else "rauschen"), ratio
    d = np.angle(z[1:] * np.conj(z[:-1])) * rate / (2 * np.pi)     # Momentanfrequenz in Hz
    f, P = welch(d, fs=rate, nperseg=2048)
    speech = float(np.mean(P[(f >= 300) & (f <= 2800)]))
    hf = float(np.mean(P[(f >= 3800) & (f <= 5500)])) + 1e-20
    ratio = 10 * np.log10(speech / hf + 1e-20)
    # Hub im Sprachband: unter ~150 Hz effektiv ist das ein stummer Träger
    dev_rms = float(np.sqrt(speech * (2800 - 300)))
    if ratio >= 6.0 and dev_rms >= 150.0:
        return "ton", ratio
    if ratio >= 0.0 and dev_rms < 150.0:
        return "traeger", ratio
    return "rauschen", ratio


# ====================================================================== UKW-Rundfunk (RTL-SDR)
#
# Ein UKW-Sender ist ±100 kHz breit und hat keine scharfe Spektrallinie: die Spitzensuche findet
# „irgendwo“ im Sender eine Spitze, die bei der Bestätigung nicht mehr an derselben Stelle liegt –
# die meisten Sender wurden deshalb verworfen. Hier wird stattdessen die Leistung je 100-kHz-Kanal
# gemessen, jeder Sender genau auf seiner Kanalmitte aufgenommen (für RDS) und mit automatischer
# Verstärkung empfangen (bei fester Scan-Verstärkung fehlte RDS oft).

RTL_GAINS = [0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7, 20.7, 22.9, 25.4, 28.0, 29.7,
             32.8, 33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6]


def _gain_step(g, n):
    i = min(range(len(RTL_GAINS)), key=lambda k: abs(RTL_GAINS[k] - g))
    return RTL_GAINS[max(0, min(len(RTL_GAINS) - 1, i + n))]


FM_RASTER = 100000.0
FM_RATE = 240000


def fm_channels(band, selected, lo, hi):
    out = []
    for seg in band.get("segments", []):
        if seg.get("mode") != "wfm" or not ("all" in (selected or ["all"]) or str(seg.get("id")) in set(selected)):
            continue
        for a, b in seg.get("ranges", []):
            f = np.ceil(max(a, lo) / FM_RASTER) * FM_RASTER
            while f <= min(b, hi):
                out.append(float(f))
                f += FM_RASTER
    return sorted(set(out))


def watch_fm(dev, band, center, cfg, selected, allowed=lambda rf: True, log=print):
    """Ein Fenster einige Sekunden aufnehmen, Sender je 100-kHz-Kanal finden und einzeln (240 kHz,
    Mitte = Sender) speichern. Rückgabe (Treffer, verworfen)."""
    from devices import RECDIR, recording_name, wav_write_iq
    rate = float(dev.rate)
    reach = rate / 2 * 0.9 - 100000                          # Sender muss ganz ins Fenster passen
    chans = [f for f in fm_channels(band, selected, center - reach, center + reach) if allowed(f)]
    if not chans:
        return [], 0
    secs = float(cfg.get("fm_record_seconds", 6.0))
    old_gain = getattr(dev, "gain", None)
    try:
        if cfg.get("fm_auto_gain", True):
            # eigene Regelung je Fenster (die Automatik im Stick übersteuert bei starken Ortssendern):
            # Stufe vom letzten Scan, bei Übersteuerung sofort tiefer neu aufnehmen, bei leisem Signal
            # beim nächsten Scan eine Stufe höher. Gemerkt in data/fm_gain.json.
            gfile = Path(__file__).resolve().parent.parent / "data" / "fm_gain.json"
            try:
                memo = json.loads(gfile.read_text())
            except Exception:
                memo = {}
            key = str(int(round(center / 1000)))
            g = float(memo.get(key, 29.7))
            for _ in range(3):
                dev.gain = g
                i, q = dev._grab(center, secs)
                m = np.abs(np.concatenate([i[::50], q[::50]]))
                clip, peak = float(np.mean(m > 0.985)), float(np.percentile(m, 99.5))
                if clip > 0.003 and g > RTL_GAINS[0]:
                    g = _gain_step(g, -3)
                    log(f"      übersteuert ({clip*100:.1f} %) – neu mit {g:g} dB")
                    continue
                if peak < 0.25:
                    memo[key] = _gain_step(g, +1)
                else:
                    memo[key] = g
                break
            try:
                gfile.parent.mkdir(parents=True, exist_ok=True)
                gfile.write_text(json.dumps(memo))
            except OSError:
                pass
        else:
            i, q = dev._grab(center, secs)
    finally:
        dev.gain = old_gain
    # mittleres Leistungsspektrum (stückweise: wenig Speicher)
    N = 4096
    acc, frames = np.zeros(N), 0
    win = np.hanning(N).astype(np.float32)
    step = max(N, len(i) // 200)
    for a in range(0, len(i) - N, step):
        z = (i[a:a + N] + 1j * q[a:a + N]) * win
        acc += np.abs(np.fft.fftshift(np.fft.fft(z))) ** 2
        frames += 1
    if not frames:
        return [], 0
    P = acc / frames
    f = np.fft.fftshift(np.fft.fftfreq(N, 1.0 / rate))
    floor = float(np.percentile(P, 15))
    thr = float(cfg.get("fm_threshold_db", 10.0))
    snr = {}
    for rf in chans:
        m = np.abs(f - (rf - center)) <= 75000
        if m.sum():
            snr[rf] = 10 * np.log10(float(P[m].mean()) / floor + 1e-12)
    hits = []
    for rf, s in snr.items():
        # Sender = über der Schwelle und stärker als beide Nachbarkanäle (Seitenbänder starker Sender)
        if s < thr or s < snr.get(rf - FM_RASTER, -99) or s < snr.get(rf + FM_RASTER, -99):
            continue
        zi, zq = dev._slice(i, q, center, rf, FM_RATE)
        RECDIR.mkdir(parents=True, exist_ok=True)
        path = RECDIR / recording_name(band["name"], rf)
        wav_write_iq(path, zi, zq, FM_RATE)
        hits.append({"rf_hz": rf, "offset_hz": rf - center, "level_db": 10 * np.log10(floor + 1e-20) + s,
                     "snr_db": round(s, 1), "recording_path": str(path), "rec_center_hz": rf,
                     "segment_id": "wfm", "mode": "wfm", "sideband": None, "audio": "ton", "audio_score": None})
        log(f"      {rf/1e6:7.1f} MHz  UKW-Sender {s:4.1f} dB")
    del i, q
    return hits, 0


FOLLOW_MODES = {"fm", "relais", "am", "dv"}         # Sprache: ganzes QSO aufnehmen


def follow_qso(dev, center, chans, cfg, log=print):
    """Am Fenster dranbleiben, solange auf einem der Kanäle gesprochen wird.

    rtl_sdr läuft dabei durchgehend (keine Lücken wie bei mehreren Einzelaufnahmen); jede Sekunde
    wird je Kanal geprüft, ob noch Signal da ist. Schluss ist, wenn alle Kanäle qso_hang_seconds
    (Standard 4 s) still waren oder qso_max_seconds (Standard 120 s) erreicht sind.
    Rückgabe {rf: (i, q)} mit 48-kHz-Ausschnitten (unnormiert)."""
    import subprocess
    from scipy.signal import resample_poly
    rate = int(dev.rate)
    max_s = float(cfg.get("qso_max_seconds", 120))
    hang_s = float(cfg.get("qso_hang_seconds", 4))
    g = math.gcd(AUDIO_RATE, rate)
    up, down = AUDIO_RATE // g, rate // g
    pad = 20 * down
    cmd = [dev.binary, "-f", str(int(center)), "-s", str(rate), "-d", str(dev.index), "-p", str(dev.ppm)]
    if dev.gain:
        cmd += ["-g", str(dev.gain)]
    if getattr(dev, "bias_t", False):
        cmd += ["-T"]
    cmd.append("-")
    out = {c["rf"]: [] for c in chans}
    tails = {c["rf"]: None for c in chans}
    ref = {}                                       # Kanalleistung bei Signal (aus der ersten Sekunde)
    silent = {c["rf"]: 0.0 for c in chans}
    t0, n_done = time.time(), 0
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except Exception as e:
        log(f"      QSO-Verfolgung nicht möglich: {e}")
        return {}
    try:
        while n_done / rate < max_s:
            raw = p.stdout.read(rate * 2)          # 1 s
            if not raw or len(raw) < rate:
                break
            u = np.frombuffer(raw, dtype=np.uint8)[: len(raw) // 2 * 2].reshape(-1, 2)
            i = (u[:, 0].astype(np.float32) - 127.5) / 127.5
            q = (u[:, 1].astype(np.float32) - 127.5) / 127.5
            i -= i.mean(); q -= q.mean()
            n_idx = np.arange(n_done, n_done + len(i), dtype=np.float64)
            all_quiet = True
            for c in chans:
                rf = c["rf"]
                z = (i + 1j * q).astype(np.complex64) * np.exp(-2j * np.pi * (rf - center) * n_idx / rate).astype(np.complex64)
                tail = tails[rf]
                zz = z if tail is None else np.concatenate([tail, z])
                y = resample_poly(zz, up, down)
                skip = 0 if tail is None else len(tail) * up // down
                y = y[skip:skip + len(z) * up // down]
                tails[rf] = z[-pad:]
                out[rf].append(y.astype(np.complex64))
                # Kanalleistung (±6 kHz) in 0,1-s-Rahmen
                fr = AUDIO_RATE // 10
                nfr = len(y) // fr
                if nfr == 0:
                    continue
                Y = np.fft.fft(y[: nfr * fr].reshape(nfr, fr), axis=1)
                f = np.fft.fftfreq(fr, 1.0 / AUDIO_RATE)
                pw = (np.abs(Y[:, np.abs(f) <= 6000]) ** 2).sum(axis=1)
                if rf not in ref:
                    ref[rf] = float(np.percentile(pw, 90)) / (10 ** (max(6.0, c.get("level", 20.0)) / 20.0))
                on = pw > ref[rf]
                # Stille am Ende dieses Abschnitts hochzählen, bei Signal zurücksetzen
                if on.any():
                    last_on = int(np.flatnonzero(on)[-1])
                    silent[rf] = (nfr - 1 - last_on) * 0.1
                else:
                    silent[rf] += nfr * 0.1
                if silent[rf] < hang_s:
                    all_quiet = False
            n_done += len(i)
            if all_quiet:
                break
    finally:
        try:
            p.terminate(); p.wait(timeout=3)
        except Exception:
            p.kill()
    secs = n_done / rate
    res = {}
    for c in chans:
        y = np.concatenate(out[c["rf"]]) if out[c["rf"]] else np.zeros(0, np.complex64)
        # Stille am Ende bis auf 1 s abschneiden
        cut = int(max(0.0, silent[c["rf"]] - 1.0) * AUDIO_RATE)
        if cut and cut < len(y):
            y = y[: len(y) - cut]
        res[c["rf"]] = (y.real.astype(np.float64), y.imag.astype(np.float64))
    freqs = ", ".join(f"{c['rf'] / 1e6:.4f}" for c in chans)
    log(f"      QSO verfolgt: {freqs} MHz, {secs:.0f} s" + (" (Höchstdauer)" if secs >= max_s - 0.5 else ""))
    return res


def watch_window(dev, band, center, cfg, selected, owner_half_width, log=print):
    """Ein Fenster belauschen. Rückgabe: Liste von Treffern (wie Scanner-Peaks)."""
    from devices import RECDIR, recording_name, wav_write_iq

    rate = float(dev.rate)
    usable = min(owner_half_width, rate / 2 * 0.85)
    chans = channel_list(band, selected, center - usable, center + usable)
    if not chans:
        return [], 0
    seconds = float(cfg.get("rtl_dwell_seconds", 4.0))
    thr = float(cfg.get("channel_threshold_db", 10.0))
    min_active = float(cfg.get("min_active_seconds", 0.3))

    i, q = dev._grab(center, seconds)
    P = _stft_power(i, q)                                # F × NFFT
    frames = P.shape[0]
    frame_s = NFFT / rate
    bin_hz = rate / NFFT
    noise = float(np.median(P))                          # Grundrauschen des Fensters
    noise_db = 10 * np.log10(noise + 1e-20)

    cand = []
    for rf, seg in chans:
        mode = seg.get("mode")
        half_bw = 4000.0 if mode == "am" else 5000.0
        off = rf - center
        b0 = int(round((off - half_bw) / bin_hz + NFFT / 2))
        b1 = int(round((off + half_bw) / bin_hz + NFFT / 2)) + 1
        if b0 < 0 or b1 > NFFT:
            continue
        band_lin = P[:, b0:b1]
        # Gleichanteil des RTL (Fenstermitte) nicht mitmessen: Bins innerhalb ±2 kHz auslassen
        dc_lo, dc_hi = int(NFFT / 2 - 2000 / bin_hz), int(NFFT / 2 + 2000 / bin_hz) + 1
        keep = [j for j in range(b0, b1) if not (dc_lo <= j < dc_hi)]
        if len(keep) < (b1 - b0) // 2:
            continue                                     # Kanal liegt auf der Mitte – anderes Fenster
        if len(keep) < (b1 - b0):
            band_lin = P[:, keep]
        ch_db = 10 * np.log10(band_lin.mean(axis=1) + 1e-20) - noise_db
        # Rahmen glätten (≈ 50 ms), sonst flackert die Aktivitätsmaske
        k = max(1, int(0.05 / frame_s))
        ch_db_s = np.convolve(ch_db, np.ones(k) / k, mode="same")
        active = ch_db_s >= thr
        n_act = int(active.sum())
        if n_act * frame_s < min_active:
            continue
        # Bandbreite: wie viele Bins liegen im aktiven Mittel > 6 dB über Rauschen?
        mean_spec = 10 * np.log10(band_lin[active].mean(axis=0) + 1e-20) - noise_db
        occ_bins = int((mean_spec >= max(6.0, float(mean_spec.max()) - 20.0)).sum())
        width_hz = occ_bins * bin_hz
        frac = n_act / frames
        cand.append(dict(rf=rf, seg=seg, mode=mode, active=active, frac=frac,
                         level=float(np.median(ch_db_s[active])), width_hz=width_hz,
                         peak_db=float(mean_spec.max())))

    # Nachbarkanäle desselben Signals zusammenfassen (25-kHz-FM überdeckt zwei 12,5-kHz-Kanäle)
    cand.sort(key=lambda c: -c["peak_db"])
    picked = []
    for c in cand:
        if any(abs(c["rf"] - p["rf"]) < 11000 for p in picked):
            continue
        picked.append(c)

    hits, rejected = [], 0
    extra = None
    follow = None                                    # verfolgte Durchgänge (Relais-/FM-QSOs), je Kanal
    del P
    end_frames = frames - int(0.25 / frame_s)
    to_follow = [c for c in picked if c["mode"] in FOLLOW_MODES and np.flatnonzero(c["active"]).size
                 and np.flatnonzero(c["active"])[-1] >= end_frames]
    for c in picked:
        # Störlinie: eine einzelne FFT-Linie (≤ 2 kHz) die ganze Zeit an
        # (AM nicht: ATIS/VOLMET senden dauernd mit schmalem Träger – dort entscheidet die Tonprüfung)
        if c["mode"] != "am" and c["width_hz"] <= 3000 and c["frac"] > 0.9:
            rejected += 1
            log(f"      {c['rf']/1e6:10.5f} MHz  Störlinie ({c['width_hz']:.0f} Hz breit, dauernd) – verworfen")
            continue
        act = np.flatnonzero(c["active"])
        a = max(0, act[0] - int(0.3 / frame_s)) * NFFT
        b = min(frames, act[-1] + int(0.5 / frame_s)) * NFFT
        zi, zq = dev._slice(i[a:b], q[a:b], center, c["rf"], AUDIO_RATE)
        # Bewertet wird nur der aktive Teil (ohne Vor-/Nachlauf)
        ca = (act[0] * NFFT - a) * AUDIO_RATE // int(rate)
        cb = (act[-1] * NFFT - a) * AUDIO_RATE // int(rate)
        core = (zi[ca:cb], zq[ca:cb])
        # Läuft der Durchgang bis zum Ende der Aufnahme: weiterhören
        if act[-1] >= end_frames:
            if c in to_follow and float(cfg.get("qso_max_seconds", 120)) > 0:
                # Sprache/Relais: dranbleiben, bis der Durchgang (das Gespräch) endet
                if follow is None:
                    follow = follow_qso(dev, center, to_follow, cfg, log)
                xi, xq = follow.get(c["rf"], (np.zeros(0), np.zeros(0)))
                if len(xi):                                  # wie der erste Teil auf den Spitzenwert beziehen
                    pk = float(np.max(np.abs(xi + 1j * xq))) or 1.0
                    xi, xq = xi / pk * 0.8, xq / pk * 0.8
            else:
                if extra is None:
                    extra = dev._grab(center, float(cfg.get("record_seconds", 6.0)))
                xi, xq = dev._slice(extra[0], extra[1], center, c["rf"], AUDIO_RATE)
            # gleicher Maßstab wie der erste Teil: beide Stücke auf ihren Spitzenwert bezogen
            zi, zq = np.concatenate([zi, xi]), np.concatenate([zq, xq])
        verdict, score = audio_quality(core[0], core[1], AUDIO_RATE, c["mode"])
        if verdict == "rauschen" and c["mode"] not in NOISY_OK:
            rejected += 1
            log(f"      {c['rf']/1e6:10.5f} MHz  nur Rauschen im Ton ({score:+.1f} dB) – verworfen")
            continue
        if verdict == "traeger" and c["frac"] > 0.9:
            rejected += 1
            log(f"      {c['rf']/1e6:10.5f} MHz  Dauerträger ohne Ton – verworfen")
            continue
        RECDIR.mkdir(parents=True, exist_ok=True)
        path = RECDIR / recording_name(band["name"], c["rf"])
        peak = float(np.max(np.abs(zi + 1j * zq))) or 1.0
        wav_write_iq(path, zi / peak * 0.8, zq / peak * 0.8, AUDIO_RATE)
        hits.append({
            "rf_hz": float(c["rf"]), "offset_hz": float(c["rf"] - center),
            "level_db": noise_db + c["level"], "snr_db": c["level"],
            "recording_path": str(path), "rec_center_hz": float(c["rf"]),
            "segment_id": c["seg"].get("id"), "mode": c["mode"], "sideband": None,
            "audio": verdict, "audio_score": round(score, 1),
            "active_s": round(len(act) * frame_s, 1), "width_hz": c["width_hz"],
        })
        log(f"      {c['rf']/1e6:10.5f} MHz  {c['mode']:6s} {c['level']:4.1f} dB  "
            f"{len(act)*frame_s:4.1f} s aktiv  {c['width_hz']/1000:4.1f} kHz breit  Ton: {verdict} ({score:+.1f} dB)")
    return hits, rejected


# ====================================================================== Sprachwache (SSB, PMSDR)
#
# SSB-Sprache verteilt ihre Energie auf ~2,4 kHz und spricht in Silben mit Pausen. Die bisherige
# Spitzensuche (schmale FFT-Linien, Median über 0,8 s) sieht davon praktisch nichts: auf jeder
# einzelnen 6-Hz-Linie liegt eine 30-dB-Station nur ~4 dB über dem Rauschen, und der Median
# über die Zeit bügelt die Silben weg. Hier wird stattdessen die Energie in einem 2,4-kHz-Fenster
# über die Zeit gemessen – das, was ein Ohr am Empfänger auch tut.

VOICE_BW = 2400.0
VOICE_HOP = 500.0


def _voice_floor(P):
    """Grundrauschen je Bin: Median über die Zeit, dann ein unteres Quantil über die Bins –
    robust auch dann, wenn das Band (Contest!) zur Hälfte belegt ist."""
    per_bin = np.median(P, axis=0)
    return float(np.percentile(per_bin, 25))


def watch_voice(dev, band, center, cfg, selected, owner_half_width, corrected_rf,
                allowed=lambda rf: True, log=print):
    """Ein PMSDR-Fenster einige Sekunden belauschen und SSB-Sprachstationen finden.
    Rückgabe: (Treffer, verworfen). Treffer tragen recording_path (ganzes 48-kHz-Fenster)."""
    from devices import RECDIR, recording_name, wav_write_iq

    rate = float(dev.rate)
    seconds = float(cfg.get("voice_dwell_seconds", 3.0))
    thr = float(cfg.get("voice_threshold_db", 6.0))
    nfft = 1024 if rate <= 50000 else int(2 ** np.ceil(np.log2(rate / 47.0)))
    bin_hz = rate / nfft

    # Sprachsegmente dieses Fensters (Bereiche in RF)
    sel = set(selected or ["all"])
    segs = [s for s in band.get("segments", []) if s.get("mode") == "voice"
            and ("all" in sel or s.get("id") in sel)]
    if not segs:
        return [], 0

    dev.tune(center)
    i, q = dev.capture(seconds)
    z = (i - 1j * q) if cfg.get("iq_conjugate", False) else (i + 1j * q)
    n = len(z) // nfft
    if n < 10:
        return [], 0
    win = np.hanning(nfft)
    S = np.fft.fftshift(np.fft.fft(z[: n * nfft].reshape(n, nfft) * win, axis=1), axes=1)
    P = (S.real ** 2 + S.imag ** 2)
    f = np.fft.fftshift(np.fft.fftfreq(nfft, 1.0 / rate))
    floor = _voice_floor(P)

    # Schmale Dauerlinien ausblenden: der Gleichanteil (DC) der PMSDR in der Fenstermitte und
    # Störlinien (Netzteil, Taktoberwellen) liegen fast die ganze Zeit an, Sprache nicht.
    # Ohne diese Maske hielt die Sprachwache die DC-Linie samt Nachbarlinien für eine Station.
    per_bin_db = 10 * np.log10(np.median(P, axis=0) / floor + 1e-12)
    line = per_bin_db > float(cfg.get("voice_line_db", 8.0))
    line = np.convolve(line.astype(float), np.ones(5), mode="same") > 0          # ±2 Bins Rand
    # Gleichanteil der PMSDR samt „Rock“ (gemessen an echten 80-m-Aufnahmen: ±500 Hz über Rauschen)
    dc = np.abs(f) < max(float(cfg.get("voice_dc_guard_hz", 650)), float(cfg.get("dc_guard_hz", 300)))
    mask = line | dc
    P = P.copy()
    P[:, mask] = floor

    edge = float(cfg.get("edge_guard_hz", 2500))
    usable = rate / 2 - edge             # ganzes Fenster absuchen …
    owner = owner_half_width             # … aber nur Stationen in der eigenen Fensterhälfte annehmen
    box = int(round(VOICE_BW / bin_hz))
    # Breitbandige Störimpulse (Netz, PLC): Rahmen, in denen fast das ganze Fenster hochgeht, ignorieren
    # Rahmen, in denen das ganze Fenster hochgeht (Pulsstörung), gegen den typischen Rahmen messen
    fm = np.median(P, axis=1)
    frame_db = 10 * np.log10(fm / np.median(fm) + 1e-12)
    impulse = frame_db > float(cfg.get("voice_impulse_db", 1.5))
    impulse = np.convolve(impulse.astype(float), np.ones(3), mode="same") > 0      # Nachbarrahmen mit

    def box_series(o):
        b = int(np.searchsorted(f, o - VOICE_BW / 2))
        blk = P[:, b:b + box]
        if blk.shape[1] < box // 2:
            return None
        return np.convolve(blk.mean(axis=1), np.ones(3) / 3, mode="same")

    def broadband_excess(o, own_db, act):
        """Pulsstörungen (Netzteile, PLC) heben viele kHz gleichzeitig an. Maß: wie weit liegt
        dieses Fenster in seinen aktiven Rahmen ÜBER den Fenstern 5–12 kHz daneben (Median, dB)?
        Station: etwa ihr eigener Pegel. Pulsstörung: nahe 0."""
        others = []
        for d in (-12000, -9000, -6000, 6000, 9000, 12000):
            if abs(o + d) > usable - VOICE_BW / 2:
                continue
            ser = box_series(o + d)
            if ser is not None:
                others.append(10 * np.log10(ser / floor + 1e-12))
        if not others:
            return 99.0
        quiet = ~impulse
        # Anstieg gegenüber dem eigenen Ruhepegel – bei Pulsstörung steigen alle mit
        rise_own = own_db - np.median(own_db[quiet])
        rise_nb = np.median(np.vstack([o_ - np.median(o_[quiet]) for o_ in others]), axis=0)
        return float(np.median(rise_own[act] - rise_nb[act]))

    cands = []
    off = -usable + VOICE_BW / 2
    while off <= usable - VOICE_BW / 2:
        b0 = int(np.searchsorted(f, off - VOICE_BW / 2))
        blk = P[:, b0:b0 + box]
        if blk.shape[1] < box // 2:
            off += VOICE_HOP
            continue
        ch = 10 * np.log10(blk.mean(axis=1) / floor + 1e-12)
        ch = np.convolve(ch, np.ones(3) / 3, mode="same")          # ~60 ms glätten
        act = (ch >= thr) & ~impulse
        frac = float(act.sum()) / max(1, int((~impulse).sum()))     # Anteil der störungsfreien Rahmen
        if frac >= 0.08:
            spec = 10 * np.log10(blk[act].mean(axis=0) / floor + 1e-12)
            width = float((spec >= 6.0).sum() * bin_hz)
            var = float(np.std(ch[act])) if act.sum() > 3 else 0.0
            # Kanten der Sprache aus den aktiven Rahmen (±1,2 kHz Rand um das Fenster)
            w0, w1 = max(0, b0 - box // 2), min(nfft, b0 + box + box // 2)
            wide = 10 * np.log10(P[act, w0:w1].mean(axis=0) / floor + 1e-12)
            occ = np.flatnonzero(wide >= max(6.0, float(wide.max()) - 25.0))
            lo_e = float(f[w0 + occ[0]]) if len(occ) else None
            hi_e = float(f[w0 + occ[-1]]) if len(occ) else None
            # Sprache hat Pausen zwischen Silben und Sätzen: Pegel schwankt um 20–30 dB. Rauschen an den
            # Fensterrändern oder auf ruhigen Bändern liegt nur knapp über der Schwelle und schwankt
            # kaum (gemessen: echte SSB-Sprache 40 m 24–33 dB, Fehlerkennungen 15 m nachts 1–10 dB).
            dyn = float(np.percentile(ch, 90) - np.percentile(ch, 10))
            cands.append(dict(off=off, frac=frac, level=float(np.median(ch[act])), width=width, dyn=dyn,
                              var=var, spec=spec, lo_e=lo_e, hi_e=hi_e,
                              excess=broadband_excess(off, ch, act)))
        off += VOICE_HOP

    # Nur echte Maxima über die Fensterlage (sonst erzeugt die Flanke einer Station einen
    # Geisttreffer daneben), dann stärkste zuerst mit ≥ 2,8 kHz Abstand
    score = {round(c["off"]): c["level"] * c["frac"] for c in cands}
    def is_peak(c):
        o = round(c["off"])
        return all(score.get(round(o + d), -1) <= score[o] for d in (-VOICE_HOP, VOICE_HOP, -2 * VOICE_HOP, 2 * VOICE_HOP))
    cands = [c for c in cands if is_peak(c)]
    cands.sort(key=lambda c: -c["level"] * c["frac"])
    picked = []
    for c in cands:
        if all(abs(c["off"] - p["off"]) >= 2800 for p in picked):
            picked.append(c)

    hits, rejected = [], 0
    rec = None                           # Aufnahme dieses Fensters: Lauschzeit + Nachlauf
    for c in picked:
        dc_edge = max(float(cfg.get("voice_dc_guard_hz", 650)), float(cfg.get("dc_guard_hz", 300)))
        dc_skip = dc_edge + VOICE_BW / 2 + 300
        if abs(c["off"]) < dc_skip:
            continue                     # liegt über dem Gleichanteil – das Nachbarfenster sieht sie sauber
        if abs(c["off"]) > owner + VOICE_HOP:
            # gehört eigentlich zum Nachbarfenster; nur nehmen, wenn sie dort über dessen
            # Gleichanteil läge (dort wird sie übersprungen). Dubletten fängt der Scanner ab.
            if abs(abs(c["off"]) - 2 * owner) >= dc_skip:
                continue
        rf_mid = corrected_rf(center, c["off"])
        seg = next((s for s in segs for a, b in s.get("ranges", []) if min(a, b) <= rf_mid <= max(a, b)), None)
        if seg is None or not allowed(rf_mid):
            continue
        side = str(seg.get("sideband") or ("LSB" if rf_mid < 10e6 else "USB")).upper()
        # Sprache: breit (> 600 Hz belegt) und schwankend (Silben); Träger/RTTY sind schmal und gleichmäßig
        if c["excess"] < float(cfg.get("voice_min_excess_db", 3.0)):
            rejected += 1
            log(f"      {rf_mid/1e6:10.5f} MHz  breitbandige Störung (nur {c['excess']:.1f} dB über "
                f"den Nachbarfenstern) – verworfen")
            continue
        # in 3 s Lauschzeit hat auch echte, dichte Sprache mitunter nur ~11 dB Schwankung (gemessen),
        # Fehlerkennungen auf ruhigen Bändern 1–8 dB
        if c["dyn"] < float(cfg.get("voice_min_dyn_db", 9.5)):
            rejected += 1
            log(f"      {rf_mid/1e6:10.5f} MHz  keine Sprechpausen (Pegel schwankt nur {c['dyn']:.1f} dB) – verworfen")
            continue
        if c["width"] < 600 or (c["var"] < 1.0 and c["frac"] > 0.9):
            rejected += 1
            log(f"      {rf_mid/1e6:10.5f} MHz  kein Sprachsignal ({c['width']:.0f} Hz breit, "
                f"Schwankung {c['var']:.1f} dB) – verworfen")
            continue
        # Träger aus der Sprachkante (Sprache beginnt ~300 Hz neben dem Träger); passt die Kante
        # nicht zur Fensterlage, grob aus der Fenstermitte
        carrier = rf_mid + 1500.0 if side == "LSB" else rf_mid - 1500.0
        edge_off = (c["hi_e"] + 300.0) if side == "LSB" else (c["lo_e"] - 300.0) if c["lo_e"] is not None else None
        # Kante direkt am ausgeblendeten Gleichanteil ist abgeschnitten, nicht echt -> grob schätzen
        dc_edge = max(float(cfg.get("voice_dc_guard_hz", 650)), float(cfg.get("dc_guard_hz", 300)))
        for e in (c["hi_e"], c["lo_e"]):
            if e is not None and (abs(abs(e) - dc_edge) < 150.0 or abs(e) > usable - 300.0):
                edge_off = None          # am Gleichanteil oder am Fensterrand abgeschnitten
        if edge_off is not None:
            est = corrected_rf(center, edge_off)
            if abs(est - carrier) <= 1200.0:
                carrier = est
        if rec is None:
            # an die gerade belauschten Sekunden direkt weiter aufnehmen (record_seconds),
            # damit die Trefferaufnahme lang genug zum Zuhören ist
            try:
                xi, xq = dev.capture(float(cfg.get("record_seconds", 10.0)))
                rec = (np.concatenate([i, xi]), np.concatenate([q, xq]))
            except Exception:
                rec = (i, q)
        RECDIR.mkdir(parents=True, exist_ok=True)
        path = RECDIR / recording_name(band["name"], carrier)
        wav_write_iq(path, rec[0], rec[1], rate)                    # ganzes Fenster (wie bisher bei PMSDR)
        hits.append({
            "rf_hz": float(carrier), "offset_hz": float(carrier - corrected_rf(center, 0.0)),
            "level_db": 10 * np.log10(floor + 1e-20) + c["level"], "snr_db": c["level"],
            "recording_path": str(path), "rec_center_hz": float(center),
            "segment_id": seg.get("id"), "mode": "voice", "sideband": side,
            "audio": "ton", "audio_score": round(c["var"], 1),
            "active_s": round(c["frac"] * seconds, 1), "width_hz": c["width"],
            "voice_mid_hz": float(rf_mid),
        })
        log(f"      {carrier/1e6:10.5f} MHz  {side} {c['level']:4.1f} dB  {c['frac']*seconds:4.1f} s aktiv  "
            f"{c['width']:4.0f} Hz breit  Schwankung {c['var']:.1f} dB")
    return hits, rejected
