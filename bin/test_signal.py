#!/usr/bin/env python3

import os
import time
import subprocess
import numpy as np
import json
from pathlib import Path
from scipy.signal import find_peaks

CENTER = 28200000

CFG = Path(__file__).resolve().parent.parent / "config" / "scanner.json"
with open(CFG, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

PPM = float(CONFIG.get("frequency_correction_ppm", 0.0))
CORRECTED_CENTER = CENTER * (1.0 + PPM * 1e-6)
EXPECTED = CENTER + 5000
RATE = 48000
SECONDS = 2.0
FIFO = "/tmp/PMSDRcommands"

def tune(freq):
    fd = os.open(FIFO, os.O_WRONLY)
    os.write(fd, f"f {freq}\n".encode())
    os.close(fd)

def capture():
    p = subprocess.Popen(
        [
            "arecord","-q",
            "-D","plughw:1,0",
            "-f","S16_LE",
            "-r",str(RATE),
            "-c","2",
            "-t","raw"
        ],
        stdout=subprocess.PIPE
    )

    time.sleep(SECONDS)
    p.terminate()
    raw,_ = p.communicate()

    x = np.frombuffer(raw,dtype="<i2").reshape(-1,2)

    i=x[:,0].astype(float)
    q=x[:,1].astype(float)

    i-=i.mean()
    q-=q.mean()

    return i-1j*q

def analyse(z):
    n=8192
    w=np.hanning(n)

    spectra=[]

    for start in range(0,len(z)-n+1,n//2):
        f=np.fft.fftshift(
            np.fft.fft(z[start:start+n]*w)
        )
        spectra.append(np.abs(f))

    s=np.median(spectra,axis=0)

    db=20*np.log10(np.maximum(s,1e-12))

    freq=np.fft.fftshift(
        np.fft.fftfreq(n,1/RATE)
    )

    valid=(np.abs(freq)>300)&(np.abs(freq)<20000)

    noise=np.median(db[valid])

    peaks,_=find_peaks(
        db,
        height=noise+8,
        distance=40
    )

    result=[]

    for p in peaks:
        if valid[p]:
            result.append(
                (db[p]-noise,freq[p])
            )

    result.sort(reverse=True)

    return noise,result[:15]

print("PMSDR POSITIVTEST")
print("-----------------")
print(f"Center: {CENTER/1e6:.6f} MHz")
print(f"Erwartet: {EXPECTED/1e6:.6f} MHz")

tune(CENTER)
time.sleep(.4)

for run in (1,2):

    z=capture()
    noise,peaks=analyse(z)

    print()
    print(f"Messung {run}")
    print(f"Noise: {noise:.1f} dB")

    for snr,off in peaks:
        print(
            f"{CORRECTED_CENTER+off:12.0f} Hz   "
            f"Offset {off:+8.1f} Hz   "
            f"SNR {snr:5.1f} dB"
        )

    time.sleep(.5)
