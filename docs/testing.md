# Testing without hardware

## Self test

```bash
devtools/selftest.sh
```

The self test copies the repository to a temporary directory (your installation is not touched), runs about
40 sections against simulated signals and fake receivers and prints `ok` or `FEHLER` per section:

| Area | Sections |
|---|---|
| Satellites | pass prediction and scheduling (1), ISS 70 cm SSTV + repeater with Doppler (2), three satellites in one recording (3), long pass memory (6), Meteor LRPT test satellite (13), directional antenna beam (16), ISS APRS on 437.825 (22) |
| Shortwave and decoders | shortwave SSTV incl. missed start (5), callsigns from decodes and ADIF (7), packet data, CW, RTTY (18), shortwave with the stick by direct sampling (20), live decoders: APRS stream, FT8 slot, CW, RTTY, SSTV (23, 24), memory of long HF recordings (28), NAVTEX with FEC, weather fax with phasing, DWD RTTY 450 Hz (36) |
| FM broadcast | RDS from a noisy FM signal (9), TMC with the German location table (10), RBDS and further countries (25) |
| Stations | station overview per callsign and map with all stations (31), PSK Reporter packets (8), WSJT-X UDP output with outage and catch-up (34) |
| EiBi | schedule: season, times and days, transmitter sites, upload, offline (35); EiBi chip in the scan with carrier measurement (38) |
| Scan and receiver sharing | schedules (8), update waits for a free window (14), stick hangs / short glitches / FT8 false alarm (15), mixed operation: releasing the stick, hanging scan, arming delay (30), run exit codes (33), "new in last run" and the last-run filter (37), scan request blocked by a lock (40) |
| System | update by git: publishing and `bin/update.sh` (45), system values and history (11), SDR sticks by serial number (12), backup and restore (17), notices and notification rules (19), band plan per country (21), offline notifications (26), unattended watch (27), watchdog, rescue page, diagnostics (29), antenna profiles (32), new network / IP address (39) |

Some sections need `multimon-ng` and `ft8_lib` (both set up by `install.sh`). `pysstv` (MIT licence), which generates the SSTV test signals, is bundled in `devtools/vendor/` – nothing to install.

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
