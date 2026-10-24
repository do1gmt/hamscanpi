# Changelog

## 1.4.2 – 2026-10-24

- Fix: live seek no longer runs on after finding a station. A gain step of the automatic gain
  restarted the whole reception, and the seek then continued from the station it had just found;
  now only `rtl_sdr` is restarted with the new gain, reception, seek hold and RDS continue.
- FM station list keeps the last complete radio text instead of overwriting it with fragments.
- README: screenshot of the *Stations* tab.
- A scan stopped by the user (or for a satellite pass) is no longer reported as an error: status
  "Scan cancelled" instead of "Scan error", grey in the scan history, no error badge on the log tab,
  no "failed" mail, not counted as an error in the 24-h tile.

## 1.4.1 – 2026-10-24

- **Own automatic gain** instead of the stick's AGC, which overloads with strong local FM stations
  (seen: 53 % of the samples clipped next to a transmitter site). Live: fixed gain steps, two steps
  down on clipping, one step up when the signal is very weak; RDS is kept while it adjusts. FM scan:
  per receive window, re-recorded at once with less gain when clipping, remembered for the next scan
  (`data/fm_gain.json`).
- English UI on FM hit cards: the RDS block and the header summary are now built from the decoded
  data (programme type, radio text, AF, identification, reception, TMC) instead of German text.
- Live: "Recently heard" only after 5 s on the same frequency (no entries for every step);
  ◀/▶ use 100-kHz steps with "FM wide".

## 1.4.0 – 2026-10-24

- **TMC traffic messages** from RDS (group 8A, single- and multi-group messages with quantity,
  length, speed, extra events, supplementary info, duration, diversion): plain text with road,
  direction, "between junction X and Y" and event, using the German *Location Code List* LCL 22.0 and
  the *Event Code List* ECL 4.01 of BASt (CC BY 4.0, converted to `share/tmc/`). Encrypted TMC is
  recognised. Shown live under the RDS box (German/English), on FM hit cards, and usable in the
  watchlist ("traffic message for A9" – live and from scans, with mail/push).
- README: new scan page screenshot; screenshots of the settings page and of satellite priorities with
  the system status.
- English UI: reason for interrupted scans translated.
- RDS: an incompletely received station name is shown with gaps („__ST FM“) instead of nothing;
  the one-line summary only contains complete radio text.
- Self test on GitHub: the shared-recording step no longer depends on the time of day (the pair of
  satellites recorded together changes with the orbits; the test failed with HC1PX + QMR-KWT-2).

## 1.3.2 – 2026-10-24

- **FM broadcast scan rebuilt:** stations are found by their power per 100-kHz channel instead of
  the peak search (whose confirmation rejected most FM stations), recorded exactly on the channel
  centre and with automatic gain – so RDS can be decoded for every station. Settings:
  `fm_record_seconds` (6), `fm_threshold_db` (10), `fm_auto_gain` (true).
- Narrower receive windows for the FM band, so every station fits completely into a window.

## 1.3.1 – 2026-10-24

- Live listening: gain defaults to "automatic" (much better FM/RDS reception than the scan gain);
  the choice is remembered per browser.
- Volume slider (0–150 %), remembered per browser.
- "Recently heard" also keeps stations found by seek, with their RDS name.
- RDS: radio text is shown even when only partly received; the end-of-text marker is recognised;
  RT+ artist/title only when the referenced part of the text is complete.
- English UI: RDS reception line, music/speech and signal check translated.
- README: live screenshot with a real RDS station.

## 1.3.0 – 2026-10-24

- **RDS for FM broadcast** (own decoder, no extra program): station name (PS), PI, programme type
  (PTY/PTYN), traffic (TP/TA), music/speech, radio text, RT+ (artist/title), station clock (CT),
  alternative frequencies (AF), ECC and data services (RT+, TMC, …).
- Live listening ("FM wide"): RDS box below the S-meter after 1–3 s; "Recently heard" shows the
  station name.
- Scans of the band "Broadcast": RDS of every station on the hit card (📻 badge with the name) and a
  new list *FM stations (RDS)* in the *Stations* tab.
- Fix: "Recently heard" restarted entries with the wrong demodulator; the demodulator selection
  follows the running reception.
- Stations and watchlist are analysed after the scan has finished (APRS/RDS/FT8 are decoded at the end).
- The status no longer shows "Decoding FT8" (or RDS) when the scan has nothing to decode for it.

## 1.2.2 – 2026-10-24

- Screenshots in the README.
- Satellite analysis needs far less memory: a 20-minute pass now ~170 MB instead of ~450 MB (the
  tone analysis writes into the audio buffer, the search for images without header works in blocks).
  `sat_recover.py` was killed by the system on 19–20-minute passes on a Pi with 1 GB.
- Signal check per pass: highest channel level above noise and seconds above 6 dB – in the log,
  in `sat_recover.py` and in the pass list ("no signal" when the satellite was not heard at all).
- Live listening while a scan or satellite pass is running: the dashboard offers to stop it first.
- APRS from multimon-ng (incl. third-party packets such as repeater beacons) now appears under
  *Stations*, with position; older hits are analysed once more (without triggering the watchlist).
- English UI: remaining German status texts translated (scan progress, live reception, APRS card,
  log entries of live listening).
- Phone: hit tiles in two columns, log time no longer wraps, older log entries show the date below
  the time; "Recently heard" follows the demodulator after retuning.

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
