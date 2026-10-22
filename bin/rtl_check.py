#!/usr/bin/env python3
"""Schnelltest fuer den RTL-SDR-Stick: Erkennung, Abtastung, Rauschpegel, Testempfang UKW."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from devices import RtlDevice, rtl_holders, usb_reset_rtl

cfg = json.loads((Path(__file__).resolve().parent.parent / "config" / "scanner.json").read_text(encoding="utf-8"))
print("RTL-SDR Check")
print("-------------")
if shutil.which("rtl_test") is None:
    print("✗ rtl-sdr nicht installiert:  sudo apt install rtl-sdr")
    sys.exit(1)
h = rtl_holders()
if h:
    print("! Stick evtl. belegt durch:", ", ".join(h))
if "--reset" in sys.argv:
    ok, how = usb_reset_rtl(0)
    print(("✓ USB-Reset: " if ok else "✗ USB-Reset: ") + how)
    import time; time.sleep(2)
r = subprocess.run(["rtl_test", "-t"], capture_output=True, text=True, timeout=20)
out = (r.stdout + r.stderr)
print(out.strip().splitlines()[0] if out.strip() else "(keine Ausgabe)")
if "No supported devices" in out or "usb_open" in out:
    print("✗ Kein Stick gefunden. Steckt er? Ist der DVB-Kerneltreiber geblockt?")
    print("  Abhilfe: echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/rtl-sdr.conf && sudo reboot")
    sys.exit(1)
print("✓ Stick erkannt")
dev = RtlDevice({"sample_rate": 1024000, "rtlsdr": cfg.get("rtlsdr", {})})
for mhz in (100.0, 145.5, 433.5):
    dev.tune(int(mhz * 1e6))
    try:
        i, q = dev.capture(0.5)
    except Exception as ex:
        print(f"✗ {mhz:7.1f} MHz: {ex}")
        sys.exit(1)
    z = i + 1j * q
    P = np.abs(np.fft.fftshift(np.fft.fft(z[:32768] * np.hanning(32768)))) ** 2
    pdb = 10 * np.log10(P + 1e-20)
    noise = np.median(pdb)
    print(f"✓ {mhz:7.1f} MHz: {len(z)} Samples, Rauschen {noise:.0f} dB, stärkste Linie {pdb.max()-noise:.0f} dB über Rauschen, Übersteuerung {'JA – Gain senken' if np.max(np.abs(i)) > 0.95 else 'nein'}")
print("Fertig. Gain/ppm in config/scanner.json unter \"rtlsdr\" anpassen, falls nötig.")
