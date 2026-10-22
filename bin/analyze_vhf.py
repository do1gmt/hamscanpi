#!/usr/bin/env python3
"""
Nachbearbeitung fuer VHF/UHF-Treffer (RTL-SDR):
- AM/NFM/WFM-Audio als <stem>_FM.wav bzw. _AM.wav (zum Anhoeren im Dashboard)
- APRS (AFSK1200) und POCSAG per multimon-ng, falls installiert -> _decode.json
"""
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import webapp as W  # demodulate(), scanner_config()

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "scanner.db"
OUT = BASE / "reports"
MULTIMON = shutil.which("multimon-ng")

AUDIO_MODE = {"fm": "nfm", "relais": "nfm", "dv": "nfm", "aprs": "nfm", "pocsag": "nfm",
              "sat": "nfm", "data": "nfm", "am": "am", "wfm": "wfm"}


def load_iq(path):
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    sm = np.frombuffer(frames, dtype="<i2")
    sm = sm[: len(sm) // 2 * 2].reshape(-1, 2).astype(np.float64) / 32768.0
    i, q = sm[:, 0] - sm[:, 0].mean(), sm[:, 1] - sm[:, 1].mean()
    return i + 1j * q, rate


def write_wav(path, audio, rate):
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak > 0:
        audio = audio / peak * 0.85
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(int(rate))
        w.writeframes((audio * 32767).astype("<i2").tobytes())


def run_multimon(audio, rate, decoder):
    """multimon-ng braucht 22050 Hz, 16 Bit mono."""
    from scipy.signal import resample_poly
    from math import gcd
    g = gcd(22050, int(rate))
    a = resample_poly(audio, 22050 // g, int(rate) // g)
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tmp:
        tmp.write((a / (np.max(np.abs(a)) or 1) * 0.9 * 32767).astype("<i2").tobytes())
        raw = tmp.name
    try:
        r = subprocess.run([MULTIMON, "-t", "raw", "-a", decoder, "-q", raw],
                           capture_output=True, text=True, timeout=60)
        lines = [l.strip() for l in r.stdout.splitlines() if l.strip() and not l.startswith("multimon")]
    finally:
        Path(raw).unlink(missing_ok=True)
    return lines


def main():
    db = sqlite3.connect(DB)
    rows = db.execute("""
        SELECT p.id, p.rf_hz, p.recording_path, COALESCE(p.rec_center_hz, s.center_hz), s.band, COALESCE(p.mode,'')
        FROM peak p JOIN scan s ON s.id = p.scan_id
        WHERE p.recording_path IS NOT NULL
          AND COALESCE(p.rec_center_hz, s.center_hz) >= 30000000
        ORDER BY p.id""").fetchall()
    db.close()
    done = 0
    for peak_id, rf_hz, recpath, center_hz, band, mode in rows:
        src = Path(recpath)
        if not src.exists():
            continue
        amode = AUDIO_MODE.get(mode)
        if not amode:
            continue
        suffix = "_AM.wav" if amode == "am" else "_FM.wav"
        out = OUT / f"{src.stem}{suffix}"
        jpath = OUT / f"{src.stem}_decode.json"
        if out.exists() and (mode not in ("aprs", "pocsag") or jpath.exists()):
            continue
        try:
            z, rate = load_iq(src)
            n = np.arange(len(z))
            bb = z * np.exp(-2j * np.pi * (float(rf_hz) - float(center_hz)) * n / rate)
            audio, arate = W.demodulate(bb, rate, amode)
            write_wav(out, audio, arate)
            print(f"{band:9s} {rf_hz/1e6:10.4f} MHz  {mode:7s} -> {out.name}")
            if mode in ("aprs", "pocsag"):
                data = {"peak_id": peak_id, "band": band, "frequency_hz": rf_hz, "decoders": []}
                if jpath.exists():
                    try:
                        data = json.loads(jpath.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                if MULTIMON:
                    dec = "AFSK1200" if mode == "aprs" else "POCSAG1200"
                    lines = run_multimon(audio, arate, dec)
                    if mode == "pocsag":
                        lines += run_multimon(audio, arate, "POCSAG512")
                    data["decoders"] = [d for d in data.get("decoders", []) if d.get("type") not in ("APRS", "POCSAG")]
                    if lines:
                        data["decoders"].insert(0, {
                            "type": "APRS" if mode == "aprs" else "POCSAG",
                            "confidence": 95, "text": "\n".join(lines[:40]),
                            "detail": f"multimon-ng, {len(lines)} Pakete", "messages": []})
                        print(f"          {len(lines)} Pakete dekodiert")
                else:
                    data.setdefault("notes", []).append("multimon-ng nicht installiert (sudo apt install multimon-ng)")
                jpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            done += 1
        except Exception as e:
            print(f"FEHLER {band} {rf_hz/1e6:.4f} MHz: {e}")
    print(f"VHF/UHF-Audio erzeugt: {done}")


if __name__ == "__main__":
    main()
