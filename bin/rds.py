#!/usr/bin/env python3
"""
RDS-Dekoder für UKW-Rundfunk (IEC 62106).

Eingang ist das Multiplexsignal (MPX) nach der FM-Demodulation, z. B. 240 kHz aus dem Live-Empfang
oder aus einer Scan-Aufnahme. RDS liegt dort auf dem 57-kHz-Hilfsträger: 1187,5 bit/s, BPSK,
biphase- und differenziell codiert, in Gruppen zu 4 Blöcken à 26 bit (16 Daten + 10 Prüfbits).

Dekodiert wird (soweit gesendet):
  PI (Programmkennung), PS (Sendername), PTY (Programmart), TP/TA (Verkehrsfunk), MS (Musik/Sprache),
  RT (Radiotext), RT+ (Interpret/Titel), CT (Uhrzeit des Senders), AF (Alternativfrequenzen),
  ECC (erweiterter Ländercode), PTYN (Programmartname), Kennungen von TMC und anderen Datendiensten.

  RdsDecoder(rate).feed(mpx)   – fortlaufend (Live-Empfang); .info liefert den aktuellen Stand
  decode_mpx(mpx, rate)        – einmalig (Aufnahme)
  decode_iq(i, q, rate)        – FM-Demodulation + RDS aus einer I/Q-Aufnahme (≥ 171 kHz)
"""
import datetime as _dt

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

BITRATE = 1187.5
SUB = 57000.0
POLY = 0x5B9                       # g(x) = x^10 + x^8 + x^7 + x^5 + x^4 + x^3 + 1
OFFSETS = {"A": 0x0FC, "B": 0x198, "C": 0x168, "Cp": 0x350, "D": 0x1B4}

PTY_DE = ["–", "Nachrichten", "Aktuelles", "Information", "Sport", "Bildung", "Hörspiel", "Kultur",
          "Wissenschaft", "Unterhaltung", "Pop", "Rock", "Leichte Musik", "Leichte Klassik",
          "Ernste Klassik", "Sonstige Musik", "Wetter", "Wirtschaft", "Kinder", "Soziales", "Religion",
          "Anrufsendung", "Reise", "Freizeit", "Jazz", "Country", "Landesmusik", "Oldies", "Folk",
          "Dokumentation", "Alarmtest", "Alarm"]
ODA = {0x4BD7: "RT+", 0xCD46: "TMC", 0xCD47: "TMC", 0x0D45: "TMC (Test)", 0x6552: "eRT", 0xC737: "UMC",
       0x4BD8: "eRT+", 0xE1C1: "Action code", 0x1BDA: "iTunes tagging"}
RTPLUS = {1: "title", 2: "album", 4: "artist", 7: "composer", 12: "programme", 31: "station"}

# RDS-Zeichensatz (EBU, Anhang E) – oberhalb von 0x7F abweichend von Latin-1
_EBU = {0x80: "á", 0x81: "à", 0x82: "é", 0x83: "è", 0x84: "í", 0x85: "ì", 0x86: "ó", 0x87: "ò", 0x88: "ú",
        0x89: "ù", 0x8A: "Ñ", 0x8B: "Ç", 0x8C: "Ş", 0x8D: "ß", 0x8E: "¡", 0x8F: "Ĳ", 0x90: "â", 0x91: "ä",
        0x92: "ê", 0x93: "ë", 0x94: "î", 0x95: "ï", 0x96: "ô", 0x97: "ö", 0x98: "û", 0x99: "ü", 0x9A: "ñ",
        0x9B: "ç", 0x9C: "ş", 0x9D: "ğ", 0x9E: "ı", 0x9F: "ĳ", 0xC0: "Á", 0xC1: "À", 0xC2: "É", 0xC3: "È",
        0xC4: "Í", 0xC5: "Ì", 0xC6: "Ó", 0xC7: "Ò", 0xC8: "Ú", 0xC9: "Ù", 0xCA: "Ř", 0xCB: "Č", 0xCC: "Š",
        0xCD: "Ž", 0xCE: "Đ", 0xCF: "Ŀ", 0xD0: "Â", 0xD1: "Ä", 0xD2: "Ê", 0xD3: "Ë", 0xD4: "Î", 0xD5: "Ï",
        0xD6: "Ô", 0xD7: "Ö", 0xD8: "Û", 0xD9: "Ü", 0xDA: "ř", 0xDB: "č", 0xDC: "š", 0xDD: "ž", 0xDE: "đ",
        0xDF: "ŀ", 0xA9: "©", 0xAB: "€", 0xA4: "$"}


def char(b):
    b &= 0xFF
    if b == 0x0D:
        return "\r"                                     # Ende des Radiotexts
    if 0x20 <= b < 0x7F:
        return "#" if b == 0x24 and False else chr(b)
    return _EBU.get(b, " ")


def _mod(w):
    """Rest der Division durch das RDS-Prüfpolynom (26-bit-Block → 10-bit-Syndrom)."""
    for i in range(25, 9, -1):
        if w >> i & 1:
            w ^= POLY << (i - 10)
    return w & 0x3FF


def checkword(data, offset):
    return _mod(data << 10) ^ OFFSETS[offset]


def block_ok(w, offset):
    return _mod(w) == OFFSETS[offset]


def _empty_info():
    return {"pi": None, "ps": None, "pty": None, "pty_name": None, "tp": None, "ta": None, "ms": None,
            "rt": None, "rtplus": {}, "ct": None, "af": [], "ecc": None, "ptyn": None, "oda": [],
            "groups": 0, "blocks_ok": 0, "blocks": 0}


class RdsDecoder:
    """Fortlaufender RDS-Dekoder. feed(mpx) mit beliebig langen Stücken (Abtastrate fest)."""

    def __init__(self, rate):
        self.rate = float(rate)
        self.dec = max(1, int(round(rate / 12000.0)))           # Basisband ~12 kHz
        self.fs2 = self.rate / self.dec
        self.sos = butter(4, 2800, fs=self.rate, output="sos")
        self.zi_r = sosfilt_zi(self.sos) * 0
        self.zi_i = sosfilt_zi(self.sos) * 0
        self.n = 0
        self.bb = np.zeros(0, np.complex64)                     # Basisband-Puffer
        self.bits = []                                          # dekodierte Datenbits (noch ohne Gruppe)
        self.last_b = None
        self.t_next = None                                      # nächster Bit-Abtastzeitpunkt (im Puffer)
        self.info = _empty_info()
        self._ps = [" "] * 8
        self._ps_seen = [False] * 4
        self._rt = [" "] * 64
        self._rt_seen = [False] * 16
        self._rt_ab = None
        self._ptyn = [" "] * 8
        self._rtplus_group = None
        self._rt_complete = None

    # ---------------------------------------------------------------- Signal → Bits
    def feed(self, mpx):
        mpx = np.asarray(mpx, dtype=np.float64)
        if not len(mpx):
            return self.info
        k = np.arange(self.n, self.n + len(mpx), dtype=np.float64)
        self.n += len(mpx)
        lo = np.exp(-2j * np.pi * SUB * k / self.rate)
        x = mpx * lo
        r, self.zi_r = sosfilt(self.sos, x.real, zi=self.zi_r)
        i, self.zi_i = sosfilt(self.sos, x.imag, zi=self.zi_i)
        # Dezimieren im Takt des Gesamtzählers
        off = (-(self.n - len(mpx))) % self.dec
        y = (r[off::self.dec] + 1j * i[off::self.dec]).astype(np.complex64)
        self.bb = np.concatenate([self.bb, y])
        if len(self.bb) >= int(self.fs2 * 0.5):
            self._demod()
        self._groups()
        return self.info

    def _demod(self):
        """Basisband-Puffer → Datenbits. Träger aus y² (gleitend), Bittakt über die Biphase-Größe
        m(t) = h(t) − h(t + P/2) (h = Mittel über ein halbes Bit), fortlaufend nachgeführt."""
        y = self.bb
        P = self.fs2 / BITRATE
        q = y.astype(np.complex128) ** 2
        L = max(8, int(self.fs2 * 0.01))
        qs = np.convolve(q, np.ones(L) / L, mode="same")
        ph = np.unwrap(np.angle(qs)) / 2.0
        r = np.real(y * np.exp(-1j * ph))
        H = max(1, int(round(P / 2)))
        h = np.convolve(r, np.ones(H) / H, mode="full")[H - 1:]
        n = len(h)
        idx = np.arange(n, dtype=np.float64)

        def m_at(t):
            return np.interp(t, idx, h) - np.interp(t + P / 2.0, idx, h)
        end = n - P - L                                    # Rand: Glättungen brauchen Nachlauf
        if self.t_next is None:                            # erstes Mal: Taktphase suchen
            best, t0 = -1.0, 0.0
            for c in np.arange(L, L + P, P / 24.0):
                ts = np.arange(c, end, P)
                v = float(np.mean(np.abs(m_at(ts)))) if len(ts) else 0.0
                if v > best:
                    best, t0 = v, c
            self.t_next = t0
        else:                                              # nachführen (±P/6)
            best, t0 = -1.0, self.t_next
            for d in np.linspace(-P / 6, P / 6, 9):
                ts = np.arange(self.t_next + d, end, P)
                if not len(ts):
                    continue
                v = float(np.mean(np.abs(m_at(ts))))
                if v > best:
                    best, t0 = v, self.t_next + d
            self.t_next = t0
        ts = np.arange(self.t_next, end, P)
        if len(ts):
            b = (m_at(ts) > 0).astype(np.uint8)
            for bit in b:
                if self.last_b is not None:
                    self.bits.append(int(bit ^ self.last_b))
                self.last_b = int(bit)
            self.t_next = ts[-1] + P
        # verbrauchten Anfang verwerfen, etwas Vorlauf für Träger- und Halbbit-Glättung behalten
        cut = int(max(0, self.t_next - 3 * L - 2 * P))
        self.bb = self.bb[cut:]
        self.t_next -= cut

    # ---------------------------------------------------------------- Bits → Gruppen
    def _groups(self):
        bits = self.bits
        i = 0
        out_upto = 0
        while i + 104 <= len(bits):
            w = [int("".join(str(b) for b in bits[i + 26 * j:i + 26 * (j + 1)]), 2) for j in range(4)]
            okA, okB = block_ok(w[0], "A"), block_ok(w[1], "B")
            okC = block_ok(w[2], "C") or block_ok(w[2], "Cp")
            okD = block_ok(w[3], "D")
            if okA and okB and (okC or okD):
                self.info["blocks"] += 4
                self.info["blocks_ok"] += 2 + okC + okD
                self._group(w[0] >> 10, w[1] >> 10, (w[2] >> 10) if okC else None, (w[3] >> 10) if okD else None)
                i += 104
                out_upto = i
            else:
                i += 1
        # verarbeitete Bits verwerfen, Rest (unter 104) behalten
        keep = max(out_upto, len(bits) - 103)
        self.bits = bits[keep:]

    def _group(self, A, B, C, D):
        inf = self.info
        inf["groups"] += 1
        inf["pi"] = f"{A:04X}"
        gtype, ver = B >> 12, (B >> 11) & 1
        inf["tp"] = bool(B >> 10 & 1)
        pty = B >> 5 & 0x1F
        inf["pty"], inf["pty_name"] = pty, PTY_DE[pty]
        g = f"{gtype}{'B' if ver else 'A'}"
        if gtype == 0:                                       # 0A/0B: Sendername, TA, MS, AF
            inf["ta"], inf["ms"] = bool(B >> 4 & 1), ("Musik" if B >> 3 & 1 else "Sprache")
            seg = B & 3
            if D is not None:
                self._ps[seg * 2], self._ps[seg * 2 + 1] = char(D >> 8), char(D)
                self._ps_seen[seg] = True
                if all(self._ps_seen):
                    inf["ps"] = "".join(self._ps)
            if ver == 0 and C is not None:
                for code in (C >> 8, C & 0xFF):
                    if 1 <= code <= 204:
                        f = round(87.5 + code * 0.1, 1)
                        if f not in inf["af"]:
                            inf["af"] = sorted(inf["af"] + [f])
        elif gtype == 1 and ver == 0 and C is not None:      # 1A: ECC
            if (C >> 12 & 7) == 0:
                inf["ecc"] = f"{C & 0xFF:02X}"
        elif gtype == 2:                                     # 2A/2B: Radiotext
            ab = B >> 4 & 1
            if self._rt_ab is not None and ab != self._rt_ab:
                self._rt = [" "] * 64
                self._rt_seen = [False] * 16
                inf["rtplus"] = {}                            # neuer Text: alte Titelangaben verwerfen
            self._rt_ab = ab
            seg = B & 0xF
            if ver == 0:
                chars = ([char(C >> 8), char(C)] if C is not None else [None, None]) + \
                        ([char(D >> 8), char(D)] if D is not None else [None, None])
                for j, c in enumerate(chars):
                    if c is not None:
                        self._rt[seg * 4 + j] = c
            elif D is not None:
                self._rt[seg * 2], self._rt[seg * 2 + 1] = char(D >> 8), char(D)
            self._rt_seen[seg] = True
            self._rt_seg = 4 if ver == 0 else 2
            raw = "".join(self._rt)
            end = raw.find("\r")
            n_used = end if end >= 0 else len(raw.rstrip())
            # auch unvollständig anzeigen (fehlende Teile als „…“), sobald irgendein Teil da ist
            segs = range(0, (n_used + self._rt_seg - 1) // self._rt_seg) if n_used else []
            missing = [k for k in segs if not self._rt_seen[k]]
            parts = []
            for k in segs:
                parts.append(raw[k * self._rt_seg:(k + 1) * self._rt_seg] if self._rt_seen[k] else "…")
            text = "".join(parts)[:n_used + 1].rstrip()
            text = " ".join(text.split())
            if text and text != "…":
                inf["rt"] = text
                inf["rt_complete"] = not missing and end >= 0 or (not missing and n_used >= 60)
        elif gtype == 3 and ver == 0 and D is not None:      # 3A: Datendienste (ODA)
            name = ODA.get(D, f"AID {D:04X}")
            if name not in inf["oda"]:
                inf["oda"] = inf["oda"] + [name]
            if D == 0x4BD7:
                self._rtplus_group = B & 0x1F                # Gruppentyp, der RT+ trägt
        elif gtype == 4 and ver == 0 and C is not None and D is not None:   # 4A: Uhrzeit
            mjd = ((B & 3) << 15) | (C >> 1)
            hour = ((C & 1) << 4) | (D >> 12)
            minute = D >> 6 & 0x3F
            sign = -1 if D >> 5 & 1 else 1
            offs = sign * (D & 0x1F) * 30
            try:
                day = _dt.date(1858, 11, 17) + _dt.timedelta(days=mjd)
                utc = _dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=_dt.timezone.utc)
                inf["ct"] = {"utc": utc.isoformat(), "local_offset_min": offs,
                             "local": (utc + _dt.timedelta(minutes=offs)).strftime("%d.%m.%Y %H:%M")}
            except (ValueError, OverflowError):
                pass
        elif gtype == 8 and ver == 0:                        # 8A: TMC
            if "TMC" not in inf["oda"]:
                inf["oda"] = inf["oda"] + ["TMC"]
        elif gtype == 10 and ver == 0 and C is not None and D is not None:  # 10A: Programmartname
            seg = B & 1
            for j, c in enumerate([C >> 8, C, D >> 8, D]):
                self._ptyn[seg * 4 + j] = char(c)
            inf["ptyn"] = "".join(self._ptyn).strip() or None
        # RT+ in der angekündigten Gruppe
        if self._rtplus_group is not None and ((gtype << 1) | ver) == self._rtplus_group and C is not None and D is not None:
            t1 = ((B & 7) << 3) | (C >> 13)
            s1, l1 = C >> 7 & 0x3F, C >> 1 & 0x3F
            t2 = ((C & 1) << 5) | (D >> 11)
            s2, l2 = D >> 5 & 0x3F, D & 0x1F
            rt = "".join(self._rt)
            sz = getattr(self, "_rt_seg", 4)
            for t, s, l in ((t1, s1, l1), (t2, s2, l2)):
                # nur, wenn alle Teile des Radiotexts für diesen Abschnitt schon empfangen sind
                covered = all(self._rt_seen[k] for k in range(s // sz, min(16, (s + l) // sz + 1)))
                if t in RTPLUS and l and s + l + 1 <= len(rt) and covered:
                    val = rt[s:s + l + 1].strip()
                    if val:
                        inf["rtplus"] = dict(inf["rtplus"], **{RTPLUS[t]: val})


def decode_mpx(mpx, rate):
    d = RdsDecoder(rate)
    step = int(rate * 0.25)
    for a in range(0, len(mpx), step):
        d.feed(mpx[a:a + step])
    d.feed(np.zeros(int(rate * 0.7)))                       # Rest durchschieben
    return d.info


def fm_demod(i, q):
    z = np.asarray(i, dtype=np.float64) + 1j * np.asarray(q, dtype=np.float64)
    return np.angle(z[1:] * np.conj(z[:-1]))


def decode_iq(i, q, rate):
    """I/Q-Aufnahme eines UKW-Senders (Mitte = Sender, ≥ 171 kHz) → RDS-Info oder None."""
    if rate < 150000 or len(i) < rate * 0.5:
        return None
    info = decode_mpx(fm_demod(i, q), rate)
    return info if info["groups"] else None


def details(info):
    """Alle dekodierten Angaben als Zeilen (Trefferkarte)."""
    if not info or not info.get("groups"):
        return ""
    L = []
    head = [f"„{info['ps'].strip()}“" if info.get("ps") else "Sendername noch unvollständig",
            f"PI {info['pi']}" if info.get("pi") else None,
            (info.get("pty_name") + (f" ({info['ptyn']})" if info.get("ptyn") else "")) if info.get("pty") else None,
            ("TP" + (" · Durchsage" if info.get("ta") else "")) if info.get("tp") else None, info.get("ms")]
    L.append(" · ".join(x for x in head if x))
    rp = info.get("rtplus") or {}
    if rp.get("artist") or rp.get("title"):
        L.append("Jetzt: " + " – ".join(x for x in (rp.get("artist"), rp.get("title")) if x)
                 + (f" (Album: {rp['album']})" if rp.get("album") else ""))
    if info.get("rt"):
        L.append("Radiotext: " + info["rt"])
    if info.get("ct"):
        o = info["ct"]["local_offset_min"]
        L.append(f"Uhrzeit des Senders: {info['ct']['local']} (UTC{'+' if o >= 0 else ''}{o / 60:g})")
    if info.get("af"):
        L.append("Alternativfrequenzen: " + ", ".join(f"{f:.1f}" for f in info["af"]) + " MHz")
    extra = [f"ECC {info['ecc']}" if info.get("ecc") else None,
             ("Datendienste: " + ", ".join(info["oda"])) if info.get("oda") else None]
    if any(extra):
        L.append(" · ".join(x for x in extra if x))
    return "\n".join(L)


def summary(info):
    """Kurztext für Trefferkarten und Listen."""
    if not info or not info.get("groups"):
        return ""
    parts = [f"„{info['ps'].strip()}“" if info.get("ps") else None,
             f"PI {info['pi']}" if info.get("pi") else None,
             info.get("pty_name") if info.get("pty") else None]
    rp = info.get("rtplus") or {}
    if rp.get("artist") or rp.get("title"):
        parts.append(" – ".join(x for x in (rp.get("artist"), rp.get("title")) if x))
    elif info.get("rt"):
        parts.append(info["rt"])
    return " · ".join(p for p in parts if p)
