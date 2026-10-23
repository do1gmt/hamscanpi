# Testing without hardware

## Self test

```bash
pip install pysstv          # only needed for the tests (generates SSTV test images)
devtools/selftest.sh
```

The self test copies the repository to a temporary directory and checks:

1. pass prediction and scheduling with synthetic orbital elements,
2. an ISS pass on 70 cm – Robot 36 SSTV on 437.550 plus the 437.800 repeater, with real Doppler,
3. three satellites recorded together, each with its own Doppler,
4. APRS via the ISS (needs `multimon-ng`),
5. shortwave SSTV: Scottie 1 starting mid-image (no VIS header) and Martin 1, station 60 Hz off,
6. memory: SSTV analysis of an 11-minute pass with five images must stay below 400 MB (a Pi was
   killed by the out-of-memory handler before this was fixed).

## Tools

| File | Purpose |
|---|---|
| `devtools/make_tles.py` | valid synthetic TLEs for all catalogue satellites (epoch = now) |
| `devtools/sim_pass.py` | simulated pass as uint8 I/Q at 960 kS/s: SSTV, FM tone, APRS packets, carrier – Doppler per satellite |
| `devtools/test_hf_sstv.py` | shortwave SSTV through `sstv_watch` with a fake receiver |
| `devtools/robot72.py` | Robot 72 encoder (pysstv only has Robot 36) |
| `devtools/fakebin/rtl_sdr` | stands in for `rtl_sdr`: noise in real time, honours `-n` |

Run the dashboard without hardware:

```bash
PMSDR_FAKE_HW=rtlsdr,pmsdr python3 bin/webapp.py 8090
```

`bin/sat_pass.py --sats <channels> --test FILE.u8 --start UNIXTIME` replays a recorded or simulated
pass instead of using the stick.

## Recovering an interrupted satellite pass

If the analysis of a pass is interrupted (power loss, out of memory), the raw per-channel files
`recordings/.sat_<channel>_<pid>.raw` stay behind. `python3 bin/sat_recover.py --list` shows them,
`python3 bin/sat_recover.py` analyses them like a normal pass (SSTV, APRS, voice) and deletes them.
