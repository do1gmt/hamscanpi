#!/usr/bin/env python3
"""
RDS aus den UKW-Treffern eines Scans dekodieren (Band „Rundfunk“, Betriebsart wfm).

Die Treffer liegen als I/Q-WAV mit 240 kHz vor (Mitte = Sender). Gefunden wird, was der Sender
in der Aufnahmezeit schickt: meist Sendername, PI und Programmart, oft Radiotext, manchmal die Uhrzeit.
Ergebnis: Eintrag „RDS“ in reports/<aufnahme>_decode.json (Trefferkarte, Liste, UKW-Senderliste).
"""
import json
import sqlite3
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rds as R                    # noqa: E402
from applog import log as applog   # noqa: E402

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "scanner.db"
REPORTS = BASE / "reports"


def load_iq(path):
    with wave.open(str(path), "rb") as w:
        rate, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        a = np.frombuffer(w.readframes(n), dtype="<i2")
    if ch != 2:
        return None, None, rate
    a = a.reshape(-1, 2).astype(np.float32) / 32768.0
    return a[:, 0], a[:, 1], rate


def pending():
    """Anzahl der UKW-Aufnahmen, deren RDS noch nicht geprüft ist."""
    if not DB.exists():
        return 0
    db = sqlite3.connect(DB)
    rows = db.execute("""SELECT p.recording_path FROM peak p JOIN scan s ON s.id = p.scan_id
                         WHERE p.recording_path IS NOT NULL AND (p.mode = 'wfm' OR s.band = 'Rundfunk')""").fetchall()
    db.close()
    n = 0
    for (rec,) in rows:
        out = REPORTS / f"{Path(rec).stem}_decode.json"
        try:
            if Path(rec).exists() and not json.loads(out.read_text(encoding="utf-8")).get("rds_tested"):
                n += 1
        except Exception:
            n += Path(rec).exists()
    return n


def main(only_new=True):
    if not DB.exists():
        return 0
    db = sqlite3.connect(DB)
    rows = db.execute("""SELECT p.id, p.rf_hz, p.recording_path, s.band FROM peak p JOIN scan s ON s.id = p.scan_id
                         WHERE p.recording_path IS NOT NULL AND (p.mode = 'wfm' OR s.band = 'Rundfunk')
                         ORDER BY p.id""").fetchall()
    db.close()
    found = 0
    for pid, rf, rec, band in rows:
        src = Path(rec)
        if not src.exists():
            continue
        out = REPORTS / f"{src.stem}_decode.json"
        try:
            data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        except Exception:
            data = {}
        if only_new and data.get("rds_tested"):
            continue
        try:
            i, q, rate = load_iq(src)
            info = R.decode_iq(i, q, rate) if i is not None else None
        except Exception as e:
            applog("WARN", "rds", "rds_failed", file=src.name, error=f"{type(e).__name__}: {e}")
            info = None
        data.update({"peak_id": pid, "band": band, "frequency_hz": rf, "rds_tested": True})
        decs = [d for d in data.get("decoders", []) if d.get("type") != "RDS"]
        if info:
            ok = round(100 * info["blocks_ok"] / max(1, info["blocks"]))
            decs.insert(0, {"type": "RDS", "confidence": min(99, 60 + info["groups"]), "validated": True,
                            "text": R.summary(info) + "\n" + "\n".join(R.details(info).split("\n")[1:]),
                            "detail": f"{info['groups']} RDS-Gruppen, {ok} % fehlerfrei · {rf/1e6:.1f} MHz",
                            "rds": info})
            found += 1
            print(f"  {rf/1e6:6.1f} MHz  {R.summary(info)}")
        data["decoders"] = decs
        REPORTS.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if found:
        applog("INFO", "rds", "rds_decoded", stations=found)
    return found


if __name__ == "__main__":
    if "--pending" in sys.argv:
        print(pending())
        raise SystemExit(0)
    n = main(only_new="--all" not in sys.argv)
    print(f"RDS: {n} Sender dekodiert")
