# Changelog

## 1.2.1 – 2026-10-24

- Satellite recordings can be stopped from the dashboard (button "Stop recording"): the stick is
  released at once, what has been recorded so far is still analysed; the pass list shows "aborted".

## 1.2.0 – 2026-10-24

- **Push to your phone** via ntfy or Telegram, in addition to or instead of mail – for errors, scans
  and watchlist alerts; SSTV images are attached.
- **Band schedules:** other bands at certain times of day and weekdays (e.g. 80/40 m at night),
  incl. satellites; the active schedule is shown on the scan page. Times in the Pi's local time.
- **Map** of heard stations with a locator (OpenStreetMap, loaded on demand).
- **Activity overview:** hits per tuning by band and hour (UTC) and by weekday and hour.
- **PSK Reporter:** heard FT8/FT4 stations are reported every 5 minutes (opt-in, IPFIX over UDP).
- **Waterfall** in live listening (RTL-SDR ±100 kHz, PMSDR whole window); tap to tune.
- FT8 spots keep their exact frequency (dial + audio offset).
- **Watchlist:** get notified when the scanner hears a frequency (±tolerance), a callsign (wildcards
  like `DL*`), a station farther than N km, or an event (SSTV from the ISS, any SSTV image, APRS via
  the ISS, any satellite). Shown in the log, as 🔔 on the hit card and by mail (quiet time per rule).
- **Whole QSOs:** on FM/repeater/AM/digital-voice channels the scanner stays on a transmission until
  it ends (4 s of silence, at most 120 s, `qso_hang_seconds` / `qso_max_seconds`) and records it
  without gaps.
- **Heard stations:** new tab with all transmitting callsigns from FT8/FT4, APRS, CW and RTTY –
  locator, distance and bearing, bands, modes, last heard; links to QRZ.com.
- **ADIF export** of the heard stations as an SWL log; own callsign under *Settings → Station*.
- Mobile: the settings tab is a ⚙ icon so six tabs fit.

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
