"""Robot-72-Kodierer für Tests (pysstv kennt nur Robot 36)."""
from itertools import chain
from pysstv.color import ColorSSTV
from pysstv.sstv import byte_to_freq, FREQ_BLACK, FREQ_WHITE, FREQ_VIS_START
class Robot72(ColorSSTV):
    VIS_CODE = 0x0C; WIDTH = 320; HEIGHT = 240; SYNC = 9
    def on_init(self): self.yuv = self.image.convert('YCbCr').load()
    def encode_line(self, line):
        px = [self.yuv[c, line] for c in range(self.WIDTH)]
        yt, ct = 138 / self.WIDTH, 69 / self.WIDTH
        return chain([(FREQ_BLACK, 3)], ((byte_to_freq(p[0]), yt) for p in px),
                     [(FREQ_BLACK, 4.5), (FREQ_VIS_START, 1.5)], ((byte_to_freq(p[2]), ct) for p in px),
                     [(FREQ_WHITE, 4.5), (FREQ_VIS_START, 1.5)], ((byte_to_freq(p[1]), ct) for p in px))
