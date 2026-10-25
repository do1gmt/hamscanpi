#!/usr/bin/env python3
"""Darf ein Update jetzt die Dienste neu starten? Nicht während einer Satellitenaufnahme oder ihrer
Auswertung (systemd beendet beim Neustart auch sat_pass.py und rtl_sdr) und nicht kurz vor einer
geplanten Aufnahme.
   python3 bin/update_window.py [--margin MIN]
Ausgabe, eine Zeile: "<Sekunden bis frei> <Grund>"; 0 = jetzt frei.
HSP_BASE: Installationsverzeichnis (das Update ruft diese Datei aus dem neuen Stand auf, gerechnet wird
mit Einstellungen, Bahndaten und satellites.py der laufenden Installation)."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

BASE = Path(os.environ.get("HSP_BASE") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(BASE / "bin"))
AFTER_S = 120                 # nach dem Aufnahmeende: Auswertung startet, Pause zum Durchatmen


def running_pass():
    """PID eines laufenden sat_pass.py (Aufnahme oder Auswertung nach dem Abkoppeln), sonst None."""
    for d in Path("/proc").iterdir():
        if not d.name.isdigit() or int(d.name) == os.getpid():
            continue
        try:
            cmd = (d / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if any(c.endswith(b"sat_pass.py") for c in cmd):
            return int(d.name)
    return None


def planned():
    """Geplante Aufnahmen der nächsten Stunden wie im Scheduler: (start, end, name)."""
    st = json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
    try:
        from scan_plan import effective_settings
        st = effective_settings(st)
    except Exception:
        pass
    if "Satelliten" not in (st.get("selected_bands") or []):
        return []
    import satellites as SAT
    seg = (st.get("selected_segments") or {}).get("Satelliten") or ["all"]
    ids = [s["id"] for s in SAT.SATELLITES] if "all" in seg else [x for x in seg if x in SAT.BY_ID]
    res = SAT.upcoming(ids, st.get("my_locator", ""), hours=6, past_s=1800, fetch=False,
                       prio=st.get("sat_prio") or {})
    return sorted((r["start"], r["end"], r.get("name") or "Überflug") for r in res.get("recordings", []))


def free_in(now, recs, margin_s):
    """Sekunden bis zum ersten Zeitpunkt, an dem keine Aufnahme läuft und keine in margin_s beginnt."""
    t, why = now, ""
    moved = True
    while moved:
        moved = False
        for s, e, name in recs:
            if s - margin_s <= t < e + AFTER_S:
                t, why, moved = e + AFTER_S, f"Überflug {name} {time.strftime('%H:%M', time.gmtime(s))}–" \
                                             f"{time.strftime('%H:%M', time.gmtime(e))} UTC", True
    return max(0, int(t - now)), why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--margin", type=float, default=15, help="Minuten Abstand vor einer Aufnahme")
    a = ap.parse_args()
    pid = running_pass()
    if pid:
        print(f"60 Satellitenaufnahme oder Auswertung läuft (PID {pid})")
        return
    try:
        recs = planned()
    except Exception as e:                                  # im Zweifel nicht blockieren
        print(f"0 Plan nicht lesbar ({type(e).__name__}) – kein Warten")
        return
    s, why = free_in(time.time(), recs, a.margin * 60)
    print(f"{s} {why or 'frei'}")


if __name__ == "__main__":
    main()
