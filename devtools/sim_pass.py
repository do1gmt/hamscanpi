"""Simulierten Überflug als uint8-I/Q (960 kS/s) erzeugen.
   python3 sim_pass.py OUT.u8 START DAUER_S TUNE_HZ  kanal:art[:pegel] ...
   art: sstv (Robot 36), tone (FM-Ton, Sprachersatz, nur 20-50 s), aprs (APRS-Pakete), carrier"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'bin')); sys.path.insert(0, str(ROOT / 'devtools'))
import sys, numpy as np
import satellites as S, json
from pathlib import Path
out, start, dur, tune = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
specs = sys.argv[5:]
FS = 960000
tles, _ = S.load_tles(fetch=False)
LL = S.locator_to_latlon('JO62QM')
trs = {}
def trk(n):
    if n not in trs: trs[n] = S.Tracker(tles[n], *LL)
    return trs[n]
A_RATE = 48000

def robot36_audio():
    from pysstv.color import Robot36
    from PIL import Image, ImageDraw
    im = Image.new('RGB', (320, 240), (20, 40, 90)); d = ImageDraw.Draw(im)
    for i in range(8): d.rectangle([i*40, 0, i*40+39, 120], fill=[(255,0,0),(0,255,0),(0,0,255),(255,255,0),(0,255,255),(255,0,255),(255,255,255),(0,0,0)][i])
    d.ellipse([110, 130, 210, 230], fill=(250, 200, 30)); d.text((20, 200), 'PMSDR TEST', fill=(255,255,255))
    im.save('/tmp/sstv_src.png')
    s = Robot36(im, A_RATE, 16)
    return np.array(list(s.gen_samples()), dtype=np.float32) / 32767

def ax25_afsk(packets, rate=A_RATE):
    def crc(data):
        c = 0xFFFF
        for b in data:
            c ^= b
            for _ in range(8): c = (c >> 1) ^ 0x8408 if c & 1 else c >> 1
        return c ^ 0xFFFF
    def addr(call, ssid, last):
        call = call.ljust(6)[:6]
        return bytes(ord(ch) << 1 for ch in call) + bytes([0x60 | (ssid << 1) | (1 if last else 0)])
    sig = []
    for (src, dst, info) in packets:
        frame = addr(dst, 0, False) + addr(src, 0, False) + addr('ARISS', 0, True) + b'\x03\xf0' + info.encode()
        c = crc(frame); frame += bytes([c & 0xFF, c >> 8])
        bits = []
        ones = 0
        for byte in frame:
            for i in range(8):
                b = (byte >> i) & 1; bits.append(b)
                ones = ones + 1 if b else 0
                if ones == 5: bits.append(0); ones = 0
        flag = [0,1,1,1,1,1,1,0]
        allbits = flag * 30 + bits + flag * 3
        # NRZI: 0 = Wechsel
        tone, tones = 0, []
        for b in allbits:
            if b == 0: tone ^= 1
            tones.append(tone)
        spb = rate / 1200; ph = 0; out = []
        for k, t in enumerate(tones):
            n = int(round((k + 1) * spb)) - int(round(k * spb))
            f = 1200 if t == 0 else 2200
            for _ in range(n): ph += 2 * np.pi * f / rate; out.append(np.sin(ph))
        sig.append(np.array(out, np.float32) * 0.8)
        sig.append(np.zeros(int(rate * 4), np.float32))
    return np.concatenate(sig)

n_tot = int(dur * FS)
blk = FS // 10
mods = []
for sp in specs:
    f, kind, *rest = sp.split(':'); f = float(f); lv = float(rest[0]) if rest else 0.3; nor = int(rest[1]) if len(rest) > 1 else 25544
    if kind == 'sstv': a = np.concatenate([np.zeros(A_RATE*3, np.float32), robot36_audio()])
    elif kind == 'tone': a = np.concatenate([np.zeros(A_RATE*20, np.float32), 0.6*np.sin(2*np.pi*800*np.arange(A_RATE*30)/A_RATE).astype(np.float32)])
    elif kind == 'aprs': a = np.concatenate([np.zeros(A_RATE*5, np.float32), ax25_afsk([('N0CALL','APRS','!5030.00N/01135.00E-Test 1'),('DL2ABC','CQ','>Hallo ISS'),('OK1XYZ','APRS',':N0CALL   :73 de OK1XYZ')])])
    else: a = np.zeros(1, np.float32)
    mods.append((f, kind, lv, a, nor))
rng = np.random.default_rng(1)
ph = [0.0] * len(mods)
with open(out, 'wb') as fo:
    for b0 in range(0, n_tot, blk):
        t = start + b0 / FS
        tt = np.arange(blk) / FS
        z = (rng.standard_normal(blk) + 1j * rng.standard_normal(blk)) * 0.05
        for i, (f, kind, lv, a, nor) in enumerate(mods):
            dop = trk(nor).doppler_hz(f, t + 0.05) if nor else 0.0          # NORAD 0: Sender auf der Erde
            # Audio auf 960k hochrechnen (Halten genügt nicht: linear interpolieren)
            ia = (b0 + np.arange(blk)) * A_RATE / FS
            au = np.interp(ia, np.arange(len(a)), a, right=0.0) if kind != 'carrier' else np.zeros(blk)
            inst = (f - tune + dop) + 5000 * au           # FM-Hub 5 kHz
            phase = ph[i] + 2 * np.pi * np.cumsum(inst) / FS
            ph[i] = float(phase[-1] % (2*np.pi))
            z += lv * np.exp(1j * phase)
        u = np.clip(z.real * 127.5 + 127.5, 0, 255).astype(np.uint8), np.clip(z.imag * 127.5 + 127.5, 0, 255).astype(np.uint8)
        inter = np.empty(blk * 2, np.uint8); inter[0::2] = u[0]; inter[1::2] = u[1]
        fo.write(inter.tobytes())
print('ok', out)
