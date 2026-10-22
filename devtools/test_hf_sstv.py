"""Kurzwellen-SSTV-Test ohne Hardware: Scottie 1 ab Bildmitte (ohne Kopf), dann Martin 1 mit Kopf,
   SSB (USB oder LSB), Gegenstation verstimmt, Rauschen. Prüft sstv_watch.watch_sstv."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'bin')); sys.path.insert(0, str(ROOT / 'devtools'))
import sys, numpy as np
from scipy.signal import hilbert
from PIL import Image, ImageDraw
from pysstv.color import ScottieS1, MartinM1
side = sys.argv[1] if len(sys.argv) > 1 else 'USB'
mist = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
snr_noise = float(sys.argv[3]) if len(sys.argv) > 3 else 0.02
R = 48000
def pic(txt, col):
    im = Image.new('RGB', (320, 256), col); d = ImageDraw.Draw(im)
    for k in range(8): d.rectangle([k*40, 0, k*40+39, 100], fill=[(255,0,0),(0,255,0),(0,0,255),(255,255,0),(0,255,255),(255,0,255),(255,255,255),(0,0,0)][k])
    d.ellipse([110, 120, 210, 220], fill=(250, 200, 30)); d.text((20, 230), txt, fill=(255,255,255)); return im
a1 = np.array(list(ScottieS1(pic('SCOTTIE', (90,20,20)), R, 16).gen_samples()), np.float32) / 32767
a2 = np.array(list(MartinM1(pic('MARTIN', (20,60,20)), R, 16).gen_samples()), np.float32) / 32767
audio = np.concatenate([a1[int(45*R):], np.zeros(int(3*R), np.float32), a2])
total = int(124 * R)
audio = np.concatenate([audio, np.zeros(max(0, total - len(audio)), np.float32)])[:total]
shift = -12000.0
t = np.arange(total) / R
an = hilbert(audio)
if side == 'LSB': an = np.conj(an)
z = 0.3 * an * np.exp(2j*np.pi*((shift + (mist if side=='USB' else -mist)) * t))
rng = np.random.default_rng(3)
z += snr_noise * (rng.standard_normal(total) + 1j*rng.standard_normal(total))
class Dev:
    rate = R; pos = 0
    def tune(self, c): pass
    def capture(self, s):
        n = int(s * R); a = z[self.pos:self.pos+n]; self.pos += n
        return a.real.copy(), a.imag.copy()
import sstv_watch as W
dial = 14230000 if side == 'USB' else 3730000
hits = W.watch_sstv(Dev(), {'name': '20m' if side=='USB' else '80m'}, {'id': 'sstv', 'frequencies_hz': [dial], 'sideband': side}, {'iq_conjugate': False})
print(len(hits), [h['note'] for h in hits])
for h in hits: print(h['recording_path'])
pic('SCOTTIE', (90,20,20)).save(str(Path('/tmp')/'src_scottie.png')); pic('MARTIN', (20,60,20)).save('/tmp/src_martin.png')
