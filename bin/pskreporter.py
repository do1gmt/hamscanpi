#!/usr/bin/env python3
"""
FT8/FT4-Spots an PSK Reporter melden (https://pskreporter.info).

Protokoll nach https://pskreporter.info/pskdev.html: IPFIX (RFC 7011) per UDP an
report.pskreporter.info:4739. Jedes Paket enthält die beiden Vorlagen (Empfänger, Sender), einen
Empfänger-Datensatz (eigenes Rufzeichen, Locator, Software, Antenne) und die Spots
(Rufzeichen, Frequenz, SNR, Betriebsart, Locator, Zeitpunkt).

Gemeldet werden nur FT8/FT4-Stationen aus der Tabelle heard, höchstens alle 5 Minuten
(so wünscht es PSK Reporter). Voraussetzung: eigenes Rufzeichen und Locator in den Einstellungen,
„An PSK Reporter melden“ eingeschaltet.
"""
import json
import random
import socket
import struct
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
STATE = BASE / "data" / "pskreporter_state.json"
HOST, PORT = "report.pskreporter.info", 4739
ENT = 30351                       # Enterprise-Nummer von PSK Reporter
RX_TPL, TX_TPL = 0x9992, 0x9993
MIN_INTERVAL_S = 300
MAX_PACKET = 1400


def _vstr(s):
    b = str(s or "").encode("utf-8")[:254]
    return bytes([len(b)]) + b


def _pad(b):
    return b + b"\0" * ((4 - len(b) % 4) % 4)


def _set(set_id, body):
    body = _pad(body)
    return struct.pack("!HH", set_id, 4 + len(body)) + body


def _efield(fid, length):
    return struct.pack("!HHI", 0x8000 | fid, length, ENT)


def templates():
    # Empfänger (Options-Vorlage, Set 3): receiverCallsign, receiverLocator, decodingSoftware, antennaInformation
    rx = struct.pack("!HHH", RX_TPL, 4, 0) + _efield(2, 0xFFFF) + _efield(4, 0xFFFF) + _efield(8, 0xFFFF) + _efield(9, 0xFFFF)
    # Sender (Vorlage, Set 2): senderCallsign, frequency, sNR, mode, senderLocator, informationSource, flowStartSeconds
    tx = (struct.pack("!HH", TX_TPL, 7) + _efield(1, 0xFFFF) + _efield(5, 4) + _efield(6, 1) + _efield(10, 0xFFFF)
          + _efield(3, 0xFFFF) + _efield(11, 1) + struct.pack("!HH", 150, 4))
    return _set(3, rx) + _set(2, tx)


def software():
    try:
        return "HamScanPi " + (BASE / "VERSION").read_text().strip()
    except OSError:
        return "HamScanPi"


def rx_record(call, locator, antenna=""):
    return _set(RX_TPL, _vstr(call) + _vstr(locator) + _vstr(software()) + _vstr(antenna))


def tx_record(s):
    snr = int(round(s.get("snr") or 0))
    snr = max(-128, min(127, snr))
    return (_vstr(s["call"]) + struct.pack("!I", int(round(s["freq_hz"]))) + struct.pack("!b", snr)
            + _vstr(s["mode"]) + _vstr(s.get("grid") or "") + bytes([1]) + struct.pack("!I", int(s["t"])))


def packets(spots, call, locator, antenna, seq, domain):
    """IPFIX-Nachrichten (je höchstens MAX_PACKET Bytes)."""
    head_len = 16
    fixed = templates() + rx_record(call, locator, antenna)
    out, chunk = [], []
    for sp in spots:
        rec = tx_record(sp)
        size = head_len + len(fixed) + 4 + sum(len(r) for r in chunk) + len(rec) + 3
        if chunk and size > MAX_PACKET:
            out.append(chunk); chunk = []
        chunk.append(rec)
    if chunk:
        out.append(chunk)
    msgs = []
    for i, ch in enumerate(out):
        body = fixed + _set(TX_TPL, b"".join(ch))
        hdr = struct.pack("!HHIII", 10, 16 + len(body), int(time.time()), (seq + i) & 0xFFFFFFFF, domain)
        msgs.append(hdr + body)
    return msgs


def dedupe(spots):
    """Je Rufzeichen, Band und Betriebsart ein Spot pro Meldung – der mit dem besten SNR (Ortskenner dazu, falls
    nur ein anderer Takt ihn hatte). Ein CQ-Rufer, der in Live alle 30 s gehört wird, geht so nur einmal raus."""
    best = {}
    for sp in spots:
        k = (sp["call"], sp.get("band") or round(float(sp["freq_hz"]) / 1e6), sp["mode"])
        cur = best.get(k)
        if cur is None or (sp.get("snr") or -99) > (cur.get("snr") or -99):
            grid = (cur or {}).get("grid")
            best[k] = dict(sp, grid=sp.get("grid") or grid)
        elif not cur.get("grid") and sp.get("grid"):
            cur["grid"] = sp["grid"]
    return sorted(best.values(), key=lambda s: s["t"])


def _state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def report(db, call, locator, antenna="", log=None, now=None, send=True):
    """Neue FT8/FT4-Spots seit dem letzten Lauf melden. Rückgabe Anzahl."""
    now = now or time.time()
    st = _state()
    if now - st.get("sent", 0) < MIN_INTERVAL_S:
        return 0
    last = int(st.get("last_heard_id", 0))
    if "last_heard_id" not in st:                        # beim ersten Mal nichts Altes nachmelden (auch bei leerer Liste)
        row = db.execute("SELECT COALESCE(MAX(id), 0) FROM heard").fetchone()
        st.update(last_heard_id=int(row[0]), sent=now, domain=random.randint(1, 2**31), seq=0)
        STATE.write_text(json.dumps(st), encoding="utf-8")
        return 0
    has_ant = "antenna" in {r[1] for r in db.execute("PRAGMA table_info(heard)")}
    rows = db.execute(f"""SELECT id, time, call, freq_hz, mode, grid, snr, band, {'antenna' if has_ant else 'NULL'} FROM heard
                          WHERE id > ? AND mode IN ('FT8', 'FT4') ORDER BY id LIMIT 2000""", (last,)).fetchall()
    st["sent"] = now
    if not rows:
        STATE.write_text(json.dumps(st), encoding="utf-8")
        return 0
    spots = []
    for r in rows:
        try:
            t = datetime.fromisoformat(str(r[1])).timestamp()
        except ValueError:
            t = now
        if now - t > 3600:                               # PSK Reporter will aktuelle Spots
            continue
        spots.append({"call": r[2], "freq_hz": r[3], "mode": r[4], "grid": r[5], "snr": r[6], "t": t,
                      "band": r[7], "antenna": r[8] or antenna})
    spots = dedupe(spots)
    msgs, seq0, dom = [], int(st.get("seq", 0)), int(st.get("domain") or random.randint(1, 2**31))
    by_ant = {}
    for sp in spots:                                    # je Antenne eigene Pakete (Empfänger-Datensatz enthält sie)
        by_ant.setdefault(sp["antenna"] or "", []).append(sp)
    for ant, group in by_ant.items():
        msgs += packets(group, call, locator, ant, seq0 + len(msgs), dom)
    if send and msgs:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            addr = socket.getaddrinfo(HOST, PORT, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
            for m in msgs:
                s.sendto(m, addr)
        except OSError as e:
            # kein Netz: die Spots bleiben offen (last_heard_id unverändert) und werden in 5 min erneut
            # versucht; gewarnt wird nur einmal je Ausfall
            first = not st.get("offline_since")
            st.setdefault("offline_since", now)
            STATE.write_text(json.dumps(st), encoding="utf-8")
            if log and first:
                log("WARN", "pskreporter_offline", error=f"{type(e).__name__}: {e}",
                    hint="Spots werden nachgemeldet, sobald das Internet wieder da ist (höchstens 1 h alte)")
            return 0
        finally:
            s.close()
    if st.pop("offline_since", None) and log:
        log("INFO", "pskreporter_resumed", spots=len(spots))
    st["seq"] = int(st.get("seq", 0)) + len(msgs)
    st["last_heard_id"] = int(rows[-1][0])
    STATE.write_text(json.dumps(st), encoding="utf-8")
    if log and spots:
        log("INFO", "pskreporter_sent", spots=len(spots), packets=len(msgs))
    return len(spots)
