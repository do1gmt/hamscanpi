#!/usr/bin/env python3
"""
FT8/FT4 auf Kurzwelle (PMSDR): statt Spitzensuche -> Bestätigung -> Langaufnahme -> Dekodierung
(jede Stufe konnte FT8 verlieren) wird hier einfach dekodiert:

  1. Abstimmen 12 kHz NEBEN die Dialfrequenz – das FT8-Band (Dial + 0…3 kHz) liegt dann weit weg
     vom Gleichanteil der PMSDR in der Fenstermitte (bisher wurde genau auf 3,573 MHz abgestimmt,
     FT8 lag damit direkt neben der DC-Linie).
  2. Genau einen FT8-Takt aufnehmen (15 s bzw. 7,5 s für FT4), beginnend auf der UTC-Taktgrenze.
     arecord wird vorher gestartet; der Anfang wird anhand der Ankunftszeit der Daten abgeschnitten.
  3. Echtes USB-Seitenband herausfiltern (bisher: Realteil -> alles unterhalb der Dialfrequenz
     wurde in das FT8-Audio gespiegelt).
  4. ft8_lib dekodieren lassen. Nur echte Dekodierungen werden Treffer.
"""
import json
import math
import re
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
from scipy.signal import butter, resample_poly, sosfiltfilt

BASE = Path(__file__).resolve().parent.parent
REPORTS = BASE / "reports"
DEFAULT_DECODER = Path.home() / "ft8_lib" / "decode_ft8"

SLOT_S = {"ft8": 15.0, "ft4": 7.5}


def usb_audio_12k(i, q, rate, conjugate, shift_hz, side="USB", bw=3200.0):
    """I/Q -> 12-kHz-Audio eines Seitenbandes. shift_hz = Dialfrequenz relativ zur Fenstermitte."""
    z = (np.asarray(i) - 1j * np.asarray(q)) if conjugate else (np.asarray(i) + 1j * np.asarray(q))
    z = z - z.mean()
    n = np.arange(len(z))
    bb = z * np.exp(-2j * np.pi * shift_hz * n / rate)            # Dialfrequenz -> 0 Hz
    sgn = 1.0 if side == "USB" else -1.0
    # Seitenband: um ±bw/2 verschieben, Tiefpass bw/2, zurück -> nur 0…bw (USB) bzw. −bw…0 (LSB)
    w = np.exp(-2j * np.pi * sgn * (bw / 2) * n / rate)
    sos = butter(8, bw / 2 + 100, fs=rate, output="sos")
    y = bb * w
    y = sosfiltfilt(sos, y.real) + 1j * sosfiltfilt(sos, y.imag)
    audio = np.real(y * np.conj(w))
    if rate != 12000:
        g = math.gcd(12000, int(rate))
        audio = resample_poly(audio, 12000 // g, int(rate) // g)
    audio = audio - audio.mean()
    pk = float(np.max(np.abs(audio))) if len(audio) else 0.0
    return audio / pk * 0.8 if pk > 0 else audio


def write_wav12k(path, audio):
    import soundfile as sf
    sf.write(str(path), audio, 12000, subtype="PCM_16")


MSG_RX = re.compile(r"^\d{6}\s+([+-]?\d+(?:\.\d+)?)\s+([+-]?\d+(?:\.\d+)?)\s+(\d+)\s+~\s+(.+?)\s*$")


def decode(audio, protocol, decoder):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as t:
        tmp = Path(t.name)
    try:
        pad = int(SLOT_S[protocol] * 12000)
        a = np.zeros(max(pad, len(audio)))
        a[: len(audio)] = audio[: len(a)]
        write_wav12k(tmp, a)
        args = [str(decoder)] + (["-ft4"] if protocol == "ft4" else []) + [str(tmp)]
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
        out = []
        for line in (r.stdout + "\n" + r.stderr).splitlines():
            m = MSG_RX.match(line.strip())
            if m and len(m.group(4).strip()) >= 3:
                out.append({"snr": float(m.group(1)), "dt": float(m.group(2)),
                            "audio_hz": int(m.group(3)), "text": m.group(4).strip()})
        if out:                                           # echte SNR (2500 Hz) statt Sync-Bewertung des Dekoders
            import ft8_snr
            ft8_snr.fix_messages(tmp, out, protocol.upper())
        return out
    finally:
        tmp.unlink(missing_ok=True)


def watch_ft8(dev, band, seg, cfg, log=print):
    """Einen FT8/FT4-Takt auf der Dialfrequenz des Segments aufnehmen und dekodieren."""
    from devices import RECDIR, recording_name, wav_write_iq

    protocol = "ft4" if seg.get("mode") == "ft4" else "ft8"
    dial = float(seg["frequencies_hz"][0])
    decoder = Path(cfg.get("ft8_decoder", str(DEFAULT_DECODER))).expanduser()
    if not decoder.exists():
        log(f"   {protocol.upper()}: Dekoder fehlt ({decoder})")
        return []
    offset = float(cfg.get("digital_tune_offset_hz", 12000.0))
    center = int(round(dial + offset))
    ppm = float(cfg.get("frequency_correction_ppm", 0.0))
    shift = dial - center * (1.0 + ppm * 1e-6)
    slot = SLOT_S[protocol]

    dev.tune(center)
    i, q, t_slot = dev.capture_slot(slot)
    audio = usb_audio_12k(i, q, dev.rate, bool(cfg.get("iq_conjugate", False)), shift, "USB")
    msgs = decode(audio, protocol, decoder)
    stamp = time.strftime("%H:%M:%S", time.gmtime(t_slot))
    if not msgs:
        log(f"   {protocol.upper()} {dial/1e6:.3f} MHz, Takt {stamp} UTC: keine Dekodierung")
        return []

    RECDIR.mkdir(parents=True, exist_ok=True)
    best = max(msgs, key=lambda m: m["snr"])
    rf = dial + best["audio_hz"]
    path = RECDIR / recording_name(band["name"], rf)
    wav_write_iq(path, i, q, dev.rate)
    REPORTS.mkdir(parents=True, exist_ok=True)
    label = protocol.upper()
    data = {
        "band": band["name"], "frequency_hz": rf, "ft8_tested": True, "slot_utc": stamp,
        "decoders": [{
            "type": label, "confidence": 100, "validated": True, "decoder": "ft8_lib",
            "text": "\n".join(m["text"] for m in msgs),
            "detail": f"{len(msgs)} {label}-Nachrichten · Takt {stamp} UTC · Dial {dial/1e6:.6f} MHz USB",
            "messages": msgs,
        }],
    }
    (REPORTS / f"{path.stem}_decode.json").write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                                      encoding="utf-8")
    log(f"   {label} {dial/1e6:.3f} MHz, Takt {stamp} UTC: {len(msgs)} Nachrichten")
    for m in msgs[:8]:
        log(f"      {m['snr']:+5.0f} dB  {m['audio_hz']:5d} Hz  {m['text']}")
    return [{
        "rf_hz": float(rf), "offset_hz": float(rf - center * (1.0 + ppm * 1e-6)),
        "level_db": best["snr"], "snr_db": best["snr"],
        "recording_path": str(path), "rec_center_hz": float(center),
        "segment_id": seg.get("id"), "mode": protocol, "sideband": "USB",
        "note": f"{len(msgs)} {label}-Nachrichten",
    }]
