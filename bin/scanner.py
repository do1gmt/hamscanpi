#!/usr/bin/env python3

import argparse
import datetime
import json
from scan_plan import make_plan
from segment_guard import rf_allowed, decoder_allowed
from devices import effective_config, open_device, detect_hardware
DEVICE_NAMES = {"pmsdr": "PMSDR", "rtlsdr": "RTL-SDR"}
import channel_watch as CW
import ft8_watch as FW
from applog import log as applog
import math
import os
import re
import sqlite3
import stat
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks, resample_poly


BASE = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE / "config" / "scanner.json"
WEB_SETTINGS_FILE = BASE / "config" / "web_settings.json"
DB_FILE = BASE / "data" / "scanner.db"


def timestamp():
    # Immer UTC
    return datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat(timespec="seconds")


def load_config():
    import bandplan as BP                              # Bandplan des gewählten Landes (Region)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return BP.apply(json.load(f))



def selected_scan_bands(cfg):

    all_bands = {
        str(x["name"])
        for x in cfg.get(
            "ranges",
            []
        )
        if x.get("name")
    }

    try:
        data = json.loads(
            WEB_SETTINGS_FILE.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return {
            "80m",
            "15m"
        } & all_bands

    selected = data.get(
        "selected_bands"
    )

    if not isinstance(
        selected,
        list
    ):
        return {
            "80m",
            "15m"
        } & all_bands

    result = {
        str(x)
        for x in selected
        if str(x) in all_bands
    }

    if not result:
        return {
            "80m",
            "15m"
        } & all_bands

    return result


def find_alsa_device(name, device):
    r = subprocess.run(
        ["env", "LC_ALL=C", "arecord", "-l"],
        text=True,
        capture_output=True
    )

    if r.returncode != 0:
        raise RuntimeError("arecord -l fehlgeschlagen")

    for line in r.stdout.splitlines():
        if name.lower() not in line.lower():
            continue

        m = re.search(
            r"(?:card|karte)\s+(\d+):.*(?:device|gerät)\s+(\d+):",
            line,
            re.IGNORECASE
        )

        if m:
            card = int(m.group(1))
            dev = int(m.group(2))

            if dev == device:
                return card, dev

    raise RuntimeError(
        f"ALSA-Gerät '{name}', device {device} nicht gefunden"
    )


def open_fifo(path):
    if not os.path.exists(path):
        raise RuntimeError(f"FIFO fehlt: {path}")

    mode = os.stat(path).st_mode

    if not stat.S_ISFIFO(mode):
        raise RuntimeError(f"Kein FIFO: {path}")

    try:
        return os.open(
            path,
            os.O_WRONLY | os.O_NONBLOCK
        )
    except OSError as e:
        raise RuntimeError(
            f"PMSDR-FIFO nicht beschreibbar: {e}"
        )


def tune(fd, frequency):
    cmd = f"f {int(frequency)}\n".encode("ascii")
    os.write(fd, cmd)


def capture_iq(card, device, rate, seconds):
    cmd = [
        "arecord",
        "-q",
        "-D", f"plughw:{card},{device}",
        "-f", "S16_LE",
        "-r", str(rate),
        "-c", "2",
        "-t", "raw"
    ]

    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    time.sleep(seconds)

    p.terminate()

    try:
        raw, err = p.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        p.kill()
        raw, err = p.communicate()

    if len(raw) < rate:
        raise RuntimeError(
            f"Zu wenig Audiodaten: {len(raw)} Byte"
        )

    samples = np.frombuffer(raw, dtype="<i2")

    if len(samples) % 2:
        samples = samples[:-1]

    samples = samples.reshape(-1, 2)

    i = samples[:, 0].astype(np.float64) / 32768.0
    q = samples[:, 1].astype(np.float64) / 32768.0

    i -= np.mean(i)
    q -= np.mean(q)

    return i, q


def analyse(i, q, cfg):
    if cfg.get("iq_conjugate", False):
        z = i - 1j * q
    else:
        z = i + 1j * q

    nfft = int(cfg["fft_size"])

    if len(z) < nfft:
        raise RuntimeError(
            f"Zu wenig Samples für FFT {nfft}"
        )

    step = nfft // 2
    window = np.hanning(nfft)
    win_norm = np.sum(window)

    spectra = []

    for start in range(0, len(z) - nfft + 1, step):
        block = z[start:start+nfft]

        fft = np.fft.fftshift(
            np.fft.fft(block * window)
        )

        amplitude = np.abs(fft) / win_norm
        spectra.append(amplitude)

    if not spectra:
        raise RuntimeError("Keine FFT-Blöcke")

    spec = np.median(
        np.asarray(spectra),
        axis=0
    )

    spec_db = 20.0 * np.log10(
        np.maximum(spec, 1e-12)
    )

    rate = int(cfg["sample_rate"])

    freqs = np.fft.fftshift(
        np.fft.fftfreq(nfft, d=1.0/rate)
    )

    valid = np.ones(nfft, dtype=bool)

    edge = float(cfg["edge_guard_hz"])
    dc = float(cfg["dc_guard_hz"])

    valid[np.abs(freqs) >
          (rate / 2.0 - edge)] = False

    valid[np.abs(freqs) < dc] = False

    noise = float(np.median(spec_db[valid]))

    threshold = noise + float(cfg["threshold_db"])

    bin_hz = rate / nfft

    min_distance = max(
        1,
        int(
            float(cfg["min_peak_spacing_hz"])
            / bin_hz
        )
    )

    detection = spec_db.copy()
    detection[~valid] = -300.0

    peaks, _ = find_peaks(
        detection,
        height=threshold,
        distance=min_distance
    )

    results = []

    for p in peaks:
        results.append({
            "offset_hz": float(freqs[p]),
            "level_db": float(spec_db[p]),
            "snr_db": float(spec_db[p] - noise)
        })

    results.sort(
        key=lambda x: x["snr_db"],
        reverse=True
    )

    results = results[
        :int(cfg["max_peaks_per_tune"])
    ]

    rms_i = float(np.sqrt(np.mean(i*i)))
    rms_q = float(np.sqrt(np.mean(q*q)))

    return {
        "noise_db": noise,
        "rms_i": rms_i,
        "rms_q": rms_q,
        "peaks": results
    }


_METER_HIST = []
METER_FILE = Path(__file__).resolve().parent.parent / "data" / "meter.json"


def scan_meter(band, center, result):
    """Pegelanzeige im Dashboard: stärkster Treffer des gerade abgesuchten Fensters (dB über Rauschen)."""
    try:
        peaks = result.get("peaks") or []
        top = max(peaks, key=lambda p: p.get("snr_db", -99), default=None)
        db = round(float(top["snr_db"]), 1) if top else 0.0
        _METER_HIST.append(db)
        del _METER_HIST[:-60]
        rf = (center + top["offset_hz"]) if top and top.get("offset_hz") is not None else None
        lab = f"{band} · stärkstes Signal {rf/1e6:.4f} MHz" if rf else f"{band} {center/1e6:.3f} MHz · nichts über der Schwelle"
        tmp = METER_FILE.with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(json.dumps({"t": time.time(), "src": "scan",
                                   "items": [{"label": lab, "db": db, "hist": list(_METER_HIST)}]}), encoding="utf-8")
        os.replace(tmp, METER_FILE)
    except Exception:
        pass


def frequencies(r):
    f = int(r["start_hz"])
    stop = int(r["stop_hz"])
    step = int(r["step_hz"])

    while f <= stop:
        yield f
        f += step


def detect_birdies(scan_results, cfg):
    tolerance = float(
        cfg.get("birdie_tolerance_hz", 120)
    )

    min_fraction = float(
        cfg.get("birdie_min_fraction", 0.35)
    )

    offsets = []

    for result in scan_results:
        for peak in result["result"]["peaks"]:
            offsets.append(
                peak["offset_hz"]
            )

    if not offsets:
        return []

    clusters = []

    for offset in sorted(offsets):
        matched = False

        for cluster in clusters:
            if abs(offset - cluster["center"]) <= tolerance:
                cluster["values"].append(offset)
                cluster["center"] = float(
                    np.median(cluster["values"])
                )
                matched = True
                break

        if not matched:
            clusters.append({
                "center": offset,
                "values": [offset]
            })

    tuning_count = len(scan_results)

    birdies = []

    for cluster in clusters:
        hit_tunes = 0

        for result in scan_results:
            found = False

            for peak in result["result"]["peaks"]:
                if abs(
                    peak["offset_hz"] -
                    cluster["center"]
                ) <= tolerance:
                    found = True
                    break

            if found:
                hit_tunes += 1

        fraction = (
            hit_tunes / tuning_count
            if tuning_count else 0
        )

        # Ein Birdie muss in mehreren Fenstern auftauchen; bei wenigen Fenstern (RTL-SDR) sonst Fehlalarm
        if (fraction >= min_fraction and hit_tunes >= 3) or hit_tunes >= int(cfg.get("birdie_min_hits", 4)):
            birdies.append({
                "offset_hz": cluster["center"],
                "fraction": fraction,
                "hits": hit_tunes
            })

    birdies.sort(
        key=lambda x: x["fraction"],
        reverse=True
    )

    return birdies



def corrected_rf_hz(center_hz, offset_hz, cfg):
    ppm = float(
        cfg.get("frequency_correction_ppm", 0.0)
    )

    corrected_center = (
        center_hz *
        (1.0 + ppm * 1e-6)
    )

    return corrected_center + offset_hz


def is_birdie(offset, birdies, cfg):
    tolerance = float(
        cfg.get("birdie_tolerance_hz", 120)
    )

    for birdie in birdies:
        # Birdies treten bei unserem PMSDR
        # häufig spiegelsymmetrisch um DC auf.
        # Deshalb +f und -f als gleiche
        # Störfamilie behandeln.
        if abs(
            abs(offset) -
            abs(birdie["offset_hz"])
        ) <= tolerance:
            return True

    return False



def frequency_in_selected_segments(
    rf_hz,
    band,
    selected_segments
):
    """
    Ordnet eine Trefferfrequenz dem spezifischsten konfigurierten
    Segment zu und akzeptiert sie nur, wenn dieses Segment gewaehlt ist.

    Prioritaet:
      1. explizite frequencies_hz (z.B. FT8 7.074 MHz)
      2. schmalster passender Bereich

    Damit gewinnt z.B. FT8 gegen das ueberlappende 40-m-Sprachsegment.
    """
    if not selected_segments or "all" in selected_segments:
        return True

    selected = {str(x) for x in selected_segments}
    matches = []

    # Explizite Betriebsfrequenzen bekommen ein kleines Schutzfenster.
    # Das deckt auch einzelne FFT-Peaks innerhalb eines FT8-Audiobandes ab.
    special_guard_hz = 3000.0

    for seg in band.get("segments", []):
        seg_id = str(seg.get("id", ""))
        if not seg_id:
            continue

        for f in seg.get("frequencies_hz", []):
            distance = abs(float(rf_hz) - float(f))
            if distance <= special_guard_hz:
                # Rang 0 = spezifischer als jeder Bereich.
                matches.append((0, distance, seg_id))

        for a, b in seg.get("ranges", []):
            lo = float(min(a, b))
            hi = float(max(a, b))
            if lo <= float(rf_hz) <= hi:
                width = hi - lo
                # Rang 1; bei Ueberlappung gewinnt der schmalere Bereich.
                matches.append((1, width, seg_id))

    if not matches:
        return False

    best_rank = min((rank, width) for rank, width, _ in matches)
    best_segments = {
        seg_id
        for rank, width, seg_id in matches
        if (rank, width) == best_rank
    }

    return bool(best_segments & selected)


def filter_peaks(center, band, result, birdies, cfg, selected_segments=None):
    start_hz = float(band["start_hz"])
    stop_hz = float(band["stop_hz"])
    scan_step = float(band["step_hz"])

    owner_half_width = scan_step / 2.0

    filtered = []

    for peak in result["peaks"]:
        offset = peak["offset_hz"]
        rf_hz = corrected_rf_hz(center, offset, cfg)

        # 1. Nur innerhalb des Klasse-E-Bandes
        if rf_hz < start_hz or rf_hz > stop_hz:
            continue

        # 2. Nur Treffer aus den aktuell ausgewaehlten Segmenten
        if not frequency_in_selected_segments(
            rf_hz,
            band,
            selected_segments
        ):
            continue

        # 3. Jedes Signal gehört nur zum nächstgelegenen
        #    Abstimmfenster -> keine Doppelzählung
        if abs(offset) > owner_half_width:
            continue

        # 4. Wiederkehrende Basisband-Birdies entfernen
        if is_birdie(offset, birdies, cfg):
            continue

        filtered.append({
            **peak,
            "rf_hz": rf_hz
        })

    return filtered




def record_confirmed_iq(card, device, rate, seconds, band, rf_hz):
    recdir = BASE / "recordings"
    recdir.mkdir(parents=True, exist_ok=True)

    now = datetime.datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    safe_band = re.sub(
        r"[^A-Za-z0-9_-]",
        "_",
        band
    )

    filename = (
        f"{now}_{safe_band}_"
        f"{int(round(rf_hz))}Hz.wav"
    )

    path = recdir / filename

    cmd = [
        "arecord",
        "-q",
        "-D", f"plughw:{card},{device}",
        "-f", "S16_LE",
        "-r", str(rate),
        "-c", "2",
        "-d", str(int(math.ceil(seconds))),
        str(path)
    ]

    r = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True
    )

    if r.returncode != 0:
        raise RuntimeError(
            "Trefferaufnahme fehlgeschlagen: "
            + r.stderr.strip()
        )

    return str(path)




def is_near_configured_ft8(band, rf_hz, tolerance_hz=3500.0):
    rf_hz = float(rf_hz)

    for seg in band.get("segments", []):
        if str(seg.get("id", "")).lower() != "ft8":
            continue

        for f in seg.get("frequencies_hz", []):
            if abs(rf_hz - float(f)) <= float(tolerance_hz):
                return True

    return False


HF_FOLLOW_BW = {"cw": 200.0, "rtty": 600.0, "digital": 600.0}   # Messbreite (Hz) beim Weiter-Aufnehmen


def read_recorded_iq(path, cfg):
    with wave.open(str(path), "rb") as w:
        channels = w.getnchannels()
        rate = w.getframerate()
        width = w.getsampwidth()
        frames = w.readframes(
            w.getnframes()
        )

    if channels != 2 or width != 2:
        raise RuntimeError(
            "FT8-Prüfung erwartet 16-Bit Stereo-I/Q"
        )

    samples = np.frombuffer(
        frames,
        dtype="<i2"
    )

    if len(samples) % 2:
        samples = samples[:-1]

    samples = samples.reshape(
        -1,
        2
    )

    i = (
        samples[:, 0]
        .astype(np.float64)
        / 32768.0
    )

    q = (
        samples[:, 1]
        .astype(np.float64)
        / 32768.0
    )

    i -= np.mean(i)
    q -= np.mean(q)

    if cfg.get(
        "iq_conjugate",
        False
    ):
        z = i - 1j*q
    else:
        z = i + 1j*q

    return z, rate


def ft8_signature(
    path,
    center_hz,
    rf_hz,
    cfg
):
    """
    Breitbandige FT8-Aktivitätserkennung.

    Sucht nicht nur direkt um den einzelnen
    gefundenen Peak, sondern über ca. +/-3 kHz
    nach schmalen 8-FSK-artigen Strukturen mit
    etwa 160-ms-Symbolen.
    """

    try:
        z, rate = read_recorded_iq(
            path,
            cfg
        )
    except Exception:
        return False, {}

    ppm = float(
        cfg.get(
            "frequency_correction_ppm",
            0.0
        )
    )

    corrected_center = (
        center_hz *
        (1.0 + ppm * 1e-6)
    )

    offset = (
        rf_hz -
        corrected_center
    )

    n = np.arange(len(z))

    # Treffer zunächst um 0 Hz legen.
    z = (
        z *
        np.exp(
            -2j *
            np.pi *
            offset *
            n /
            rate
        )
    )

    frame_len = int(
        round(
            rate * 0.160
        )
    )

    if len(z) < frame_len * 12:
        return False, {}

    hop = frame_len // 2

    window = np.hanning(
        frame_len
    )

    freqs = np.fft.fftshift(
        np.fft.fftfreq(
            frame_len,
            d=1.0 / rate
        )
    )

    wide_mask = (
        (freqs >= -3200)
        &
        (freqs <= 3200)
    )

    wf = freqs[wide_mask]

    spectra = []

    for pos in range(
        0,
        len(z) - frame_len + 1,
        hop
    ):
        block = (
            z[
                pos:
                pos + frame_len
            ]
            *
            window
        )

        spec = np.abs(
            np.fft.fftshift(
                np.fft.fft(
                    block
                )
            )
        ) ** 2

        spectra.append(
            spec[wide_mask]
        )

    if len(spectra) < 15:
        return False, {}

    spectra = np.asarray(
        spectra
    )

    best = None

    # FT8 belegt nur ca. 50 Hz.
    # Wir prüfen über das ganze Audioband
    # überlappende Teilfenster.
    for search_center in np.arange(
        -3000.0,
        3000.1,
        25.0
    ):

        m = (
            (wf >= search_center - 65.0)
            &
            (wf <= search_center + 65.0)
        )

        if np.sum(m) < 8:
            continue

        subfreq = wf[m]
        sub = spectra[:, m]

        dominant = []
        quality = []

        for frame in sub:

            median = float(
                np.median(frame)
            ) + 1e-20

            k = int(
                np.argmax(frame)
            )

            peak = float(
                frame[k]
            )

            peak_ratio = (
                peak /
                median
            )

            # Schmaler Digitalton muss sich
            # klar vom lokalen Untergrund abheben.
            if peak_ratio < 7.0:
                continue

            a = max(
                0,
                k - 1
            )

            b = min(
                len(frame),
                k + 2
            )

            concentration = (
                float(
                    np.sum(
                        frame[a:b]
                    )
                )
                /
                (
                    float(
                        np.sum(frame)
                    )
                    + 1e-20
                )
            )

            if concentration < 0.15:
                continue

            dominant.append(
                float(
                    subfreq[k]
                )
            )

            quality.append(
                peak_ratio
            )

        if len(dominant) < 15:
            continue

        dominant = np.asarray(
            dominant
        )

        bins = np.round(
            dominant / 6.25
        ).astype(int)

        unique_bins = len(
            np.unique(bins)
        )

        changes = int(
            np.sum(
                bins[1:] !=
                bins[:-1]
            )
        )

        lo = float(
            np.percentile(
                dominant,
                10
            )
        )

        hi = float(
            np.percentile(
                dominant,
                90
            )
        )

        span = hi - lo

        median_quality = float(
            np.median(
                quality
            )
        )

        # FT8:
        # mehrere diskrete Töne,
        # kleine Gesamtbreite,
        # regelmäßige Tonwechsel.
        if not (
            3 <= unique_bins <= 12
            and
            span <= 75.0
            and
            changes >= 5
            and
            median_quality >= 8.0
        ):
            continue

        score = (
            len(dominant)
            +
            changes * 2
            +
            min(
                median_quality,
                30
            )
        )

        candidate = {
            "score": score,
            "center_hz":
                float(search_center),
            "frames":
                len(dominant),
            "tones":
                unique_bins,
            "span_hz":
                span,
            "changes":
                changes,
            "quality":
                median_quality
        }

        if (
            best is None
            or
            candidate["score"]
            >
            best["score"]
        ):
            best = candidate

    if best is None:
        return False, {}

    return True, best



def wait_for_ft8_slot():
    """
    FT8 beginnt jeweils auf UTC-Sekunde
    00, 15, 30 und 45.

    Aufnahme startet knapp nach der
    nächsten 15-Sekunden-Grenze.
    """

    now = time.time()

    next_slot = (
        math.floor(
            now / 15.0
        ) + 1
    ) * 15.0

    wait = (
        next_slot -
        now +
        0.08
    )

    if wait > 0:
        time.sleep(wait)


def rerecord_iq(
    path,
    card,
    device,
    rate,
    seconds
):
    """
    Vorhandene kurze Aufnahme durch
    längere I/Q-Aufnahme ersetzen.
    """

    path = Path(path)

    try:
        path.unlink()
    except FileNotFoundError:
        pass

    cmd = [
        "arecord",
        "-q",
        "-D",
        f"plughw:{card},{device}",
        "-f",
        "S16_LE",
        "-r",
        str(rate),
        "-c",
        "2",
        "-d",
        str(
            int(
                math.ceil(seconds)
            )
        ),
        str(path)
    ]

    r = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True
    )

    if r.returncode != 0:
        raise RuntimeError(
            "FT8-Langaufnahme fehlgeschlagen: "
            + r.stderr.strip()
        )


def classify_segment(band, rf_hz):
    """
    Ordnet eine Frequenz dem spezifischsten Segment des Bandplans zu.
    Rueckgabe: dict(id, mode, sideband, label) oder None.
    Prioritaet: explizite Betriebsfrequenz (3 kHz Fenster) vor schmalstem Bereich.
    """
    rf = float(rf_hz)
    best = None
    for seg in band.get("segments", []):
        seg_id = str(seg.get("id", ""))
        if not seg_id:
            continue
        for f in seg.get("frequencies_hz", []):
            d = abs(rf - float(f))
            if d <= 3000.0:
                cand = (0, d, seg)
                if best is None or cand[:2] < best[:2]:
                    best = cand
        for a, b in seg.get("ranges", []):
            lo, hi = float(min(a, b)), float(max(a, b))
            if lo <= rf <= hi:
                cand = (1, hi - lo, seg)
                if best is None or cand[:2] < best[:2]:
                    best = cand
    if best is None:
        return None
    seg = best[2]
    return {
        "id": str(seg.get("id")),
        "mode": str(seg.get("mode", "unknown")),
        "sideband": seg.get("sideband"),
        "label": str(seg.get("label", seg.get("id"))),
    }


def voice_side_for_frequency(band, rf_hz):
    """
    Liefert die SSB-Seite (USB/LSB), wenn rf_hz in einem Sprach- oder
    SSTV-Segment liegt - aus dem Bandplan, nicht aus dem Label.
    """
    info = classify_segment(band, rf_hz)
    if not info or info["mode"] not in ("voice", "sstv"):
        return None
    sb = str(info.get("sideband") or "").upper()
    return sb if sb in ("USB", "LSB") else None


def estimate_ssb_carrier(path, center_hz, rf_hz, band, cfg):
    """
    Schaetzt bei einem Treffer im Sprachsegment die unterdrueckte
    SSB-Traegerfrequenz aus den Kanten des belegten Spektrums.

    Sprache belegt ab dem Traeger ca. 250 ... 2800 Hz auf EINER Seite.
    Bei LSB liegt der Traeger knapp oberhalb der belegten Energie,
    bei USB knapp unterhalb. Die Spektralkante ist robust messbar,
    ein "Sprachscore" dagegen nicht (er ist spiegelsymmetrisch).

    read_recorded_iq() hat die I/Q-Konjugation bereits angewendet,
    deshalb hier KEINE weitere Seitenumkehr (das war der alte Fehler).

    Rueckgabe: (neue_rf_hz, info) oder (rf_hz, None).
    """
    real_side = voice_side_for_frequency(band, rf_hz)
    if real_side is None:
        return rf_hz, None

    try:
        z, rate = read_recorded_iq(path, cfg)
    except Exception:
        return rf_hz, None

    ppm = float(cfg.get("frequency_correction_ppm", 0.0))
    corrected_center = center_hz * (1.0 + ppm * 1e-6)
    base_offset = rf_hz - corrected_center

    nfft = 4096
    hop = nfft // 2
    win = np.hanning(nfft)
    acc = None
    count = 0
    for start in range(0, len(z) - nfft + 1, hop):
        seg = z[start:start + nfft] * win
        P = np.abs(np.fft.fft(seg)) ** 2
        acc = P if acc is None else acc + P
        count += 1
    if not count:
        return rf_hz, None
    psd = np.fft.fftshift(acc / count)
    freq = np.fft.fftshift(np.fft.fftfreq(nfft, 1.0 / rate))
    pdb = 10.0 * np.log10(psd + 1e-20)
    rel = freq - base_offset

    window = (rel >= -4500.0) & (rel <= 4500.0)
    if np.sum(window) < 50:
        return rf_hz, None
    noise = float(np.percentile(pdb[window], 25))
    occupied = window & (pdb > noise + 7.0)

    if not np.any(occupied):
        return rf_hz, None

    bin_hz = rate / nfft
    gap_bins = max(1, int(150.0 / bin_hz))
    hit_idx = int(np.argmin(np.abs(rel)))
    n = len(occupied)

    # zusammenhaengenden belegten Bereich um den Treffer suchen,
    # Luecken bis 150 Hz ueberbruecken
    lo = hit_idx
    i = hit_idx
    while i > 0:
        if np.any(occupied[max(0, i - gap_bins):i]):
            i -= 1
            if occupied[i]:
                lo = i
        else:
            break
    hi = hit_idx
    i = hit_idx
    while i < n - 1:
        if np.any(occupied[i + 1:min(n, i + 1 + gap_bins)]):
            i += 1
            if occupied[i]:
                hi = i
        else:
            break

    width = float(rel[hi] - rel[lo])

    # Linienkonzentration: steckt der Grossteil der Energie des belegten
    # Bereichs in einer schmalen Linie (+-30 Hz um das Maximum), ist das ein
    # Dauertraeger / Ton und keine Sprache - auch wenn die Breite passt.
    seg_lin = psd[lo:hi + 1]
    seg_rel = rel[lo:hi + 1]
    tonal = False
    line_fraction = 0.0
    if len(seg_lin) > 3 and np.sum(seg_lin) > 0:
        kmax = int(np.argmax(seg_lin))
        near = np.abs(seg_rel - seg_rel[kmax]) <= 30.0
        line_fraction = float(np.sum(seg_lin[near]) / np.sum(seg_lin))
        tonal = line_fraction > 0.5
    if width < 100.0:
        tonal = True      # einzelne schmale Linie

    # Breitbandiges Rauschen / Stoerer: glattes Spektrum ohne Struktur,
    # kaum Pegelschwankung ueber die Zeit (Sprache: Harmonische + Pausen)
    flatness = 0.0
    if len(seg_lin) > 8:
        flatness = float(np.exp(np.mean(np.log(seg_lin + 1e-20))) / (np.mean(seg_lin) + 1e-20))
    blk = max(1, rate // 10)
    levels = [np.mean(np.abs(z[k:k + blk]) ** 2) for k in range(0, len(z) - blk + 1, blk)]
    level_std = float(np.std(10 * np.log10(np.array(levels) + 1e-20))) if len(levels) > 3 else 9.9
    noise_like = (width > 3400.0 and flatness > 0.6) or (width > 2000.0 and level_std < 2.0 and flatness > 0.6)

    info = {
        "flatness": flatness,
        "level_std_db": level_std,
        "noise_like": noise_like,
        "side": real_side,
        "occupied_lo_hz": float(rel[lo]),
        "occupied_hi_hz": float(rel[hi]),
        "width_hz": width,
        "line_fraction": line_fraction,
        "tonal": tonal,
        "accepted": False,
        "delta_hz": 0.0,
        "score": width,
    }

    if tonal or noise_like:
        return rf_hz, info

    # Sprache ist 1.2 ... 3.4 kHz breit, sonst keine Korrektur.
    if width < 1200.0 or width > 3400.0:
        return rf_hz, info

    if real_side == "LSB":
        delta = float(rel[hi]) + 200.0
    else:
        delta = float(rel[lo]) - 200.0

    if abs(delta) > 3500.0:
        return rf_hz, info

    info["accepted"] = True
    info["delta_hz"] = delta
    return rf_hz + delta, info


def confirm_peaks(first_peaks, second_peaks, cfg):
    tolerance = float(
        cfg.get("confirm_tolerance_hz", 200)
    )

    min_snr = float(
        cfg.get("confirm_snr_db", 12.0)
    )

    confirmed = []

    for first in first_peaks:
        best = None
        best_delta = None

        for second in second_peaks:
            if second["snr_db"] < min_snr:
                continue

            delta = abs(
                second["rf_hz"] -
                first["rf_hz"]
            )

            if delta <= tolerance:
                if best is None or delta < best_delta:
                    best = second
                    best_delta = delta

        if best is not None:
            confirmed.append({
                "rf_hz": (
                    first["rf_hz"] +
                    best["rf_hz"]
                ) / 2.0,
                "offset_hz": (
                    first["offset_hz"] +
                    best["offset_hz"]
                ) / 2.0,
                "level_db": min(
                    first["level_db"],
                    best["level_db"]
                ),
                "snr_db": min(
                    first["snr_db"],
                    best["snr_db"]
                )
            })

    return confirmed


def open_database():
    db = sqlite3.connect(DB_FILE)

    db.execute("PRAGMA journal_mode=WAL")

    db.execute("""
        CREATE TABLE IF NOT EXISTS scan (
            id INTEGER PRIMARY KEY,
            time TEXT NOT NULL,
            band TEXT NOT NULL,
            center_hz INTEGER NOT NULL,
            noise_db REAL NOT NULL,
            rms_i REAL NOT NULL,
            rms_q REAL NOT NULL
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS peak (
            id INTEGER PRIMARY KEY,
            scan_id INTEGER NOT NULL,
            rf_hz REAL NOT NULL,
            offset_hz REAL NOT NULL,
            level_db REAL NOT NULL,
            snr_db REAL NOT NULL,
            recording_path TEXT,
            FOREIGN KEY(scan_id)
              REFERENCES scan(id)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS birdie (
            id INTEGER PRIMARY KEY,
            time TEXT NOT NULL,
            band TEXT NOT NULL,
            offset_hz REAL NOT NULL,
            hit_fraction REAL NOT NULL,
            hits INTEGER NOT NULL
        )
    """)

    try:                                                   # ab 1.7.18: Antenne je Scanfenster (Trefferliste)
        db.execute("ALTER TABLE scan ADD COLUMN antenna TEXT")
        db.commit()
    except sqlite3.OperationalError:
        pass

    for col, typ in (
        ("recording_path", "TEXT"),
        ("protected", "INTEGER NOT NULL DEFAULT 0"),
        ("segment_id", "TEXT"),
        ("mode", "TEXT"),
        ("sideband", "TEXT"),
        ("carrier_delta_hz", "REAL"),
        ("rec_center_hz", "REAL"),
        ("audio", "TEXT"),
        ("audio_score", "REAL"),
    ):
        try:
            db.execute(f"ALTER TABLE peak ADD COLUMN {col} {typ}")
            db.commit()
        except sqlite3.OperationalError:
            pass

    db.commit()
    return db


def listened_summary(band, cfg, centers, selected, plan_entry):
    """Für das Protokoll: abgehörte Bereiche (zusammengefasst), Fenster, geprüfte Kanäle, Digitalfrequenzen."""
    rate = float(cfg.get("sample_rate", 48000))
    if cfg.get("device") == "rtlsdr":
        half = min(float(band.get("step_hz", 900000)) / 2.0, rate / 2 * 0.85)
    else:
        half = rate / 2 - float(cfg.get("edge_guard_hz", 2500))
    spans = sorted((c - half, c + half) for c in centers)
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 1000:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    lo, hi = float(band["start_hz"]), float(band["stop_hz"])
    cover = [f"{max(a, lo)/1e6:.3f}–{min(b, hi)/1e6:.3f}" for a, b in merged if min(b, hi) > max(a, lo)]
    sel = set(selected or ["all"])
    digital = sorted({f for sg in band.get("segments", [])
                      if sg.get("mode") in ("ft8", "ft4", "sstv", "wspr") and ("all" in sel or str(sg.get("id")) in sel)
                      for f in sg.get("frequencies_hz", [])})
    out = {"segments": plan_entry.get("labels") or [],
           "coverage": cover,
           "windows": [f"{c/1e6:.5f}" for c in centers]}
    if cfg.get("device") == "rtlsdr":
        chans = set()
        for c in centers:
            chans.update(rf for rf, _ in CW.channel_list(band, selected, c - half, c + half))
        out["channels"] = len(chans)
    if digital:
        out["digital"] = [f"{f/1e6:.3f}" for f in digital]
    return out


CUR_ANTENNA = [None]          # Antenne des gerade benutzten Empfängers (System → Geräte)


def antenna_label(device):
    """Beschriftung der Antenne: RTL = Eintrag des Haupt-Sticks, sonst config antennas.<gerät>."""
    try:
        if device == "rtlsdr":
            import sticks as STK
            main = next((r for r in STK.load()["sticks"].values() if r.get("role") == "main"), None)
            if main and (main.get("antenna") or "").strip():
                return main["antenna"].strip()
        cfgf = Path(__file__).resolve().parent.parent / "config" / "scanner.json"
        return (json.loads(cfgf.read_text(encoding="utf-8")).get("antennas") or {}).get(device) or None
    except Exception:
        return None


def save_scan(db, band, center, result, peaks):
    cur = db.execute("""
        INSERT INTO scan
        (
            time,
            band,
            center_hz,
            noise_db,
            rms_i,
            rms_q,
            antenna
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        timestamp(),
        band,
        int(center),
        result["noise_db"],
        result["rms_i"],
        result["rms_q"],
        CUR_ANTENNA[0]
    ))

    scan_id = cur.lastrowid

    for peak in peaks:
        db.execute("""
            INSERT INTO peak
            (
                scan_id,
                rf_hz,
                offset_hz,
                level_db,
                snr_db,
                recording_path,
                segment_id,
                mode,
                sideband,
                carrier_delta_hz,
                rec_center_hz,
                audio,
                audio_score
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            scan_id,
            peak["rf_hz"],
            peak["offset_hz"],
            peak["level_db"],
            peak["snr_db"],
            peak.get("recording_path"),
            peak.get("segment_id"),
            peak.get("mode"),
            peak.get("sideband"),
            peak.get("carrier_delta_hz"),
            peak.get("rec_center_hz"),
            peak.get("audio"),
            peak.get("audio_score")
        ))

    db.commit()


def save_birdies(db, band, birdies):
    for birdie in birdies:
        db.execute("""
            INSERT INTO birdie
            (
                time,
                band,
                offset_hz,
                hit_fraction,
                hits
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            timestamp(),
            band,
            birdie["offset_hz"],
            birdie["fraction"],
            birdie["hits"]
        ))

    db.commit()


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--once",
        action="store_true",
        help="einen Scanzyklus"
    )

    args = parser.parse_args()

    base_cfg = load_config()
    cfg = base_cfg
    print("HamScanPi Scanner")
    print("-------------")

    devices = {}

    def get_device(name, dcfg):
        if name not in devices:
            try:
                devices[name] = open_device(name, dcfg)
            except Exception as e:
                applog("ERROR", "scanner", "device_open_failed", device=name, error=str(e))
                raise
            applog("INFO", "scanner", "device_opened", device=name, rate=devices[name].rate)
            print(f"Gerät      : {name} ({devices[name].rate} S/s)")
        return devices[name]

    db = open_database()

    total_scans = 0
    total_peaks = 0
    total_rejected = 0

    active_plan = make_plan(
        cfg
    )

    plan_by_band = {
        p["name"]: p
        for p in active_plan
    }

    print(
        "Scan-Bänder : " +
        ", ".join(
            p["name"]
            + "["
            + ",".join(
                p["segments"]
            )
            + "]"
            for p in active_plan
        )
    )

    hw = detect_hardware()
    missing, scanned_bands = [], 0
    try:
        for band in base_cfg["ranges"]:
            name = band["name"]

            if name not in plan_by_band:
                continue

            if band.get("satellite"):
                continue          # Satelliten nimmt der Scheduler bei jedem Überflug auf (sat_pass.py)
            cfg = effective_config(base_cfg, band)
            band = dict(band, step_hz=int(plan_by_band[name].get("step_hz", band["step_hz"])))   # Frequenzraster
            hw_dev = cfg["device"]
            if plan_by_band[name].get("via") == "rtlhf":
                # Kurzwelle mit dem Stick: Gerät „rtlhf“ (48-kHz-Fenster wie die PMSDR); ppm stellt rtl_sdr ein
                cfg = dict(cfg, device="rtlhf", frequency_correction_ppm=0.0)
                hw_dev = "rtlsdr"
                print(f"Band       : {name} mit dem Stick (Kurzwelle per Direktabtastung)")
            if not hw[hw_dev]["present"]:
                missing.append(name)
                print(f"Band       : {name} übersprungen – {DEVICE_NAMES.get(cfg['device'], cfg['device'])} nicht angeschlossen")
                applog("WARN", "scanner", "band_skipped", band=name, reason="device_missing", device=cfg["device"])
                continue
            scanned_bands += 1
            dev_obj = get_device(cfg["device"], cfg)
            CUR_ANTENNA[0] = antenna_label("rtlsdr" if cfg["device"] == "rtlhf" else cfg["device"])
            applog("DEBUG", "scanner", "band_start", band=name, device=cfg["device"], tunes=len(plan_by_band[name]))
            _band_t0, _band_p0, _band_r0 = time.time(), total_peaks, total_rejected

            centers = list(
                plan_by_band[
                    name
                ]["centers_hz"]
            )

            selected_segments = list(
                plan_by_band[name]["segments"]
            )

            print()
            print(
                f"Band       : {band['name']} "
                f"{band['start_hz']/1e6:.3f}-"
                f"{band['stop_hz']/1e6:.3f} MHz"
            )

            band_results = []
            band_seen_rf = []   # bereits bestaetigte Frequenzen dieses Bandes (Fensterueberlappung)

            # FT8/FT4 (PMSDR): einen Takt zeitgenau aufnehmen und direkt dekodieren, statt über
            # Spitzensuche -> Bestätigung -> Langaufnahme zu gehen (jede Stufe konnte FT8 verlieren)
            ft8_watch_on = cfg.get("device", "pmsdr") in ("pmsdr", "rtlhf") and cfg.get("ft8_watch", True)
            if ft8_watch_on:
                sel = set(str(x) for x in selected_segments) if selected_segments else {"all"}
                dig = [sg for sg in band.get("segments", [])
                       if sg.get("mode") in ("ft8", "ft4") and sg.get("frequencies_hz")
                       and ("all" in sel or str(sg.get("id")) in sel)]
                for sg in dig:
                    try:
                        fhits = FW.watch_ft8(dev_obj, band, sg, cfg)
                    except Exception as e:
                        applog("WARN", "scanner", "ft8_watch_failed", band=name, segment=sg.get("id"),
                               error=f"{type(e).__name__}: {e}")
                        print(f"   {sg.get('mode','').upper()}-Wache fehlgeschlagen: {e}")
                        fhits = []
                    if fhits:
                        total_peaks += len(fhits)
                        for p in fhits:
                            band_seen_rf.append(p["rf_hz"])
                            applog("INFO", "scanner", "hit", band=name, rf_hz=round(p["rf_hz"]),
                                   mode=p["mode"], note=p.get("note"))
                        save_scan(db, band["name"], int(fhits[0]["rec_center_hz"]),
                                  {"noise_db": 0.0, "rms_i": 0.0, "rms_q": 0.0}, fhits)
                # SSTV (Kurzwelle): kurz hineinhören, bei Signal ~2 min aufnehmen und das Bild dekodieren
                sstv = [sg for sg in band.get("segments", [])
                        if sg.get("mode") == "sstv" and sg.get("frequencies_hz")
                        and ("all" in sel or str(sg.get("id")) in sel)] if cfg.get("sstv_watch", True) else []
                for sg in sstv:
                    try:
                        import sstv_watch as SW
                        shits = SW.watch_sstv(dev_obj, band, sg, cfg)
                    except Exception as e:
                        applog("WARN", "scanner", "sstv_watch_failed", band=name, segment=sg.get("id"),
                               error=f"{type(e).__name__}: {e}")
                        print(f"   SSTV-Wache fehlgeschlagen: {e}")
                        shits = []
                    if shits:
                        total_peaks += len(shits)
                        for p in shits:
                            band_seen_rf.append(p["rf_hz"])
                            applog("INFO", "scanner", "hit", band=name, rf_hz=round(p["rf_hz"]),
                                   mode="sstv", note=p.get("note"))
                        save_scan(db, band["name"], int(shits[0]["rec_center_hz"]),
                                  {"noise_db": 0.0, "rms_i": 0.0, "rms_q": 0.0}, shits)
                dig = dig + sstv
                # Fenster, die nur für FT8/FT4/SSTV im Plan stehen, braucht die Spitzensuche nicht mehr
                dials = {int(f) for sg in dig for f in sg.get("frequencies_hz", [])}
                only_digital = all(str(sg.get("id")) in {str(d.get("id")) for d in dig}
                                   for sg in band.get("segments", [])
                                   if "all" not in sel and str(sg.get("id")) in sel)
                if only_digital:
                    centers = [c for c in centers if int(c) not in dials]
                if not centers:
                    print("   (nur FT8/FT4/SSTV gewählt – keine weiteren Fenster)")

            # PASS 1:
            # komplettes Band aufnehmen und Basisbandmuster lernen
            for center in centers:

                dev_obj.tune(center)

                i, q = dev_obj.capture(float(cfg["capture_seconds"])
                )

                result = analyse(i, q, cfg)

                band_results.append({
                    "center": center,
                    "result": result
                })

                print(
                    f"{center/1e6:9.3f} MHz  "
                    f"Noise {result['noise_db']:7.1f} dB"
                )
                scan_meter(name, center, result)

            birdies = detect_birdies(
                band_results,
                cfg
            )

            print()

            if birdies:
                print("Basisband-Birdies:")

                for b in birdies:
                    print(
                        f"   {b['offset_hz']:+8.1f} Hz  "
                        f"{b['fraction']*100:5.1f}%"
                    )
            else:
                print("Basisband-Birdies: keine")

            save_birdies(
                db,
                band["name"],
                birdies
            )

            print()
            print("Gefilterte Treffer:")

            # PASS 2:
            # Kandidaten filtern und durch zweite
            # Aufnahme bestätigen

            print()
            print("Bestätigte Treffer:")

            for item in band_results:

                center = item["center"]
                result = item["result"]

                candidates = filter_peaks(
                    center,
                    band,
                    result,
                    birdies,
                    cfg,
                    selected_segments
                )

                candidates = [
                    p for p in candidates
                    if rf_allowed(
                        band["name"],
                        p["rf_hz"]
                    )
                ]

                # Kanalwache (RTL-SDR): FM/AM/Relais/APRS … nicht per Schnappschuss,
                # sondern einige Sekunden belauschen und den Ton aus derselben Aufnahme
                # schneiden. Schnappschuss-Kandidaten in diesen Segmenten entfallen.
                if cfg.get("device") == "rtlsdr" and cfg.get("channel_watch", True):
                    candidates = [
                        p for p in candidates
                        if (classify_segment(band, p["rf_hz"]) or {}).get("mode") not in CW.CHANNEL_MODES
                    ]
                    try:
                        watch_hits, watch_rej = CW.watch_window(
                            dev_obj, band, center, cfg, selected_segments,
                            float(band["step_hz"]) / 2.0)
                    except Exception as e:
                        applog("WARN", "scanner", "channel_watch_failed", band=name,
                               center_hz=int(center), error=f"{type(e).__name__}: {e}")
                        print(f"   Kanalwache fehlgeschlagen: {e}")
                        watch_hits, watch_rej = [], 0
                    fresh = []
                    for p in watch_hits:
                        if any(abs(p["rf_hz"] - r) < 6000 for r in band_seen_rf):
                            continue
                        band_seen_rf.append(p["rf_hz"])
                        fresh.append(p)
                    total_rejected += watch_rej
                    if fresh:
                        total_peaks += len(fresh)
                        for p in fresh:
                            applog("DEBUG", "scanner", "hit", band=name, rf_hz=round(p["rf_hz"]),
                                   snr_db=round(p["snr_db"], 1), audio=p.get("audio"))
                        save_scan(db, band["name"], center, result, fresh)
                        print(f"{center/1e6:9.3f} MHz  Kanalwache: {len(fresh)} Treffer mit Aufnahme")

                # UKW-Rundfunk (RTL-SDR): Sender je 100-kHz-Kanal statt Spitzensuche (siehe channel_watch)
                if cfg.get("device") == "rtlsdr" and any(sg.get("mode") == "wfm" for sg in band.get("segments", [])):
                    candidates = [
                        p for p in candidates
                        if (classify_segment(band, p["rf_hz"]) or {}).get("mode") != "wfm"
                    ]
                    try:
                        fm_hits, _ = CW.watch_fm(dev_obj, band, center, cfg, selected_segments,
                                                 allowed=lambda rf: rf_allowed(band["name"], rf))
                    except Exception as e:
                        applog("WARN", "scanner", "fm_watch_failed", band=name, center_hz=int(center),
                               error=f"{type(e).__name__}: {e}")
                        print(f"   UKW-Suche fehlgeschlagen: {e}")
                        fm_hits = []
                    fresh = []
                    for p in fm_hits:
                        if any(abs(p["rf_hz"] - r) < 50000 for r in band_seen_rf):
                            continue
                        band_seen_rf.append(p["rf_hz"])
                        fresh.append(p)
                    if fresh:
                        total_peaks += len(fresh)
                        save_scan(db, band["name"], center, result, fresh)
                        print(f"{center/1e6:9.3f} MHz  UKW: {len(fresh)} Sender aufgenommen")

                if ft8_watch_on:
                    candidates = [
                        p for p in candidates
                        if (classify_segment(band, p["rf_hz"]) or {}).get("mode") not in ("ft8", "ft4")
                    ]

                # Sprachwache (PMSDR, Kurzwelle): SSB-Sprache über die Energie im 2,4-kHz-Fenster
                # über einige Sekunden finden – die Spitzensuche sieht Sprache praktisch nicht.
                if cfg.get("device", "pmsdr") in ("pmsdr", "rtlhf") and cfg.get("voice_watch", True):
                    candidates = [
                        p for p in candidates
                        if (classify_segment(band, p["rf_hz"]) or {}).get("mode") != "voice"
                    ]
                    try:
                        vhits, vrej = CW.watch_voice(
                            dev_obj, band, center, cfg, selected_segments,
                            float(band["step_hz"]) / 2.0,
                            lambda c, o: corrected_rf_hz(c, o, cfg),
                            allowed=lambda rf: rf_allowed(band["name"], rf))
                    except Exception as e:
                        applog("WARN", "scanner", "voice_watch_failed", band=name,
                               center_hz=int(center), error=f"{type(e).__name__}: {e}")
                        print(f"   Sprachwache fehlgeschlagen: {e}")
                        vhits, vrej = [], 0
                    fresh = []
                    for p in vhits:
                        if any(abs(p["rf_hz"] - r) < 2500 for r in band_seen_rf):
                            continue
                        # Träger genauer aus den Spektralkanten der Aufnahme
                        try:
                            # Hinweis auf die Mitte der Sprache (nicht den Träger): die Kantensuche
                            # geht vom Hinweis aus und braucht dort belegtes Spektrum
                            mid = p.get("voice_mid_hz", p["rf_hz"])
                            new_rf, info = estimate_ssb_carrier(p["recording_path"], p["rec_center_hz"],
                                                                mid, band, cfg)
                            if info is not None and info.get("accepted"):
                                p["carrier_delta_hz"] = new_rf - p["rf_hz"]
                                p["offset_hz"] += new_rf - p["rf_hz"]
                                p["rf_hz"] = new_rf
                        except Exception:
                            pass
                        band_seen_rf.append(p["rf_hz"])
                        fresh.append(p)
                    total_rejected += vrej
                    if fresh:
                        total_peaks += len(fresh)
                        for p in fresh:
                            applog("DEBUG", "scanner", "hit", band=name, rf_hz=round(p["rf_hz"]),
                                   snr_db=round(p["snr_db"], 1), mode="voice")
                        save_scan(db, band["name"], center, result, fresh)
                        print(f"{center/1e6:9.3f} MHz  Sprachwache: {len(fresh)} Station(en)")

                # zusätzlicher Mindest-SNR
                candidates = [
                    p for p in candidates
                    if p["snr_db"] >= float(
                        cfg.get(
                            "confirm_snr_db",
                            12.0
                        )
                    )
                ]

                total_scans += 1

                if not candidates:
                    save_scan(
                        db,
                        band["name"],
                        center,
                        result,
                        []
                    )
                    continue

                # Zweite unabhängige Messung
                dev_obj.tune(center)

                i2, q2 = dev_obj.capture(float(
                        cfg.get(
                            "confirm_capture_seconds",
                            cfg["capture_seconds"]
                        )
                    )
                )

                result2 = analyse(
                    i2,
                    q2,
                    cfg
                )

                second_peaks = filter_peaks(
                    center,
                    band,
                    result2,
                    birdies,
                    cfg,
                    selected_segments
                )

                second_peaks = [
                    p for p in second_peaks
                    if rf_allowed(
                        band["name"],
                        p["rf_hz"]
                    )
                ]

                confirmed = confirm_peaks(
                    candidates,
                    second_peaks,
                    cfg
                )

                # Signale an der Fenstergrenze nicht doppelt (Nachbarfenster ueberlappen)
                dedupe_hz = max(float(cfg.get("min_peak_spacing_hz", 250)), 200.0)
                unique = []
                for p in confirmed:
                    if any(abs(p["rf_hz"] - r) < dedupe_hz for r in band_seen_rf):
                        continue
                    band_seen_rf.append(p["rf_hz"])
                    unique.append(p)
                confirmed = unique

                if (
                    confirmed
                    and cfg.get(
                        "record_confirmed",
                        True
                    )
                ):
                    # eine WAV pro bestätigtem Treffer
                    # Aufnahme erfolgt auf derselben
                    # Mittenfrequenz
                    for p in confirmed:
                        dev_obj.tune(center)

                        recording_path = (
                            dev_obj.record(
                                center,
                                float(
                                    cfg.get(
                                        "record_seconds",
                                        5.0
                                    )
                                ),
                                band["name"],
                                p["rf_hz"],
                                mode=(classify_segment(band, p["rf_hz"]) or {}).get("mode")
                            )
                        )

                        recording_path, rec_center = recording_path
                        p["recording_path"] = recording_path
                        p["rec_center_hz"] = rec_center

                        # CW/RTTY/Digital: weiter aufnehmen, solange auf der Frequenz noch gesendet wird
                        # (statt fest record_seconds) – mehr Text für den Dekoder
                        seg_mode = (classify_segment(band, p["rf_hz"]) or {}).get("mode")
                        if (cfg.get("device") in ("pmsdr", "rtlhf") and seg_mode in HF_FOLLOW_BW
                                and float(cfg.get("hf_follow_max_seconds", 60)) > 0):
                            try:
                                (xi, xq), extra_s = CW.hf_continue(
                                    dev_obj, cfg, [float(p["offset_hz"])], HF_FOLLOW_BW[seg_mode],
                                    max_s=float(cfg.get("hf_follow_max_seconds", 60)),
                                    hang_s=float(cfg.get("hf_follow_hang_seconds", 6)))
                                if extra_s >= 4:
                                    with wave.open(str(recording_path), "rb") as w:
                                        raw16 = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
                                    raw16 = raw16[: len(raw16) // 2 * 2].reshape(-1, 2).astype(np.float32) / 32768.0
                                    ri, rq = raw16[:, 0], raw16[:, 1]
                                    from devices import wav_write_iq
                                    wav_write_iq(recording_path, np.concatenate([ri, xi]), np.concatenate([rq, xq]),
                                                 dev_obj.rate)
                                    print(f"      {seg_mode.upper()} sendet weiter – {extra_s:.0f} s angehängt")
                            except Exception as e:
                                print(f"      weiter aufnehmen nicht möglich: {e}")

                        # -------------------------
                        # SSB-Traegerfrequenz schaetzen
                        # -------------------------

                        ssb_rf_hz, ssb_info = (
                            estimate_ssb_carrier(
                                recording_path,
                                rec_center,
                                p["rf_hz"],
                                band,
                                cfg
                            )
                        )

                        if ssb_info is not None:
                            if ssb_info["accepted"]:
                                old_rf_hz = p["rf_hz"]
                                p["rf_hz"] = ssb_rf_hz
                                p["offset_hz"] += (
                                    ssb_rf_hz - old_rf_hz
                                )
                                p["carrier_delta_hz"] = ssb_info["delta_hz"]

                                print(
                                    "      SSB-Korrektur: "
                                    f"{ssb_info['side']} "
                                    f"{ssb_info['delta_hz']:+.0f} Hz, "
                                    f"Score {ssb_info['score']:.1f} -> "
                                    f"{p['rf_hz']/1e6:.6f} MHz"
                                )
                            else:
                                if ssb_info.get("tonal"):
                                    p["tonal"] = True
                                    print(
                                        "      Dauertraeger/Ton statt Sprache "
                                        f"({ssb_info['line_fraction']*100:.0f}% in einer Linie)"
                                    )
                                elif ssb_info.get("noise_like"):
                                    p["noise_like"] = True
                                    print(
                                        "      Breitband/Stoerung statt Sprache "
                                        f"({ssb_info['width_hz']:.0f} Hz breit, Flachheit {ssb_info['flatness']:.2f})"
                                    )
                                else:
                                    print(
                                        "      SSB-Test verworfen: "
                                        f"Breite {ssb_info['width_hz']:.0f} Hz"
                                    )

                        # -------------------------
                        # FT8-Verdacht
                        # -------------------------

                        if (
                            cfg.get(
                                "ft8_detection",
                                True
                            )
                            and decoder_allowed(
                                "ft8",
                                band["name"],
                                p["rf_hz"]
                            )
                        ):
                            ft8, ft8_info = (
                                ft8_signature(
                                    recording_path,
                                    rec_center,
                                    p["rf_hz"],
                                    cfg
                                )
                            )

                            if ft8:

                                print(
                                    "      FT8-Verdacht: "
                                    f"{ft8_info['tones']} Töne, "
                                    f"{ft8_info['span_hz']:.1f} Hz, "
                                    f"Qualität "
                                    f"{ft8_info['quality']:.1f}x, "
                                    f"Offset {ft8_info['center_hz']:+.0f} Hz"
                                )

                                # PMSDR sicher noch einmal
                                # auf dieselbe Mitte stellen.
                                dev_obj.tune(center)

                                if cfg.get(
                                    "ft8_slot_alignment",
                                    True
                                ):
                                    print(
                                        "      warte auf "
                                        "FT8-Zeitfenster ..."
                                    )

                                    wait_for_ft8_slot()

                                seconds = float(
                                    cfg.get(
                                        "ft8_long_record_seconds",
                                        16.0
                                    )
                                )

                                print(
                                    f"      FT8 Langaufnahme "
                                    f"{seconds:.0f} s"
                                )

                                p["rec_center_hz"] = dev_obj.rerecord(
                                    recording_path,
                                    center,
                                    seconds,
                                    p["rf_hz"],
                                    mode=(classify_segment(band, p["rf_hz"]) or {}).get("mode")
                                )

                                p[
                                    "ft8_suspected"
                                ] = True

                rejected = (
                    len(candidates) -
                    len(confirmed)
                )

                total_rejected += rejected
                total_peaks += len(confirmed)

                for p in confirmed:
                    applog("DEBUG", "scanner", "hit", band=name, rf_hz=round(p["rf_hz"]), snr_db=round(p["snr_db"], 1))
                    info = classify_segment(band, p["rf_hz"])
                    if info:
                        p["segment_id"] = info["id"]
                        p["mode"] = info["mode"]
                        p["sideband"] = info.get("sideband")
                        if p.get("tonal") and info["mode"] in ("voice", "sstv"):
                            p["mode"] = "carrier"
                        elif p.get("noise_like") and info["mode"] in ("voice", "sstv"):
                            p["mode"] = "noise"
                    else:
                        p["segment_id"] = None
                        p["mode"] = "unknown"
                        p["sideband"] = None

                save_scan(
                    db,
                    band["name"],
                    center,
                    result,
                    confirmed
                )

                if confirmed:
                    print(
                        f"{center/1e6:9.3f} MHz  "
                        f"bestätigt {len(confirmed):2d}"
                    )

                    for p in confirmed:
                        print(
                            f"   -> "
                            f"{p['rf_hz']/1e6:10.6f} MHz  "
                            f"{p['snr_db']:5.1f} dB "
                            f"über Noise"
                        )

                        if p.get("recording_path"):
                            print(
                                f"      WAV: "
                                f"{p['recording_path']}"
                            )

                if rejected:
                    print(
                        f"{center/1e6:9.3f} MHz  "
                        f"verworfen  {rejected:2d}"
                    )

            # Protokoll: welche Frequenzen in diesem Band tatsächlich abgehört wurden
            try:
                applog("INFO", "scanner", "band_scanned", band=name, device=cfg["device"],
                       hits=total_peaks - _band_p0, rejected=total_rejected - _band_r0,
                       seconds=round(time.time() - _band_t0),
                       **listened_summary(band, cfg, centers, selected_segments, plan_by_band[name]))
            except Exception as e:
                applog("DEBUG", "scanner", "band_summary_failed", band=name, error=str(e))

        if missing and not scanned_bands:
            raise RuntimeError("Kein passendes Gerät angeschlossen – übersprungen: " + ", ".join(missing))
    finally:
        for d in devices.values():
            d.close()
        db.close()

    print()
    print("-----------------------------")
    applog("INFO", "scanner", "run_summary", tunes=total_scans, peaks=total_peaks, rejected=total_rejected)
    print(f"Abstimmungen : {total_scans}")
    print(f"Peaks        : {total_peaks}")
    print(f"Verworfen    : {total_rejected}")
    print(f"Datenbank    : {DB_FILE}")
    print("Scan beendet")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        sys.exit(130)
    except Exception as e:
        import traceback
        applog("ERROR", "scanner", "run_exception", error=f"{type(e).__name__}: {e}",
               where=traceback.format_exc().strip().splitlines()[-3:])
        print(
            f"\nFEHLER: {e}",
            file=sys.stderr
        )
        sys.exit(1)

