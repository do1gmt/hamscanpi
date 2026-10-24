# Changelog

## 1.0.0 – 2026-10-24

First public release.

**Scanning**
- Unattended band scanning with a PMSDR (shortwave) or an RTL-SDR stick (VHF/UHF); the selected
  receiver stands for the connected antenna.
- RTL-SDR receive windows cover every channel of the selected segments; no channel sits on the
  DC spike in the window centre.
- Channel watch for FM/repeaters/PMR/APRS/pager, snapshot peak search with confirmation, SSB voice
  watch on shortwave, FT8/FT4 and shortwave SSTV with image decoding.
- Adjustable scan raster, scan interval from 2 min with a duration estimate, restricted bands
  (e.g. airband) switched off by default.

**Satellites**
- Pass prediction from CelesTrak/AMSAT orbital elements, Doppler correction per channel.
- ISS SSTV (437.550/145.800), ISS repeater, ISS APRS, FM and linear satellites, SSTV CubeSats.
- Scoring for overlapping passes (priority, elevation, waiting time, activity), up to three
  satellites recorded together, `bin/sat_recover.py` for interrupted passes.
- SSTV decoder (Robot 36/72, Martin, Scottie, PD) incl. images without VIS header; analysis of an
  11-minute pass needs about 120 MB.

**Dashboard**
- Scan, hits, live, log and settings; English and German; mobile layout.
- Access protection (password, trusted networks), system status, automatic clean-up,
  disk-space guard, update notice.
- All times in UTC except local-time columns, which are labelled with the time-zone abbreviation.

**Tools**
- `install.sh`, `bin/update.sh`, `devtools/selftest.sh` (also run on GitHub for every change).
