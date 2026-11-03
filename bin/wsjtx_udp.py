#!/usr/bin/env python3
"""FT8/FT4-Dekodierungen im Netzwerk bereitstellen – wie WSJT-X, damit OpenHamClock, GridTracker, JTAlert & Co.
die von HamScanPi empfangenen Stationen anzeigen.

Protokoll: WSJT-X „UDP Server“ (NetworkMessage.hpp, Schema 2): Qt-QDataStream, big-endian.
  Kopf      magic 0xADBCCBDA, schema 2, Typ, Id (utf8)
  Typ 0     Heartbeat: max. Schema 3, Version, Revision         – alle 15 s, damit der Empfänger uns kennt
  Typ 1     Status: Dial-Frequenz, Betriebsart, eigenes Rufzeichen/Locator …  – vor jedem Takt mit Dekodierungen
  Typ 2     Decode: neu, Zeit (ms seit 0 UTC), SNR, DT, Ablage (Hz), Betriebsart (~ FT8, + FT4), Nachricht …
  Typ 6     Close – beim Abschalten
Ziele (Einstellungen → Netzwerk-Ausgabe): eine oder mehrere Adressen „Rechner:Port“, z. B. der Rechner mit
OpenHamClock auf Port 2237, oder eine Multicast-Gruppe (224.0.0.1:2237), die mehrere Programme gleichzeitig hören.

Ohne Netz: Senden passiert in einem eigenen Faden mit Warteschlange – Scan und Live warten nie darauf. Namen werden
mit Zeitgrenze aufgelöst und zwischengespeichert. Klappt ein Ziel nicht (kein Netz, Rechner aus), wird das einmal
protokolliert, die Dekodierungen der letzten 2 Minuten werden aufgehoben und nachgeschickt, sobald es wieder geht
(ältere sind für eine Live-Anzeige wertlos und werden verworfen). Zustand: data/wsjtx_udp_state.json.
"""
import json
import queue
import socket
import struct
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SETTINGS = BASE / "config" / "web_settings.json"
STATE = BASE / "data" / "wsjtx_udp_state.json"

MAGIC = 0xADBCCBDA
SCHEMA = 2
MODE_CHAR = {"FT8": "~", "FT4": "+"}
KEEP_S = 120                       # so lange werden Dekodierungen bei Netzausfall aufgehoben
HEARTBEAT_S = 15


def _u32(v):
    return struct.pack(">I", int(v) & 0xFFFFFFFF)


def _utf8(s):
    if s is None:
        return b"\xff\xff\xff\xff"
    b = str(s).encode("utf-8")
    return _u32(len(b)) + b


def _bool(v):
    return b"\x01" if v else b"\x00"


def _head(typ, ident):
    return _u32(MAGIC) + _u32(SCHEMA) + _u32(typ) + _utf8(ident)


def heartbeat(ident, version):
    return _head(0, ident) + _u32(3) + _utf8(version) + _utf8("")


def status(ident, dial_hz, mode, de_call="", de_grid="", decoding=True):
    return (_head(1, ident) + struct.pack(">Q", int(dial_hz)) + _utf8(mode) + _utf8("") + _utf8("") + _utf8(mode)
            + _bool(False) + _bool(False) + _bool(decoding) + _u32(0) + _u32(0) + _utf8(de_call) + _utf8(de_grid)
            + _utf8("") + _bool(False) + _utf8("") + _bool(False) + struct.pack(">B", 0) + _u32(0)
            + _u32(15 if mode == "FT8" else 7) + _utf8("HamScanPi") + _utf8(""))


def decode(ident, t_utc, snr, dt, df_hz, mode, text, new=True):
    """t_utc: Unixzeit des Takts; Zeitfeld = Millisekunden seit Mitternacht UTC (QTime)."""
    ms = int((t_utc % 86400) * 1000)
    return (_head(2, ident) + _bool(new) + _u32(ms) + struct.pack(">i", int(round(snr)))
            + struct.pack(">d", float(dt or 0.0)) + _u32(max(0, int(round(df_hz)))) + _utf8(MODE_CHAR.get(mode, "~"))
            + _utf8(text) + _bool(False) + _bool(False))


def close(ident):
    return _head(6, ident)


def parse(data):
    """Rückweg für Tests: Kopf und Typ eines Pakets (magic, schema, Typ, Id)."""
    magic, schema, typ = struct.unpack(">III", data[:12])
    n = struct.unpack(">I", data[12:16])[0]
    return magic, schema, typ, data[16:16 + n].decode()


# ---------------------------------------------------------------- Einstellungen und Ziele
def cfg():
    try:
        s = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    c = dict(s.get("wsjtx_udp") or {})
    c.setdefault("enabled", False)
    c.setdefault("targets", "")
    c.setdefault("ident", "HamScanPi")
    c["my_call"], c["my_locator"] = s.get("my_call", ""), s.get("my_locator", "")
    return c


def parse_targets(text):
    """„192.168.178.50:2237, 224.0.0.1:2237, ohc.local“ -> [(Rechner, Port)] (Port fehlt: 2237)."""
    out = []
    for part in str(text or "").replace(";", ",").replace("\n", ",").split(","):
        p = part.strip()
        if not p:
            continue
        host, _, port = p.rpartition(":") if p.count(":") == 1 else (p, "", "")
        host = host or p
        try:
            port = int(port) if port else 2237
        except ValueError:
            raise ValueError(f"Port ungültig: {p}")
        if not 1 <= port <= 65535 or not host or " " in host:
            raise ValueError(f"Ziel ungültig: {p} (Rechner:Port, z. B. 192.168.178.50:2237)")
        out.append((host, port))
    return out


# ---------------------------------------------------------------- Sender (eigener Faden)
class Sender:
    def __init__(self):
        self.q = queue.Queue(maxsize=2000)
        self.pending = []              # (t, ziel, paket) – bei Netzausfall aufgehoben
        self.addr_cache = {}           # host -> (addr, Zeit)
        self.state = {"sent": 0, "dropped": 0, "last_ok": None, "last_error": None, "error_since": None, "targets": {}}
        self.sock = None
        self.last_hb = 0.0
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def submit(self, items):
        try:
            self.q.put_nowait(items)
        except queue.Full:
            self.state["dropped"] += len(items)

    def _resolve(self, host, port):
        c = self.addr_cache.get((host, port))
        if c and time.time() - c[1] < 300:
            return c[0]
        res = [None]

        def look():
            try:
                res[0] = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
            except OSError:
                pass
        t = threading.Thread(target=look, daemon=True)
        t.start()
        t.join(3.0)                    # Namensauflösung ohne Netz kann lange hängen
        if res[0] is None:
            raise OSError(f"Name {host} nicht auflösbar")
        self.addr_cache[(host, port)] = (res[0], time.time())
        return res[0]

    def _send(self, target, pkt):
        if self.sock is None:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            self.sock.settimeout(1.0)
        self.sock.sendto(pkt, self._resolve(*target))

    def _deliver(self, target, pkt, t):
        key = f"{target[0]}:{target[1]}"
        try:
            self._send(target, pkt)
            self.state["sent"] += 1
            ts = self.state["targets"].setdefault(key, {})
            if ts.get("error"):
                _log("INFO", "wsjtx_udp_resumed", target=key)
            ts.update(ok=time.time(), error=None)
            self.state["last_ok"] = time.time()
            return True
        except OSError as e:
            ts = self.state["targets"].setdefault(key, {})
            if not ts.get("error"):
                _log("WARN", "wsjtx_udp_failed", target=key, error=f"{type(e).__name__}: {e}",
                     hint="Dekodierungen der letzten 2 min werden nachgeschickt, sobald das Ziel erreichbar ist")
            ts.update(error=f"{type(e).__name__}: {e}", error_t=time.time())
            self.state["last_error"] = f"{key}: {type(e).__name__}: {e}"
            if t is not None:
                self.pending.append((t, target, pkt))
            return False

    def _run(self):
        last_state = 0.0
        while True:
            try:
                items = self.q.get(timeout=5.0)
            except queue.Empty:
                items = []
            c = cfg()
            try:
                targets = parse_targets(c["targets"]) if c.get("enabled") else []
            except ValueError:
                targets = []
            now = time.time()
            if targets and now - self.last_hb >= HEARTBEAT_S:
                self.last_hb = now
                for tg in targets:
                    self._deliver(tg, heartbeat(c["ident"], _version()), None)
            if self.pending and targets:                       # Nachholen, was während des Ausfalls liegen blieb
                old, self.pending = self.pending, []
                for k, (t, tg, pkt) in enumerate(old):
                    if now - t > KEEP_S or tg not in targets:
                        self.state["dropped"] += 1
                    elif not self._deliver(tg, pkt, t):         # noch nicht erreichbar: Rest bleibt liegen
                        self.pending.extend(x for x in old[k + 1:] if now - x[0] <= KEEP_S)
                        break
            for t, pkt_fn in items:
                for tg in targets:
                    self._deliver(tg, pkt_fn(c), t)
            if len(self.pending) > 2000:
                self.state["dropped"] += len(self.pending) - 2000
                self.pending = self.pending[-2000:]
            if now - last_state > 10 or items:
                last_state = now
                try:
                    STATE.parent.mkdir(parents=True, exist_ok=True)
                    st = dict(self.state, pending=len(self.pending), updated=now)
                    tmp = STATE.with_suffix(f".{threading.get_ident()}.tmp")
                    tmp.write_text(json.dumps(st), encoding="utf-8")
                    tmp.replace(STATE)
                except OSError:
                    pass


def _version():
    try:
        return (BASE / "VERSION").read_text().strip()
    except OSError:
        return "?"


def _log(level, event, **d):
    try:
        from applog import log
        log(level, "wsjtx_udp", event, **d)
    except Exception:
        pass


_sender = [None]
_lock = threading.Lock()


def _get():
    with _lock:
        if _sender[0] is None:
            _sender[0] = Sender()
        return _sender[0]


def publish(dial_hz, mode, t_slot, messages):
    """Einen Takt mit Dekodierungen ausgeben (Status mit Dial-Frequenz, dann je Nachricht ein Decode).
    Kehrt sofort zurück; ist die Ausgabe aus, passiert nichts."""
    c = cfg()
    if not c.get("enabled") or not c.get("targets") or not messages:
        return False
    mode = str(mode).upper()
    items = [(t_slot, lambda cc, d=dial_hz, m=mode: status(cc["ident"], d, m, cc["my_call"], cc["my_locator"]))]
    for msg in messages:
        items.append((t_slot, lambda cc, x=msg: decode(cc["ident"], t_slot, x.get("snr", 0), x.get("dt", 0.0),
                                                       x.get("audio_hz", 0), mode, x.get("text", ""))))
    _get().submit(items)
    return True


def flush(timeout=3.0):
    """Vor dem Beenden eines kurzlebigen Prozesses (Scanner): Warteschlange abarbeiten lassen."""
    s = _sender[0]
    if not s:
        return
    t0 = time.time()
    while not s.q.empty() and time.time() - t0 < timeout:
        time.sleep(0.05)
    time.sleep(0.1)


def test(targets_text, ident="HamScanPi"):
    """„Testpaket senden“: Heartbeat, Status und eine Dekodierung direkt (ohne Warteschlange) – Ergebnis je Ziel."""
    out = []
    s = Sender.__new__(Sender)
    s.addr_cache, s.sock = {}, None
    c = cfg()
    for tg in parse_targets(targets_text):
        try:
            s._send(tg, heartbeat(ident, _version()))
            s._send(tg, status(ident, 14_074_000, "FT8", c["my_call"], c["my_locator"]))
            s._send(tg, decode(ident, time.time() // 15 * 15, -10, 0.2, 1500, "FT8",
                               f"CQ {c['my_call'] or 'TEST'} {(c['my_locator'] or 'JN59')[:4]}"))
            out.append({"target": f"{tg[0]}:{tg[1]}", "ok": True})
        except OSError as e:
            out.append({"target": f"{tg[0]}:{tg[1]}", "ok": False, "error": f"{type(e).__name__}: {e}"})
    return out


def state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}
