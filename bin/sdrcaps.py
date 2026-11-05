#!/usr/bin/env python3
"""Was kann ein angesteckter RTL-Stick? Frequenzbereich nach Tuner und Modell, Kurzwelle per
Direktabtastung (Q-Zweig) oder Aufwärtsmischer – und wie der installierte Treiber die Direktabtastung
schreibt (`rtl_sdr -D`, `-D 2`, `rtl_fm -E direct2` – je nach Version verschieden; mit der falschen
Schreibweise nahm `rtl_sdr` die „2“ als Dateinamen).

Die Tabelle ist bewusst klein; unbekannte Sticks gelten als „nur ab 24 MHz“.
   python3 bin/sdrcaps.py          zeigt Treiber-Schreibweisen und die Fähigkeiten des Haupt-Sticks
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CACHE = BASE / "data" / "sdr_tools.json"
HF_SPLIT = 24_000_000            # darunter: Direktabtastung (Tuner R820T arbeitet erst ab ~24 MHz)

# Tuner -> Bereich (Hz)
TUNERS = {
    "Rafael Micro R820T": (24e6, 1766e6), "Rafael Micro R828D": (24e6, 1766e6),
    "Elonics E4000": (52e6, 2200e6), "Fitipower FC0012": (22e6, 948.6e6), "Fitipower FC0013": (22e6, 1100e6),
    "FCI FC2580": (146e6, 924e6), "Rafael Micro R860": (24e6, 1766e6),
}
# Modelle mit Kurzwelle: (Suchmuster in Hersteller/Produkt, klein geschrieben) -> Art, Bereich
MODELS = [
    (("nesdr smart v5",), {"hf": "direct_q", "hf_range": (100e3, 28.8e6), "name": "NESDR SMArt v5"}),
    (("nesdr smart", "v5"), {"hf": "direct_q", "hf_range": (100e3, 28.8e6), "name": "NESDR SMArt v5"}),
    (("rtl-sdr blog v4",), {"hf": "upconverter", "hf_range": (500e3, 28.8e6), "name": "RTL-SDR Blog V4",
                            "needs": "rtl-sdr-blog"}),
    (("blog v3",), {"hf": "direct_q", "hf_range": (500e3, 28.8e6), "name": "RTL-SDR Blog V3"}),
    (("rtl-sdr blog",), {"hf": "direct_q", "hf_range": (500e3, 28.8e6), "name": "RTL-SDR Blog V3"}),
]


def caps(stick):
    """Fähigkeiten eines Sticks aus dem Geräteregister (Hersteller, Produkt, Tuner).
    Rückgabe dict(min_hz, max_hz, hf, hf_range, model, note)."""
    tuner = (stick or {}).get("tuner") or ""
    text = f"{(stick or {}).get('manufacturer') or ''} {(stick or {}).get('product') or ''}".lower()
    lo, hi = TUNERS.get(tuner, (24e6, 1766e6))
    model = next((m for pats, m in MODELS if all(p in text for p in pats)), None)
    if tuner == "Rafael Micro R828D" and "blog" in text:
        model = dict(MODELS[2][1])
    out = {"min_hz": lo, "max_hz": hi, "hf": None, "hf_range": None, "model": None, "note": ""}
    if model:
        out.update(hf=model["hf"], hf_range=list(model["hf_range"]), model=model["name"], min_hz=model["hf_range"][0])
        if model.get("needs") == "rtl-sdr-blog":
            out["note"] = "Kurzwelle braucht den RTL-SDR-Blog-Treiber (Aufwärtsmischer im Stick)"
        else:
            out["note"] = "Kurzwelle per Direktabtastung (Q-Zweig) – Kurzwellenantenne nötig, kein Verstärkungsregler"
    return out


def _usage(tool):
    """Hilfetext eines Werkzeugs (rtl_sdr/rtl_fm geben ihn ohne Argumente auf stderr aus)."""
    path = shutil.which(tool)
    if not path:
        return None, None
    try:
        r = subprocess.run([path, "-h"], capture_output=True, text=True, timeout=5)
        txt = (r.stdout or "") + (r.stderr or "")
    except Exception:
        txt = ""
    return path, txt


def tool_flags(refresh=False):
    """Schreibweise der Direktabtastung je Werkzeug, zwischengespeichert (neu bei geändertem Programm).
    Rückgabe {"rtl_sdr": [...] oder None, "rtl_fm": [...] oder None, "checked": {...}}."""
    cache = {}
    if CACHE.exists() and not refresh:
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    out, stamp = {}, {}
    for tool in ("rtl_sdr", "rtl_fm"):
        path = shutil.which(tool)
        key = f"{path}:{os.stat(path).st_mtime:.0f}" if path else None
        stamp[tool] = key
        if key and cache.get("stamp", {}).get(tool) == key and tool in cache:
            out[tool] = cache[tool]
            continue
        _, txt = _usage(tool)
        flag = None
        if txt:
            if tool == "rtl_fm":
                if re.search(r"direct2", txt):
                    flag = ["-E", "direct2"]
                elif re.search(r"-D\s+direct", txt):
                    flag = ["-D", "2"]
            else:
                m = re.search(r"\[-D\s+([^\]]*)\]", txt)
                if m:
                    # „[-D enable direct sampling (default: off)]“ = Schalter ohne Wert (Q-Zweig);
                    # „[-D direct_sampling_mode, 0 (default/off), 1 (I), 2 (Q) …]“ = mit Wert
                    flag = ["-D", "2"] if re.search(r"mode|\(I\)|\(Q\)|0 \(", m.group(1)) else ["-D"]
        out[tool] = flag
    out["stamp"] = stamp
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(out), encoding="utf-8")
    except OSError:
        pass
    return out


def hf_ok(stick):
    """Kann dieser Stick mit dem installierten Treiber Kurzwelle? (ja/nein, Begründung)"""
    c = caps(stick)
    if not c["hf"]:
        return False, "dieser Stick empfängt erst ab 24 MHz"
    if c["hf"] == "upconverter":
        return False, c["note"]
    fl = tool_flags()
    if not fl.get("rtl_sdr"):
        return False, "der installierte Treiber kann keine Direktabtastung (rtl_sdr ohne -D)"
    return True, c["note"]


def main():
    fl = tool_flags(refresh=True)
    print("Direktabtastung:", {k: v for k, v in fl.items() if k != "stamp"})
    try:
        sys.path.insert(0, str(BASE / "bin"))
        import sticks as STK
        main_ = next((r for r in STK.load()["sticks"].values() if r.get("role") == "main"), None)
    except Exception:
        main_ = None
    if main_:
        print("Haupt-Stick:", main_.get("product"), main_.get("tuner"))
        print("Fähigkeiten:", caps(main_))
        print("Kurzwelle:", hf_ok(main_))


if __name__ == "__main__":
    main()
