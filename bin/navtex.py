#!/usr/bin/env python3
"""NAVTEX / SITOR-B dekodieren: Seewetter, nautische Warnungen (518 kHz englisch, 490 kHz Landessprache,
4209,5 kHz Kurzwelle). 100 Baud FSK, 170 Hz Ablage, Zeichensatz CCIR 476 (7 Bit, je Zeichen genau 4 Einsen),
Fehlerschutz durch Wiederholung: jedes Zeichen kommt zweimal, die Wiederholung 5 Zeichenplätze später
(Sender: „DX“ und „RX“ abwechselnd).

Ablauf: Tonpaar im Spektrum suchen (wie RTTY) -> Frequenz-Diskriminator -> Bittakt (100 Bd) aus der
Lage mit dem saubersten Signal -> Zeichenrahmen aus der Eigenschaft „4 Einsen“ (Lage 0…6, Bitreihenfolge und
Polarität werden ausprobiert, das Beste gewinnt) -> Zeitversatz der beiden Kopien (gerade/ungerade Plätze) ->
je Zeichen die gültige Kopie -> Buchstaben/Ziffern-Umschaltung -> Text.

Nachrichten beginnen mit „ZCZC B1B2B3B4“ (B1 Sender, B2 Art, B3B4 laufende Nummer) und enden mit „NNNN“.
Zeichentabelle nach ITU-R M.476 (CCIR 476), siehe en.wikipedia.org/wiki/CCIR_476.
"""
import numpy as np

# CCIR 476: Code (MSB zuerst, 1 = Mark) -> (Buchstabe, Ziffer). Steuerzeichen als Namen.
_TAB = {
    0x0F: ("SIA", "SIA"),
    0x17: ("J", "\a"), 0x1B: ("F", "!"), 0x1D: ("C", ":"), 0x1E: ("K", "("),
    0x27: ("W", "2"), 0x2B: ("Y", "6"), 0x2D: ("P", "0"), 0x2E: ("Q", "1"),
    0x33: ("SIB", "SIB"), 0x35: ("G", "&"), 0x36: ("FIGS", "FIGS"), 0x39: ("M", "."), 0x3A: ("X", "/"), 0x3C: ("V", "="),
    0x47: ("A", "-"), 0x4B: ("S", "'"), 0x4D: ("I", "8"), 0x4E: ("U", "7"),
    0x53: ("D", "ENQ"), 0x55: ("R", "4"), 0x56: ("E", "3"), 0x59: ("N", ","), 0x5A: ("LTRS", "LTRS"), 0x5C: (" ", " "),
    0x63: ("Z", "+"), 0x65: ("L", ")"), 0x66: ("RPT", "RPT"), 0x69: ("H", "£"), 0x6A: ("BLK", "BLK"), 0x6C: ("\n", "\n"),
    0x71: ("O", "9"), 0x72: ("B", "?"), 0x74: ("T", "5"), 0x78: ("\r", "\r"),
}
VALID = set(_TAB)
SUBJECT = {"A": "Navigational warning", "B": "Meteorological warning", "C": "Ice report", "D": "Search and rescue",
           "E": "Weather forecast", "F": "Pilot service", "G": "AIS", "H": "LORAN", "J": "SATNAV", "K": "Other nav aids",
           "L": "Navigational warning (add.)", "T": "Test", "V": "Special", "W": "Special", "X": "Special", "Y": "Special",
           "Z": "No messages"}
SUBJECT_DE = {"A": "Nautische Warnung", "B": "Wetterwarnung", "C": "Eisbericht", "D": "Seenotrettung",
              "E": "Wettervorhersage", "F": "Lotsendienst", "G": "AIS", "H": "LORAN", "J": "Satellitennavigation",
              "K": "andere Navigationshilfen", "L": "Nautische Warnung (Fortsetzung)", "T": "Test", "Z": "keine Nachrichten"}


def encode(text):
    """Text -> Folge von CCIR-476-Codes mit Umschaltungen (für Tests und Probesignale)."""
    rev_l = {v[0]: k for k, v in _TAB.items() if len(v[0]) == 1}
    rev_f = {v[1]: k for k, v in _TAB.items() if len(v[1]) == 1}
    out, figs = [0x5A], False
    for ch in text.upper():
        if ch in rev_l and (not figs or ch in " \n\r") :
            out.append(rev_l[ch])
        elif ch in rev_l:
            out += [0x5A, rev_l[ch]]
            figs = False
        elif ch in rev_f:
            if not figs:
                out.append(0x36)
                figs = True
            out.append(rev_f[ch])
    return out


def fec_stream(codes, phasing=8):
    """SITOR-B: jedes Zeichen zweimal, Wiederholung 5 Plätze später (wie der Sender, für Tests)."""
    pre = [0x33] * 3                                            # SIB als Füllung vorne
    seq = [0x0F] * phasing + codes + [0x33] * 3
    out = []
    buf = pre[:]
    for c in seq:
        out.append(buf[0])                                      # Wiederholung (RX-Platz)
        buf = buf[1:] + [c]
        out.append(c)                                           # Erstsendung (DX-Platz)
    return out


def bits_of(codes):
    return np.array([(c >> (6 - i)) & 1 for c in codes for i in range(7)], dtype=np.int8)


# ---------------------------------------------------------------- Demodulation
def _discriminator(z, rate):
    from scipy.signal import butter, sosfiltfilt
    fi = np.angle(z[1:] * np.conj(z[:-1])) * rate / (2 * np.pi)
    sos = butter(4, 120, fs=rate, output="sos")
    return sosfiltfilt(sos, fi)


def demod_bits(z, rate, f1, f2, baud=100.0):
    """Bits (1 = oberer Ton) im 100-Bd-Takt; die Abtastlage mit dem klarsten Signal gewinnt."""
    fi = _discriminator(z, rate) - (f1 + f2) / 2
    sps = rate / baud
    best = None
    for ph in np.linspace(0, sps, 16, endpoint=False):
        idx = (np.arange(ph + sps / 2, len(fi), sps)).astype(int)
        v = fi[idx]
        q = float(np.mean(np.abs(v)))
        if best is None or q > best[0]:
            best = (q, v)
    return (best[1] > 0).astype(np.int8)


def _chars(bits, off, msb_first):
    n = (len(bits) - off) // 7
    if n <= 0:
        return np.zeros(0, dtype=int)
    b = bits[off:off + n * 7].reshape(n, 7)
    w = (1 << np.arange(6, -1, -1)) if msb_first else (1 << np.arange(7))
    return (b * w).sum(axis=1)


def sync(bits):
    """Zeichenrahmen, Bitreihenfolge und Polarität finden: das Muster mit den meisten gültigen Zeichen."""
    best = None
    for pol in (0, 1):
        bb = bits if pol == 0 else 1 - bits
        for msb in (True, False):
            for off in range(7):
                c = _chars(bb, off, msb)
                if not len(c):
                    continue
                # Anzahl gültiger Zeichen (nicht der Anteil: lange Pausen mit Rauschen drücken den Anteil, ohne dass
                # der Rahmen falsch wäre)
                score = int(sum(1 for x in c if x in VALID))
                if best is None or score > best[0]:
                    best = (score, c)
    return best if best else (0, np.zeros(0, dtype=int))


def defec(codes):
    """Zwei Kopien je Zeichen: welche Plätze die Erstsendung tragen, entscheidet die Übereinstimmung mit dem Platz
    5 weiter; je Zeichen die gültige Kopie (Erstsendung bevorzugt)."""
    best = None
    for par in (0, 1):
        agree = sum(1 for k in range(par, len(codes) - 5, 2) if codes[k] == codes[k + 5] and codes[k] in VALID)
        if best is None or agree > best[0]:
            best = (agree, par)
    par = best[1]
    out = []
    for k in range(par, len(codes), 2):                         # Erstsendung; die Kopie liegt 5 Plätze weiter
        a = codes[k]
        r = codes[k + 5] if k + 5 < len(codes) else None
        if a in VALID:
            out.append(a)
        elif r in VALID:
            out.append(r)
        else:
            out.append(None)
    return out


def to_text(codes):
    figs, out = False, []
    for c in codes:
        if c is None:
            out.append("_")
            continue
        l, f = _TAB[c]
        if l == "LTRS":
            figs = False
        elif l == "FIGS":
            figs = True
        elif l in ("SIA", "SIB", "RPT", "BLK"):
            continue
        else:
            ch = f if figs else l
            if ch in ("\r", "\a", "ENQ"):
                continue
            out.append(ch)
    import re
    txt = re.sub(r"_{3,}", "…", "".join(out)).strip("…")      # Strecken ohne Signal
    while "\n\n\n" in txt:
        txt = txt.replace("\n\n\n", "\n\n")
    return txt


def decode(z, rate, f1=None, f2=None):
    """Komplexes Basisband (Töne irgendwo um 0 Hz) -> {"text", "quality", "messages": [...]} oder None."""
    if f1 is None:
        # Tonpaar im stärksten 4-s-Abschnitt suchen: sendet der Sender nur einen Teil des Fensters, ginge es im
        # Spektrum des ganzen Fensters im Rauschen unter
        try:
            import decode_recordings as DR
            seg = int(rate * 4)
            best = None
            for k in range(0, max(1, len(z) - seg + 1), seg // 2):
                p = DR.find_rtty_pair(z[k:k + seg], rate)
                if p and 145 <= p[3] <= 205 and (best is None or p[0] > best[0]):
                    best = p
            pair = best
        except Exception:
            pair = None
        if not pair:
            return None
        _, f1, f2, _ = pair
    bits = demod_bits(z, rate, min(f1, f2), max(f1, f2))
    n_ok, codes = sync(bits)
    if n_ok < 25 or len(codes) < 20:
        return None
    dec = defec(list(codes))
    good = np.array([c is not None for c in dec], dtype=float)
    if len(good) < 12:
        return None
    # Rauschen ergibt zufällig auch gültige Zeichen (35 von 128): nur Strecken behalten, in denen fast alles
    # lesbar ist (gleitend über 12 Zeichen), der Rest gilt als „kein Signal“
    local = np.convolve(good, np.ones(12) / 12, mode="same")
    dec = [c if local[k] >= 0.75 else None for k, c in enumerate(dec)]
    q = float(local.max())
    if q < 0.85:
        return None
    text = to_text(dec)
    return {"text": text.strip(), "quality": round(q * 100), "messages": messages(text)}


def messages(text):
    """Vollständige NAVTEX-Nachrichten: ZCZC B1B2B3B4 … NNNN."""
    out = []
    pos = 0
    while True:
        a = text.find("ZCZC", pos)
        if a < 0:
            break
        b = text.find("NNNN", a)
        if b < 0:
            break
        body = text[a + 4:b].strip()
        head = body.split(None, 1)
        hdr = head[0] if head else ""
        info = {"header": hdr, "body": head[1].strip() if len(head) > 1 else ""}
        if len(hdr) == 4 and hdr[:2].isalpha():
            info.update(station=hdr[0], subject=hdr[1], number=hdr[2:], subject_name=SUBJECT.get(hdr[1], "?"),
                        subject_de=SUBJECT_DE.get(hdr[1], SUBJECT.get(hdr[1], "?")))
        out.append(info)
        pos = b + 4
    return out
