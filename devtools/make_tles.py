"""Synthetische, gültige TLEs (Epoche jetzt) für Tests ohne Netz."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'bin')); sys.path.insert(0, str(ROOT / 'devtools'))
import json, sys, time, random
from datetime import datetime, timezone
def cks(l):
    return str(sum(int(c) if c.isdigit() else (1 if c=='-' else 0) for c in l[:68]) % 10)
def tle(norad, inc, raan, ecc, argp, ma, n, t=None):
    t = t or time.time()
    d = datetime.fromtimestamp(t, timezone.utc)
    doy = (t - datetime(d.year,1,1,tzinfo=timezone.utc).timestamp())/86400 + 1
    l1 = f"1 {norad:05d}U 98067A   {d.year%100:02d}{doy:012.8f}  .00010000  00000-0  18000-3 0  999"
    l1 = l1[:68]; l1 += cks(l1)
    l2 = f"2 {norad:05d} {inc:8.4f} {raan:8.4f} {int(ecc*1e7):07d} {argp:8.4f} {ma:8.4f} {n:11.8f}    1"
    l2 = l2[:68].ljust(68); l2 += cks(l2)
    return [l1, l2]
EL = {25544:(51.64,120,0.0005,30,10,15.50),7530:(101.98,80,0.0012,200,50,12.536),44881:(97.4,30,0.001,90,100,15.2),
      39444:(97.6,300,0.006,100,200,14.82),24278:(98.6,150,0.035,250,300,13.53),44909:(82.5,50,0.02,20,20,12.80),
      60209:(97.4,200,0.001,10,250,15.1),27607:(64.55,10,0.008,300,60,14.80),43678:(97.5,250,0.001,0,120,15.25),
      43017:(97.7,100,0.02,150,170,14.79),61781:(97.29,98.7,0.0015,85,274,15.36),40908:(97.3,330,0.0013,40,310,15.25),
      57172:(97.6,200,0.001,40,50,15.10),57189:(97.6,200.2,0.001,40,47,15.10),57203:(97.6,199.8,0.001,40,44,15.10),
      67284:(97.39,250,0.001,350,10.4,15.229),67291:(97.39,250,0.0008,344,13.0,15.229),-1:(97.39,250,0.001,340,16.5,15.229),-2:(97.39,250,0.001,345,7.0,15.229),59112:(97.5,187,0.0008,301,58,15.49)}
out = {"fetched": time.time(), "tried": time.time(), "source": "Test", "errors": {}, "not_found": [], "tle": {}}
for n,(i,r,e,a,m,mm) in EL.items():
    l1,l2 = tle(abs(n) if n>0 else 99000+abs(n),i,r,e,a,m,mm)
    out["tle"][str(n)] = [str(n), l1, l2]
import satellites as S
for k,v in out["tle"].items():
    assert S._checksum_ok(v[1]) and S._checksum_ok(v[2]), k
    assert S.parse_tle_text("\n".join(v)), k
dest = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / 'data' / 'tle.json')
Path(dest).parent.mkdir(parents=True, exist_ok=True)
json.dump(out, open(dest, 'w'), indent=1)
print('ok', len(out['tle']))
