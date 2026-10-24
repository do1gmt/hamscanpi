#!/usr/bin/env python3
import os
import re
import stat
import subprocess
import time
import numpy as np

FIFO="/tmp/PMSDRcommands"
RATE=48000
SECONDS=2.0
CENTER=3_700_500
TX=3_705_370
TARGET=TX-CENTER

def find_alsa(name="Aureon", device=0):
    r=subprocess.run(["env", "LC_ALL=C", "arecord", "-l"],text=True,capture_output=True)
    for line in r.stdout.splitlines():
        if name.lower() not in line.lower():
            continue
        m=re.search(r"(?:card|karte)\s+(\d+):.*(?:device|gerät)\s+(\d+):",line,re.I)
        if m and int(m.group(2))==device:
            return int(m.group(1)),int(m.group(2))
    raise SystemExit("Aureon nicht gefunden")

def open_fifo():
    if not os.path.exists(FIFO):
        raise SystemExit(f"FIFO fehlt: {FIFO}")
    if not stat.S_ISFIFO(os.stat(FIFO).st_mode):
        raise SystemExit(f"Kein FIFO: {FIFO}")
    return os.open(FIFO,os.O_WRONLY|os.O_NONBLOCK)

def tune(fd,hz):
    os.write(fd,f"f {int(hz)}\n".encode())

def capture(card,dev):
    p=subprocess.Popen(
        ["arecord","-q","-D",f"plughw:{card},{dev}",
         "-f","S16_LE","-r",str(RATE),"-c","2","-t","raw"],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE
    )
    time.sleep(SECONDS)
    p.terminate()
    raw,_=p.communicate(timeout=2)
    x=np.frombuffer(raw,dtype="<i2").astype(np.float64)
    x=x[:len(x)//2*2].reshape(-1,2)/32768.0
    i=x[:,0]-np.mean(x[:,0])
    q=x[:,1]-np.mean(x[:,1])
    return i,q

def spectrum(i,q):
    z=i-1j*q
    n=len(z)
    w=np.hanning(n)
    s=np.fft.fftshift(np.fft.fft(z*w))
    f=np.fft.fftshift(np.fft.fftfreq(n,1/RATE))
    p=np.abs(s)**2
    return f,p

def level_near(f,p,target,width=80):
    m=np.abs(f-target)<=width
    return float(np.max(p[m]))

def db(x):
    return 10*np.log10(max(x,1e-30))

card,dev=find_alsa()
fd=open_fifo()

try:
    tune(fd,CENTER)
    time.sleep(0.6)

    print()
    print("PTT-Differenztest")
    print("-----------------")
    print(f"PMSDR Mitte : {CENTER/1e6:.6f} MHz")
    print(f"IC-705      : {TX/1e6:.6f} MHz FM")
    print(f"Soll-Offset : {TARGET:+.0f} Hz")
    print()
    print("1) PTT NICHT drücken.")
    input("ENTER drücken für Aufnahme ohne Sender ... ")
    i0,q0=capture(card,dev)
    f0,p0=spectrum(i0,q0)

    print()
    print("2) Jetzt PTT gedrückt halten.")
    input("ENTER drücken und PTT während der Aufnahme halten ... ")
    i1,q1=capture(card,dev)
    f1,p1=spectrum(i1,q1)
    print("PTT loslassen.")
    print()

    for sign,label in [(+1,"+Offset"),(-1,"-Offset")]:
        target=sign*TARGET
        off=level_near(f0,p0,target)
        on=level_near(f1,p1,target)
        gain=db(on/off)
        print(f"{label:7s} {target:+.0f} Hz")
        print(f"  ohne PTT : {db(off):7.1f} dB")
        print(f"  mit PTT  : {db(on):7.1f} dB")
        print(f"  Änderung : {gain:+7.1f} dB")
        print()

    # stärkste durch PTT neu hinzugekommene Linien
    ratio=(p1+1e-30)/(p0+1e-30)
    mask=(np.abs(f1)>300)&(np.abs(f1)<18000)
    idx=np.where(mask)[0]
    order=idx[np.argsort(ratio[idx])[::-1]]

    print("Stärkste PTT-bedingte Änderungen:")
    shown=[]
    for k in order:
        freq=float(f1[k])
        if any(abs(freq-x)<100 for x in shown):
            continue
        shown.append(freq)
        print(f"  {freq:+8.1f} Hz  {db(ratio[k]):+7.1f} dB")
        if len(shown)>=8:
            break

finally:
    os.close(fd)
