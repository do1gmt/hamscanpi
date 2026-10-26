#!/usr/bin/env python3
"""Darf ein Update jetzt die Dienste neu starten? Nicht während einer Satellitenaufnahme oder ihrer
Auswertung (systemd beendet beim Neustart auch sat_pass.py und rtl_sdr) und nicht kurz vor einer
geplanten Aufnahme.
   python3 bin/update_window.py [--margin MIN] [--tail MIN]
Ausgabe, eine Zeile: "<Sekunden bis frei> <Grund>"; 0 = jetzt frei.
Oder "STOP <PID> <Grund>": eine laufende Aufnahme ist fast zu Ende (höchstens --tail Minuten übrig) oder
hat niedrige Priorität ("wenn Stick frei") – das Update bricht sie sauber ab (SIGTERM wie der Knopf im
Dashboard: die Aufnahme endet geordnet, das bisher Aufgenommene wird ausgewertet) und wartet dann nur
noch auf die Auswertung. Die Auswertung selbst wird nie unterbrochen.
Aufnahmen mit niedriger Priorität, die erst noch beginnen, halten das Update nicht auf.
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
REC_PID = BASE / "data" / "sat_rec.pid"   # gibt es nur, solange aufgenommen wird (nicht bei der Auswertung)


def _settings():
    st = json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
    try:
        from scan_plan import effective_settings
        st = effective_settings(st)
    except Exception:
        pass
    return st


def recording():
    """Laufende Aufnahme: (PID, Restsekunden, Kanäle) – None, wenn gerade nicht aufgenommen wird."""
    try:
        pid = int(REC_PID.read_text().strip())
        args = (Path("/proc") / str(pid) / "cmdline").read_bytes().decode(errors="replace").split("\0")
    except (OSError, ValueError):
        return None
    if not any(a.endswith("sat_pass.py") for a in args):
        return None
    def arg(name):
        return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else ""
    try:
        until = float(arg("--until") or arg("--los") or 0)
    except ValueError:
        until = 0
    chans = [c for c in (arg("--sats") or arg("--sat")).split(",") if c]
    return pid, (until - time.time()) if until else None, chans


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
    st = _settings()
    if "Satelliten" not in (st.get("selected_bands") or []):
        return []
    import satellites as SAT
    seg = (st.get("selected_segments") or {}).get("Satelliten") or ["all"]
    ids = [s["id"] for s in SAT.SATELLITES] if "all" in seg else [x for x in seg if x in SAT.BY_ID]
    res = SAT.upcoming(ids, st.get("my_locator", ""), hours=6, past_s=1800, fetch=False,
                       prio=st.get("sat_prio") or {})
    # Aufnahmen "wenn Stick frei" (niedrige Priorität) halten ein Update nicht auf
    return sorted((r["start"], r["end"], r.get("name") or "Überflug") for r in res.get("recordings", [])
                  if r.get("prio", 99) > SAT.OPPORTUNISTIC_PRIO)


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
    ap.add_argument("--margin", type=float, default=3, help="Minuten Abstand vor einer Aufnahme (das Update dauert ~1 min)")
    ap.add_argument("--tail", type=float, default=3, help="laufende Aufnahme abbrechen, wenn höchstens so viele Minuten übrig")
    a = ap.parse_args()
    rec = recording()
    if rec:
        pid, rest, chans = rec
        low = False
        try:
            import satellites as SAT
            pr = _settings().get("sat_prio") or {}
            low = bool(chans) and max(SAT.eff_prio(c, pr) for c in chans if c in SAT.BY_ID) <= SAT.OPPORTUNISTIC_PRIO
        except Exception:
            pass
        if rest is not None and rest <= a.tail * 60:
            print(f"STOP {pid} Aufnahme {','.join(chans)} ist fast zu Ende (noch {max(0, rest) / 60:.0f} min)")
            return
        if low:
            print(f"STOP {pid} Aufnahme {','.join(chans)} hat niedrige Priorität (wenn Stick frei)")
            return
        wait = int(max(60, min(rest - a.tail * 60, 300))) if rest is not None else 60
        print(f"{wait} Satellitenaufnahme {','.join(chans)} läuft noch {max(0, rest or 0) / 60:.0f} min "
              f"(wird {a.tail:.0f} min vor Ende abgebrochen)")
        return
    pid = running_pass()
    if pid:
        print(f"30 Auswertung des letzten Überflugs läuft (PID {pid}) – wird nicht unterbrochen")
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
