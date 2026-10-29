#!/usr/bin/env python3

import json
import sys
import sqlite3
from pathlib import Path

import numpy as np
import soundfile as sf

from scipy.signal import (
    butter,
    sosfiltfilt,
    find_peaks
)

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "scanner.db"
CFG = BASE / "config" / "scanner.json"
REPORTS = BASE / "reports"

with open(CFG, "r", encoding="utf-8") as f:
    CFGDATA = json.load(f)
try:                                                      # Bandplan des gewählten Landes
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bandplan as _BP
    CFGDATA = _BP.apply(CFGDATA)
except Exception:
    pass

PPM = float(
    CFGDATA.get(
        "frequency_correction_ppm",
        0.0
    )
)

CONJ = bool(
    CFGDATA.get(
        "iq_conjugate",
        False
    )
)


# --------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------

def load_iq(path):
    data, rate = sf.read(
        path,
        always_2d=True
    )

    if data.shape[1] < 2:
        raise RuntimeError(
            "Keine Stereo-I/Q-Datei"
        )

    i = data[:, 0]
    q = data[:, 1]

    z = i + 1j * q

    if CONJ:
        z = np.conj(z)

    z -= np.mean(z)

    return z, rate


def shift_to_hit(
    z,
    rate,
    center_hz,
    rf_hz
):
    corrected_center = (
        center_hz *
        (1.0 + PPM * 1e-6)
    )

    offset = (
        rf_hz -
        corrected_center
    )

    n = np.arange(len(z))

    osc = np.exp(
        -2j *
        np.pi *
        offset *
        n /
        rate
    )

    return z * osc


def rle(bits):
    if len(bits) == 0:
        return []

    out = []

    value = bits[0]
    start = 0

    for i in range(1, len(bits)):
        if bits[i] != value:
            out.append(
                (
                    int(value),
                    start,
                    i
                )
            )

            value = bits[i]
            start = i

    out.append(
        (
            int(value),
            start,
            len(bits)
        )
    )

    return out


# --------------------------------------------------
# FT8-Verdacht
# --------------------------------------------------

def detect_ft8(z, rate):

    frame_len = int(
        round(
            rate * 0.160
        )
    )

    if len(z) < frame_len * 12:
        return None

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

    mask = (
        (freqs >= -3200)
        &
        (freqs <= 3200)
    )

    wf = freqs[mask]

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
            spec[mask]
        )

    if len(spectra) < 15:
        return None

    spectra = np.asarray(
        spectra
    )

    candidates = []

    for center in np.arange(
        -3000.0,
        3000.1,
        25.0
    ):

        m = (
            (wf >= center - 65)
            &
            (wf <= center + 65)
        )

        if np.sum(m) < 8:
            continue

        sf = wf[m]
        sub = spectra[:, m]

        dominant = []
        quality = []
        frame_idx = []

        for fi, frame in enumerate(sub):

            median = (
                float(
                    np.median(frame)
                )
                + 1e-20
            )

            k = int(
                np.argmax(frame)
            )

            ratio = (
                float(
                    frame[k]
                )
                /
                median
            )

            if ratio < 7.0:
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
                    sf[k]
                )
            )
            frame_idx.append(fi)

            quality.append(
                ratio
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

        span = float(
            np.percentile(
                dominant,
                90
            )
            -
            np.percentile(
                dominant,
                10
            )
        )

        q = float(
            np.median(
                quality
            )
        )

        # Anteil der häufigsten Tonlage: ein zappelnder Träger oder CW sitzt fast immer auf einer
        _, counts = np.unique(bins, return_counts=True)
        top_share = float(counts.max()) / len(bins)

        # Beständigkeit: ein FT8-Ton steht 0,16 s, also in zwei aufeinanderfolgenden Rahmen (Abstand
        # 0,08 s) meist auf derselben Linie. Rauschspitzen springen zufällig (Gleichstand ~5 %).
        fidx = np.asarray(frame_idx)
        adj = (fidx[1:] == fidx[:-1] + 1)
        steady = float(np.mean(bins[1:][adj] == bins[:-1][adj])) if adj.sum() >= 8 else 0.0

        # FT8: 8 Töne im 6,25-Hz-Raster (Rahmen 0,16 s = genau 6,25 Hz Auflösung), also ~44 Hz breit;
        # 10–90-%-Spanne der Töne dann um 30–44 Hz. Früher reichte „höchstens 75 Hz“ – ein Träger, der
        # zwischen zwei Nachbarlinien wackelt (Spanne 6 Hz), galt so als „FT8 98 %“.
        if not (
            5 <= unique_bins <= 12
            and
            25.0 <= span <= 75.0
            and
            top_share <= 0.6
            and
            steady >= 0.3
            and
            changes >= 5
            and
            q >= 8.0
        ):
            continue

        score = (
            len(dominant)
            +
            changes * 2
            +
            min(
                q,
                30
            )
        )

        candidates.append(
            {
                "center": float(center),
                "tones": unique_bins,
                "span": span,
                "changes": changes,
                "quality": q,
                "top_share": top_share,
                "score": score
            }
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x:
            x["score"],
        reverse=True
    )

    best = candidates[0]

    # Sicherheit: Tonvielfalt (bis 8 Töne), Wechsel, Pegel – erst ein klares, typisches Muster kommt
    # über 85 %, ein knapp erfülltes bleibt um 60–70 %
    confidence = int(
        np.clip(
            30
            + 3 * min(best["tones"], 8)
            + min(best["changes"], 15)
            + min(best["quality"], 40) * 0.6
            - max(0.0, best["top_share"] - 0.4) * 50,
            50,
            95
        )
    )

    return {
        "type": "FT8",
        "confidence":
            confidence,
        "text": "",
        "detail": (
            "FT8-Aktivität · "
            f"{best['tones']} Tonlagen · "
            f"{best['span']:.1f} Hz"
        )
    }




# --------------------------------------------------
# CW
# --------------------------------------------------

MORSE = {
    ".-": "A",
    "-...": "B",
    "-.-.": "C",
    "-..": "D",
    ".": "E",
    "..-.": "F",
    "--.": "G",
    "....": "H",
    "..": "I",
    ".---": "J",
    "-.-": "K",
    ".-..": "L",
    "--": "M",
    "-.": "N",
    "---": "O",
    ".--.": "P",
    "--.-": "Q",
    ".-.": "R",
    "...": "S",
    "-": "T",
    "..-": "U",
    "...-": "V",
    ".--": "W",
    "-..-": "X",
    "-.--": "Y",
    "--..": "Z",

    "-----": "0",
    ".----": "1",
    "..---": "2",
    "...--": "3",
    "....-": "4",
    ".....": "5",
    "-....": "6",
    "--...": "7",
    "---..": "8",
    "----.": "9"
}


def decode_cw(z, rate):

    cutoff = 120

    sos = butter(
        4,
        cutoff,
        btype="lowpass",
        fs=rate,
        output="sos"
    )

    try:
        carrier = sosfiltfilt(
            sos,
            z
        )
    except Exception:
        return None

    env = np.abs(carrier)

    smooth_n = max(
        1,
        int(rate * 0.010)
    )

    kernel = np.ones(
        smooth_n
    ) / smooth_n

    env = np.convolve(
        env,
        kernel,
        mode="same"
    )

    low = np.percentile(
        env,
        20
    )

    high = np.percentile(
        env,
        90
    )

    if high <= low * 1.7:
        return None

    threshold = (
        low +
        (high - low) * 0.45
    )

    bits = (
        env > threshold
    ).astype(np.uint8)

    segments = rle(bits)

    on_durations = []

    for value, a, b in segments:
        if value != 1:
            continue

        d = (
            (b - a) /
            rate
        )

        if 0.018 <= d <= 1.2:
            on_durations.append(d)

    if len(on_durations) < 3:
        return None

    unit = np.percentile(
        on_durations,
        30
    )

    if not (
        0.020 <= unit <= 0.35
    ):
        return None

    letters = []
    current = ""

    for value, a, b in segments:

        duration = (
            (b - a) /
            rate
        )

        ratio = (
            duration /
            unit
        )

        if value == 1:

            if ratio < 2.0:
                current += "."
            else:
                current += "-"

        else:

            if ratio >= 6.0:

                if current:
                    letters.append(
                        MORSE.get(
                            current,
                            "?"
                        )
                    )

                    current = ""

                letters.append(" ")

            elif ratio >= 2.0:

                if current:
                    letters.append(
                        MORSE.get(
                            current,
                            "?"
                        )
                    )

                    current = ""

    if current:
        letters.append(
            MORSE.get(
                current,
                "?"
            )
        )

    text = "".join(
        letters
    ).strip()

    while "  " in text:
        text = text.replace(
            "  ",
            " "
        )

    valid = sum(
        1
        for c in text
        if c != "?"
    )

    unknown = text.count("?")

    if valid < 2:
        return None

    confidence = int(
        100 *
        valid /
        max(
            1,
            valid + unknown
        )
    )

    wpm = (
        1.2 / unit
        if unit > 0
        else 0
    )

    return {
        "type": "CW",
        "confidence": confidence,
        "text": text,
        "detail": (
            f"ca. {wpm:.0f} WPM"
        )
    }


# --------------------------------------------------
# DTMF
# --------------------------------------------------

DTMF_ROWS = [
    697,
    770,
    852,
    941
]

DTMF_COLS = [
    1209,
    1336,
    1477,
    1633
]

DTMF_MAP = [
    ["1", "2", "3", "A"],
    ["4", "5", "6", "B"],
    ["7", "8", "9", "C"],
    ["*", "0", "#", "D"]
]


def tone_power(
    x,
    rate,
    freq
):
    n = np.arange(
        len(x)
    )

    osc = np.exp(
        -2j *
        np.pi *
        freq *
        n /
        rate
    )

    return (
        np.abs(
            np.sum(
                x * osc
            )
        ) ** 2
    )


def decode_dtmf_file(path):

    if not path.exists():
        return None

    x, rate = sf.read(
        path,
        always_2d=False
    )

    if x.ndim > 1:
        x = x[:, 0]

    x = np.asarray(
        x,
        dtype=float
    )

    frame = int(
        rate * 0.050
    )

    hop = int(
        rate * 0.025
    )

    if len(x) < frame:
        return None

    detected = []

    for start in range(
        0,
        len(x) - frame,
        hop
    ):
        seg = x[
            start:
            start + frame
        ]

        seg = (
            seg *
            np.hanning(
                len(seg)
            )
        )

        rows = [
            tone_power(
                seg,
                rate,
                f
            )
            for f in DTMF_ROWS
        ]

        cols = [
            tone_power(
                seg,
                rate,
                f
            )
            for f in DTMF_COLS
        ]

        ri = int(
            np.argmax(rows)
        )

        ci = int(
            np.argmax(cols)
        )

        rs = sorted(
            rows,
            reverse=True
        )

        cs = sorted(
            cols,
            reverse=True
        )

        if (
            rs[0] >
            rs[1] * 3.5
            and
            cs[0] >
            cs[1] * 3.5
        ):

            ratio = (
                rows[ri] /
                max(
                    cols[ci],
                    1e-20
                )
            )

            dbdiff = abs(
                10 *
                np.log10(
                    max(
                        ratio,
                        1e-20
                    )
                )
            )

            if dbdiff < 12:
                detected.append(
                    DTMF_MAP[
                        ri
                    ][
                        ci
                    ]
                )
            else:
                detected.append(
                    None
                )

        else:
            detected.append(
                None
            )

    result = []

    last = None
    count = 0

    for symbol in (
        detected +
        [None]
    ):

        if symbol == last:
            count += 1
            continue

        if (
            last is not None
            and count >= 3
        ):
            if (
                not result
                or result[-1] != last
            ):
                result.append(last)

        last = symbol
        count = 1

    if not result:
        return None

    return {
        "type": "DTMF",
        "confidence": 90,
        "text": "".join(result),
        "detail": "Tonwahl"
    }


# --------------------------------------------------
# ITA2 / RTTY
# --------------------------------------------------

ITA2_LETTERS = {
    0: "",
    1: "E",
    2: "\n",
    3: "A",
    4: " ",
    5: "S",
    6: "I",
    7: "U",
    8: "\r",
    9: "D",
    10: "R",
    11: "J",
    12: "N",
    13: "F",
    14: "C",
    15: "K",
    16: "T",
    17: "Z",
    18: "L",
    19: "W",
    20: "H",
    21: "Y",
    22: "P",
    23: "Q",
    24: "O",
    25: "B",
    26: "G",
    27: "<FIGS>",
    28: "M",
    29: "X",
    30: "V",
    31: "<LTRS>"
}

ITA2_FIGURES = {
    0: "",
    1: "3",
    2: "\n",
    3: "-",
    4: " ",
    5: "'",
    6: "8",
    7: "7",
    8: "\r",
    9: "$",
    10: "4",
    11: "'",
    12: ",",
    13: "!",
    14: ":",
    15: "(",
    16: "5",
    17: "\"",
    18: ")",
    19: "2",
    20: "#",
    21: "6",
    22: "0",
    23: "1",
    24: "9",
    25: "?",
    26: "&",
    27: "<FIGS>",
    28: ".",
    29: "/",
    30: ";",
    31: "<LTRS>"
}


def spectrum_peaks(
    z,
    rate
):
    nfft = 8192

    spectra = []

    step = nfft // 2

    for start in range(
        0,
        len(z) - nfft,
        step
    ):
        block = z[
            start:
            start + nfft
        ]

        block = (
            block *
            np.hanning(
                nfft
            )
        )

        spec = np.abs(
            np.fft.fftshift(
                np.fft.fft(
                    block
                )
            )
        )

        spectra.append(spec)

    if not spectra:
        return []

    spec = np.median(
        spectra,
        axis=0
    )

    db = (
        20 *
        np.log10(
            np.maximum(
                spec,
                1e-12
            )
        )
    )

    freq = np.fft.fftshift(
        np.fft.fftfreq(
            nfft,
            1 / rate
        )
    )

    mask = (
        np.abs(freq) <
        3000
    )

    noise = np.median(
        db[mask]
    )

    peaks, props = find_peaks(
        db,
        height=noise + 7,
        distance=max(
            2,
            int(
                40 /
                (rate / nfft)
            )
        )
    )

    result = []

    for p in peaks:
        if not mask[p]:
            continue

        result.append(
            (
                float(freq[p]),
                float(
                    db[p] -
                    noise
                )
            )
        )

    result.sort(
        key=lambda x: x[1],
        reverse=True
    )

    return result[:25]


def find_rtty_pair(
    z,
    rate
):
    peaks = spectrum_peaks(
        z,
        rate
    )

    best = None

    for i in range(
        len(peaks)
    ):
        for j in range(
            i + 1,
            len(peaks)
        ):
            f1, s1 = peaks[i]
            f2, s2 = peaks[j]

            shift = abs(
                f1 - f2
            )

            if (
                145 <=
                shift <=
                205
            ):
                score = (
                    s1 + s2
                )

                if (
                    best is None
                    or score >
                    best[0]
                ):
                    best = (
                        score,
                        f1,
                        f2,
                        shift
                    )

    return best


def ita2_decode_codes(
    codes
):
    mode = "LTRS"

    out = []

    for code in codes:

        if code == 31:
            mode = "LTRS"
            continue

        if code == 27:
            mode = "FIGS"
            continue

        table = (
            ITA2_LETTERS
            if mode == "LTRS"
            else ITA2_FIGURES
        )

        ch = table.get(
            code,
            ""
        )

        if not ch.startswith("<"):
            out.append(ch)

    return "".join(out)


def try_rtty_decode(
    z,
    rate,
    f1,
    f2,
    baud
):
    cutoff = min(
        80,
        abs(f1 - f2) *
        0.4
    )

    sos = butter(
        3,
        cutoff,
        btype="lowpass",
        fs=rate,
        output="sos"
    )

    n = np.arange(
        len(z)
    )

    def channel(freq):
        mixed = (
            z *
            np.exp(
                -2j *
                np.pi *
                freq *
                n /
                rate
            )
        )

        filtered = sosfiltfilt(
            sos,
            mixed
        )

        return (
            np.abs(
                filtered
            ) ** 2
        )

    p1 = channel(f1)
    p2 = channel(f2)

    discr = (
        p1 - p2
    )

    symbol = (
        rate /
        baud
    )

    best = None

    for polarity in (
        1,
        -1
    ):
        d = (
            discr *
            polarity
        )

        bits = (
            d > 0
        ).astype(
            np.uint8
        )

        codes = []

        i = int(symbol)

        while (
            i +
            int(7 * symbol)
            <
            len(bits)
        ):

            before = bits[
                max(
                    0,
                    i -
                    int(
                        0.3 *
                        symbol
                    )
                )
            ]

            now = bits[i]

            if (
                before == 1
                and now == 0
            ):
                data_bits = []

                valid = True

                for b in range(5):
                    pos = int(
                        i +
                        (
                            1.5 + b
                        ) *
                        symbol
                    )

                    if pos >= len(bits):
                        valid = False
                        break

                    data_bits.append(
                        int(
                            bits[pos]
                        )
                    )

                stop_pos = int(
                    i +
                    6.5 *
                    symbol
                )

                if (
                    valid
                    and stop_pos <
                    len(bits)
                    and bits[
                        stop_pos
                    ] == 1
                ):
                    code = 0

                    for b, bit in enumerate(
                        data_bits
                    ):
                        code |= (
                            bit << b
                        )

                    codes.append(code)

                    i += int(
                        7.0 *
                        symbol
                    )

                    continue

            i += max(
                1,
                int(
                    0.15 *
                    symbol
                )
            )

        text = ita2_decode_codes(
            codes
        )

        printable = sum(
            1
            for c in text
            if (
                c.isalnum()
                or
                c in
                " .,/?-"
            )
        )

        score = printable

        if (
            best is None
            or score >
            best[0]
        ):
            best = (
                score,
                text
            )

    if (
        best is None
        or best[0] < 3
    ):
        return None

    text = best[1].strip()

    if len(text) < 3:
        return None

    return text


def decode_rtty(
    z,
    rate
):
    pair = find_rtty_pair(
        z,
        rate
    )

    if pair is None:
        return None

    score, f1, f2, shift = pair

    best_text = None
    best_baud = None

    for baud in (
        45.45,
        50,
        75
    ):
        try:
            text = try_rtty_decode(
                z,
                rate,
                f1,
                f2,
                baud
            )
        except Exception:
            text = None

        if text:
            if (
                best_text is None
                or len(text) >
                len(best_text)
            ):
                best_text = text
                best_baud = baud

    if best_text:
        return {
            "type": "RTTY",
            "confidence": 80,
            "text": best_text,
            "detail": (
                f"{best_baud:g} Bd, "
                f"{shift:.0f} Hz Shift"
            )
        }

    # Signal sieht nach RTTY aus,
    # aber Text nicht zuverlässig.
    if score >= 18:
        return {
            "type": "RTTY?",
            "confidence": 55,
            "text": "",
            "detail": (
                f"FSK-Paar ~"
                f"{shift:.0f} Hz"
            )
        }

    return None


# --------------------------------------------------
# Einzelton
# --------------------------------------------------

def dominant_audio_tone(
    path
):
    if not path.exists():
        return None

    x, rate = sf.read(
        path,
        always_2d=False
    )

    if x.ndim > 1:
        x = x[:, 0]

    x = np.asarray(
        x,
        dtype=float
    )

    if len(x) < 2048:
        return None

    n = min(
        len(x),
        rate * 3
    )

    x = (
        x[:n] *
        np.hanning(n)
    )

    spec = np.abs(
        np.fft.rfft(x)
    )

    freq = np.fft.rfftfreq(
        n,
        1 / rate
    )

    mask = (
        (freq >= 250)
        &
        (freq <= 3000)
    )

    db = (
        20 *
        np.log10(
            np.maximum(
                spec,
                1e-12
            )
        )
    )

    noise = np.median(
        db[mask]
    )

    idxs = np.where(
        mask
    )[0]

    if len(idxs) == 0:
        return None

    p = idxs[
        np.argmax(
            db[idxs]
        )
    ]

    snr = (
        db[p] -
        noise
    )

    if snr < 18:
        return None

    return {
        "type": "TON",
        "confidence": min(
            95,
            int(
                55 +
                snr
            )
        ),
        "text": (
            f"{freq[p]:.0f} Hz"
        ),
        "detail": (
            f"{snr:.0f} dB über Audiogrund"
        )
    }


# --------------------------------------------------
# Hauptprogramm
# --------------------------------------------------

db = sqlite3.connect(DB)

rows = db.execute("""
SELECT
    p.id,
    p.rf_hz,
    p.recording_path,
    COALESCE(p.rec_center_hz, s.center_hz),
    s.band
FROM peak p
JOIN scan s
    ON s.id = p.scan_id
WHERE p.recording_path
    IS NOT NULL
  AND COALESCE(p.mode, '') IN
      ('', 'unknown', 'cw', 'digital', 'rtty', 'beacon', 'carrier')
ORDER BY p.id
""").fetchall()

db.close()

decoded_count = 0

for (
    peak_id,
    rf_hz,
    recording_path,
    center_hz,
    band
) in rows:

    src = Path(
        recording_path
    )

    if not src.exists():
        continue

    stem = src.stem

    outfile = (
        REPORTS /
        f"{stem}_decode.json"
    )

    # Bereits ausgewertet:
    # nicht jedes Mal neu rechnen.
    if outfile.exists():
        continue

    try:
        z, rate = load_iq(
            src
        )

        bb = shift_to_hit(
            z,
            rate,
            center_hz,
            rf_hz
        )

        # FT8 zuerst prüfen.
        # Wenn das Muster passt, werden
        # CW / RTTY / DTMF absichtlich
        # NICHT mehr gestartet.
        ft8 = detect_ft8(
            bb,
            rate
        )

        if ft8:

            data = {
                "peak_id": peak_id,
                "band": band,
                "frequency_hz": rf_hz,
                "decoders": [
                    ft8
                ]
            }

            outfile.write_text(
                json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2
                ),
                encoding="utf-8"
            )

            decoded_count += 1

            print(
                f"{band:4s} "
                f"{rf_hz/1e6:.6f} MHz:"
            )

            print(
                "   FT8 "
                f"{ft8['confidence']} % "
                f"{ft8['detail']}"
            )

            continue

        results = []

        # CW
        cw = decode_cw(
            bb,
            rate
        )

        if cw:
            results.append(cw)

        # RTTY
        rtty = decode_rtty(
            bb,
            rate
        )

        if rtty:
            results.append(rtty)

        usb = (
            REPORTS /
            f"{stem}_USB.wav"
        )

        lsb = (
            REPORTS /
            f"{stem}_LSB.wav"
        )

        # DTMF beide Seiten versuchen
        dtmf_usb = decode_dtmf_file(
            usb
        )

        dtmf_lsb = decode_dtmf_file(
            lsb
        )

        if dtmf_usb:
            dtmf_usb["detail"] += (
                " · USB"
            )
            results.append(
                dtmf_usb
            )

        elif dtmf_lsb:
            dtmf_lsb["detail"] += (
                " · LSB"
            )
            results.append(
                dtmf_lsb
            )

        # Nur wenn noch nichts erkannt:
        # dominanten Ton anzeigen.
        strong_types = {
            r["type"]
            for r in results
        }

        if not (
            "CW" in strong_types
            or
            "RTTY" in strong_types
            or
            "DTMF" in strong_types
        ):
            tone = dominant_audio_tone(
                usb
            )

            if tone:
                tone["detail"] += (
                    " · USB"
                )

                results.append(tone)

        data = {
            "peak_id": peak_id,
            "band": band,
            "frequency_hz": rf_hz,
            "decoders": results
        }

        outfile.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        if results:
            decoded_count += 1

            print(
                f"{band:4s} "
                f"{rf_hz/1e6:.6f} MHz:"
            )

            for r in results:
                print(
                    "   ",
                    r["type"],
                    r.get(
                        "text",
                        ""
                    ),
                    r.get(
                        "detail",
                        ""
                    )
                )

    except Exception as e:

        data = {
            "peak_id": peak_id,
            "band": band,
            "frequency_hz": rf_hz,
            "decoders": [],
            "error": str(e)
        }

        outfile.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

print()
print(
    "Treffer mit Decodergebnis:",
    decoded_count
)
