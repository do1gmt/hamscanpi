#!/usr/bin/env python3
"""Lokale config/scanner.json nach einem Update ergänzen (von bin/update.sh aufgerufen).

Übernimmt aus config/scanner.default.json: neue Einstellungen (fehlende Schlüssel), den aktuellen
Satellitenkatalog und den Rechtshinweis. Eigene Werte (PPM, Verstärkung, Antennen …) bleiben."""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "bin"))
import satellites as SAT  # noqa: E402

local, default = BASE / "config" / "scanner.json", BASE / "config" / "scanner.default.json"
if not local.exists():
    local.write_text(default.read_text(encoding="utf-8"), encoding="utf-8")
    print("config/scanner.json angelegt")
    sys.exit(0)
c = json.loads(local.read_text(encoding="utf-8"))
d = json.loads(default.read_text(encoding="utf-8"))
added = [k for k in d if k not in c and k != "ranges"]
for k in added:
    c[k] = d[k]
c["legal_note"] = d.get("legal_note", c.get("legal_note", ""))
for r in c.get("ranges", []):
    if r.get("satellite") or r.get("name") == "Satelliten":
        r["segments"] = SAT.segments_for_bandplan()
        fs = [s["center_hz"] for s in SAT.SATELLITES]
        r["start_hz"], r["stop_hz"] = min(fs), max(fs)
# Wettersatelliten: NOAA-APT gibt es seit 2025 nicht mehr – Segment aus der Vorlage übernehmen
dw = next((r for r in d.get("ranges", []) if r["name"] == "Wettersat"), None)
for r in c.get("ranges", []):
    if dw and r.get("name") == "Wettersat":
        for sg in r.get("segments", []):
            if sg.get("id") == "apt" and "NOAA" in sg.get("label", ""):
                new = next((x for x in dw["segments"] if x["id"] == "apt"), None)
                if new:
                    sg.update(label=new["label"], frequencies_hz=new["frequencies_hz"], note=new["note"])
                    added.append("Wettersat (Meteor statt NOAA)")
names = {r["name"] for r in c.get("ranges", [])}
new_bands = [r for r in d.get("ranges", []) if r["name"] not in names]
c.setdefault("ranges", []).extend(new_bands)
local.write_text(json.dumps(c, indent=2, ensure_ascii=False), encoding="utf-8")
print("config/scanner.json ergänzt" + (": " + ", ".join(added + [b["name"] for b in new_bands]) if added or new_bands else ""))
