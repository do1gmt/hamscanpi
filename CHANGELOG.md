# Changelog

## 1.7.64 – 2026-11-03

- Fix: a requested scan that does not start no longer hangs as "Scan requested 0 %". If the scheduler finds the
  run or receiver lock taken, it logs `scan_start_rejected` with the process holding it (new `bin/lockinfo.py`,
  via /proc) and shows "Scan not started – … (PID …)" instead; `run_cycle.sh` logs `already_running` /
  `stick_busy` with the holder as well. A request still queued after 3 min without a run is reset
  (`scan_request_stale`), and "Scan now" can be pressed again after 2 min.
- Fix: saving the EiBi scan options (max. windows, threshold, Europe only) failed ("cannot access local variable
  'data'").
- Diagnose package: lock holders, `log/scan-cycle.log` and `log/scheduler.log` included.
- Self-test section 40.

## 1.7.63 – 2026-11-03

- **New network / new IP address:** checked every minute (default route, IPv4 address, gateway and its hardware
  address); a change confirmed by two checks in a row gives a notice "New IP address: …" (same network) or
  "New network: …" (different gateway) with the dashboard link, `<hostname>.local` and the rescue page – sent by
  mail/push as the new event type "New network / new IP address" (on by default, switchable under Notifications;
  without internet it is sent later). In a different network the notice asks whether the locator still fits.
  Logged as `network_changed`. Self-test section 39. README: taking the Pi elsewhere.

## 1.7.62 – 2026-11-03

- **EiBi in the scan, like "Satellites":** new chip "EiBi" under Bands & modes, its segments are the broadcast meter
  bands. When on, each scan (after the bands) checks the broadcasts on air now according to EiBi: grouped into
  windows (≤ 30 kHz apart, centre shifted off any carrier), 3 s each, carrier level of every broadcast against the
  noise floor (spectrum without DC removal), heard from 15 dB. Results in their own log `eibi_log` (not hits), scan
  rows for the progress and the scan history. Planned by `scan_plan.py` (counts in the duration estimate), run by
  `scanner.py` (`eibi_scan`), works with the PMSDR or the stick via direct sampling.
- Card **"Shortwave broadcasting in the scan"** in the scan tab (after satellite passes; on phones as well): what the
  next scan will check (or a preview while the chip is off), options (max. windows, threshold, only Europe/global
  targets), the last check per broadcast (station, language, transmitter with distance, SNR, ✓ heard), heard/checked
  per band in the last 24 h; tapping a row tunes Live in AM.
- Self-test section 38. README: EiBi in the scan.

## 1.7.61 – 2026-11-03

- Fix: **"new in last run"** (counter on the hits page and the "new" badge) now really refers to the last
  completed scan run – also when it found nothing, was interrupted for a satellite pass or failed – and only to
  hits inside that run's time window, without satellite passes. Before it took the last *successful* run and
  counted everything since its start, so after interrupted/failed runs (or while every run was falsely "failed"
  in 1.7.48–1.7.50) it showed the hits of an older run that had found something. "Hits last scan" uses the same
  run. Self-test section 37.

## 1.7.60 – 2026-11-03

- **NAVTEX in Live** (`bin/navtex.py`): SITOR-B, 100 Bd, 170 Hz shift; character table CCIR 476 (ITU-R M.476),
  frame, bit order and polarity found automatically, FEC (every character twice, copy 5 places later) – a damaged
  first transmission is replaced by its copy; noise between messages is suppressed. Every 10 s the last 60 s are
  decoded (running text, updated in place); complete messages `ZCZC B1B2B3B4 … NNNN` appear separately with station,
  subject (weather forecast, navigational warning, SAR …) and number.
- **Weather fax in Live** (`bin/wefax.py`): HF fax 120 lpm / IOC 576 (1500 Hz black … 2300 Hz white), continuous
  decoding; start tone (300 Hz) begins a new chart, phasing lines fix the horizontal position, stop tone (450 Hz)
  ends it; without start it draws anyway. The chart grows in the decode list (saved every 10 s as
  `reports/live_wefax_*.png`, deleted after 30 days).
- **Weather RTTY:** the RTTY tone-pair search also accepts 400–500 Hz and 800–900 Hz shifts (DWD 50 Bd / 450 Hz),
  in Live and for scan hits.
- **Automatic decoder by frequency** for utility stations: NAVTEX 490/518/4209.5 kHz, weather fax DWD
  3855/7880/13882.5 kHz and Northwood, DWD RTTY 147.3/4583/7646/10100.8/11039/14467.3 kHz (±2.5 kHz, USB).
- Self-test section 36 (synthetic NAVTEX with destroyed characters, fax with phasing at two offsets, DWD RTTY,
  noise-only must give nothing). README: utility decoders.

## 1.7.59 – 2026-11-03

- **EiBi shortwave schedule in Live** (`bin/eibi.py`):
  - Below the frequency (below 30 MHz): what EiBi lists on air right now within ±5 kHz – station, language,
    target area, transmitter site with distance, end time; most likely first (exact frequency, target Europe,
    nearest transmitter), further candidates below. Updated when the frequency changes and every minute.
  - Card **"Shortwave – on air now"**: everything scheduled now in the broadcast bands 120 m … 11 m, filter by
    band, language, country, station; tapping a row tunes Live to it in AM.
  - Rules: times across midnight (day of the start counts), days of week (`Mo-Fr`, `1245`, `SaSu`, `Tu,Fr`),
    single dates (`15Sep`), entries valid only between two dates (persistence 6), inactive entries skipped;
    irregular/special days marked "irregular". Transmitter sites (`k`, `/D-k`, `/D`, country default) with
    coordinates from README.TXT; target areas composed from prefix + area (CEu = Central Europe).
  - Download: `sked-<current season>.csv`, else `sked-latest.csv`, else the previous season, plus README.TXT;
    at season change and every 14 days (checked daily); failures never touch the existing list, logged once
    (`eibi_download_failed`); manual upload of CSV and README; "Fetch now".
- Self-test section 35 (fixtures in `devtools/fixtures/`). README: EiBi.

## 1.7.58 – 2026-11-03

- **Share decodes on the network like WSJT-X** (`bin/wsjtx_udp.py`; Settings → Station → "Share on the network
  (WSJT-X protocol)"): every FT8/FT4 decode from scans and Live goes out as UDP packets in the WSJT-X "UDP Server"
  format (schema 2: heartbeat every 15 s, status with dial frequency and own call/locator before each cycle,
  one decode per message with time, SNR, DT, offset, mode and text) to one or more targets (`host:port`, multicast
  possible) – for OpenHamClock, GridTracker, JTAlert & co. Configurable id, "Save and send test packet" with a
  result per target, counters and errors shown.
- **Without network:** sending happens in a background thread with a queue (scan and Live never wait); host names
  are resolved with a 3 s limit and cached; an unreachable target is logged once and "resumed" when it works again;
  decodes of the last 2 minutes are kept and sent afterwards, older ones dropped.
- Self-test section 34 (packets read back field by field with an independent reader `devtools/wsjtx_read.py`,
  reception over UDP, outage and catch-up). README: share decodes.

## 1.7.57 – 2026-11-03

- Fix: choosing an HF antenna profile (e.g. Sigma HF-360, 0.1–30 MHz) for the stick failed with "Frequency from
  (MHz) between 1 and 6000" – the stick's antenna range started at 1 MHz, the profiles at 0.01 MHz. Both now
  accept 0.01–6000 MHz (the stick receives from 100 kHz via direct sampling). Self-test section 32 extended.

## 1.7.56 – 2026-11-03

- Station map: finer zoom – the +/− buttons zoom in half steps, mouse wheel and pinch in quarter steps (before:
  whole steps only); maximum zoom raised from 12 to 15.

## 1.7.55 – 2026-11-03

- **Station position from the most frequently sent locator.** Before, the "largest" (and since 1.7.49 the newest)
  locator won – a single deviating locator (portable operation, a decoding error, or a message where the grid
  belonged to someone else) moved a station far away (HB9MZI near Heidelberg). Now the 4-character square sent
  most often wins, within it the most precise locator; counts per locator are kept in the summary (`station.grids`),
  rebuilt once after the update.
- **Open station:** search field "Open station" (with suggestions), a click on a callsign in the list or on a
  point on the map opens everything collected for that callsign: receptions, first/last heard, best SNR, position
  used on the map; every locator sent with count, distance, period and an example message (so you can see where a
  locator came from); bands and modes; every single reception (newest first, 200 per page) with message, antenna
  and source (Live or a link that opens the hit). QRZ.com link.
- Hits list can show a single hit (`/api/hits?id=…`, with "show all hits").
- README: stations. Self-test section 31 extended.

## 1.7.54 – 2026-11-03

- **Translations checked systematically:** all tabs were opened in the English interface with test data (FT8, CW,
  RTTY, APRS, SSTV hits, stations, devices, settings), everything expanded, and every visible text, tooltip and
  placeholder checked for German leftovers; in addition all German strings in the dashboard code and the band
  plan labels were run through the translation.
  - FT8 meanings are now produced in the interface language ("QSO finished", "answers", "gives … report …
    (confirmed)", "calls", undecodable messages).
  - Decoder lines ("5 messages · 3 FT8 messages · cycle … UTC"), APRS "via", SSTV "% of the lines",
    band-plan labels (beacons, satellites, satellite downlink, ISM 433/868, digital voice / data, CW/SSB narrow,
    probe satellites), device capabilities (HF via direct sampling / driver without direct sampling), scan box
    hint, serial-number message, watch-list error, track panel, backup placeholder, clock tile, "no signal" note.
- New `devtools/i18n_check.py`: the same check as a tool (Playwright).

## 1.7.53 – 2026-11-03

- Live → **Recently heard** keeps the last **30** frequencies (was 8, stored on the Pi) and shows for each the
  band, the demodulator, the band-plan mode of that frequency (FT8, APRS, FM, repeater, SSTV, …; full segment
  name on mouse-over), the RDS station name if any and how long ago it was heard; scrolls inside the card.

## 1.7.52 – 2026-11-03

- **Station map:** switch between **callsigns** (one point per station, colour = SNR, border = mode) and
  **locator squares** (4-character squares, 2° × 1°, shaded by number of stations). Details (callsign, locator,
  SNR, distance, modes, bands, last heard – or for a square: stations, best SNR, callsigns) only on mouse-over or
  tap; no more labels next to the points.
- **Finer periods** for list, map and ADIF export: 30 min, 1 h, 6 h, 12 h, 24 h, 1 week, 1 month, 6 months,
  1 year, all (new parameter `hours`; `days` still works). The map follows period, band and search.
- README: station map. Self-test section 31 extended.

## 1.7.51 – 2026-11-03

- **Fix: every scan was marked "Scan failed (Scan beendet)" since 1.7.48.** The last line of `run_cycle.sh` was
  `[ "$CANCELLED" -eq 1 ] && exit 3`; without an interruption that test is false, so the script ended with exit
  code 1 and each successful run counted as failed (hits were saved, but status "error", error notifications,
  error counter). Now explicitly `exit 3` when interrupted for a pass, otherwise `exit 0`.
- Self-test section 33 runs `run_cycle.sh` with a stand-in scanner and checks the exit code for both cases.

## 1.7.50 – 2026-11-03

- **Antenna profiles** (`bin/antennas.py`, System → Devices → Antennas): each antenna once with name, frequency
  range and – for directional antennas – direction, beamwidth, elevation; the first profiles are created from
  the existing entries (sticks, PMSDR). Choosing a profile – in the scan box ("Antenna ▾"), at the main stick or
  at the HF receiver – is saved immediately ("✓ Antenna … – applies right away"), fills the stick's antenna
  fields (used by pass planning and hits) and is logged (`antenna_changed`). Saving edited profiles updates the
  receivers using them; editing a stick's fields by hand turns its profile off. Included in the backup.
- **Pass tile in preview** (satellites not recorded): "in the sky" in a neutral colour instead of a green
  "running".
- **Phone layout of the scan page:** scan, then bands & modes and schedules, then satellite passes and history.
- **Scan history shows the receiver actually used:** HF runs via the stick (direct sampling) appeared as
  "PMSDR"; the device is now stored per scan window (`scan.device`).
- README: antenna profiles. Self-test section 32.

## 1.7.49 – 2026-11-03

- **Station map shows all stations with a locator**, not only the 100 of the current list page (that was why it
  looked like far fewer than the 1,100+ stations). New `/api/stations/map` (compact, all matching the filters),
  drawn on a canvas so thousands of points stay smooth; above 300 points without connecting lines and fixed
  labels; paging the list no longer reloads/re-zooms the map. The note says how many of how many stations are
  shown and why stations without a locator are missing.
- **Summary per callsign** (`station` table): kept up to date on every new reception (scan and Live), built once
  from `heard` on first use. List without period/band filter reads it directly; with filters the full record is
  used with new indexes (band, time). Nothing is deleted – `heard` stays the complete record. Measured with
  300,000 receptions of 20,000 callsigns: list 0.01 s, map 0.07 s, filtered 0.2 s (PC).
- New README screenshot of the scan page. README: stations. Self-test section 31.

## 1.7.48 – 2026-11-03

- **Mixed HF/satellite operation, clean hand-over of the stick.**
  - **Scan and analysis separated:** `run_cycle.sh` holds the stick lock (`scan.lock`) only while the scanner
    runs and a new `cycle.lock` for the whole run; the analysis no longer blocks passes (before, a pass waited
    until all recordings were analysed).
  - **Yield instead of kill:** when a pass needs the stick, the scheduler writes `data/scan_yield`; the scanner
    checks it between windows, while following a QSO or packets, while keep-recording HF and in long HF captures
    and stops within 1–2 s, keeping what it already recorded (exit code 3, run "interrupted for a satellite pass",
    analysis still runs). Only after 20 s: SIGTERM/SIGKILL to the run's process group, then leftover `rtl_sdr`
    are killed (before: SIGTERM to the shell only – child processes and `rtl_sdr` could keep the stick).
  - **Planning:** the expected scan duration is now the longest of the last 5 runs (+15 %) or the dashboard's
    estimate for the current band selection (+20 %), not the median – long HF scans no longer collide with
    passes. A pass that is already more than half over does not interrupt a running scan.
  - **Scan watchdog:** no sign of life from the scanner (`data/scanner.heartbeat`, per window/step) for
    10 minutes while it holds the stick → the run is ended, notice "Hanging scan ended" with where it stood.
  - **60 s grace period** after switching on "Satellites" or changing the satellite selection/priorities: no pass
    starts (countdown and "now" button in the pass list) – browsing no longer throws out a running scan.
  - Logged: `scan_yield_requested`, `scan_yielded`, `scan_stopped` (forced), `scan_yielded_for_pass`,
    `run_yielded`, `scan_hung`, `satellites_arming`, `satellite_arming`, `satellite_skipped` (half over).
- README: scans and passes share the stick. Self-test section 30.

## 1.7.47 – 2026-11-03

- **Self-healing:** systemd watchdog for the web server (checks itself every 30 s with a request to itself;
  restarted after 3 min without an answer) and the scheduler (reports every loop, 10 s; restarted after 5 min).
  Memory limits per service (`MemoryMax`: web 500 MB, scheduler incl. scans/passes/analysis 750 MB with
  `OOMPolicy=continue`) – an outlier only hits its own service. Restarts appear as notices.
- **System → Services:** state, memory and restarts of web server, scheduler and rescue page; restart buttons,
  "Restart Pi", "Download diagnostics", link to the rescue page.
- **Rescue page** (`bin/rescue.py`, own service `hamscanpi-rescue`, port 8081): standard library only, keeps
  answering when the dashboard hangs – services, memory, latest log lines, restart services/Pi, diagnostics.
  Same password and trusted networks as the dashboard (the login works for both).
- **Diagnostics** (`bin/diag.py`): logs, satellite log, kernel and service journal of the last 2 days, system
  history, notices, check state, settings without credentials, memory/disks/processes – one tar.gz.
- Restarting needs root: install and update create `/etc/sudoers.d/hamscanpi` (checked with `visudo`), allowing
  exactly `systemctl --no-block restart hamscanpi-web|hamscanpi-scheduler|hamscanpi-rescue` and
  `systemctl reboot`. Requests are logged (`restart_requested`, `reboot_requested`, with IP).
- README: "When something hangs". Self-test section 29.

## 1.7.46 – 2026-11-03

- **Cause of the hangs in mixed HF/satellite operation fixed: memory during the analysis.** The system history
  showed `analyze_recordings.py` with 476 MB and only 128 MB free before the hang (and an out-of-memory kill on
  29.10.). The analysis loaded every recording completely in double precision and kept 6–8 copies – harmless for
  the former 10 s recordings, but 400–500 MB for the 90–120 s shortwave recordings since keep-recording (1.7.28)
  and HF via the stick. The Pi then swapped until the dashboard stopped answering.
  New `bin/iqload.py`: recordings are read in 2 s pieces, mixed to the hit frequency (continuous phase) and
  reduced to 12 kHz right away, in single precision; the spectrum image reads only the first 5 s. Used by
  `analyze_recordings.py` (USB/LSB audio, now a complex band-pass at 12 kHz), `decode_recordings.py` (FT8 check,
  CW, RTTY at 12 kHz), `analyze_vhf.py` and `analyze_fm.py` (mixing only). Measured on a 120 s recording:
  837 MB → about 210 MB peak, of which ~100 MB are the Python libraries themselves.
- A recording that cannot be analysed (out of memory or error) is skipped and logged (`analysis_skipped`,
  `decode_skipped`/`decode_failed`) instead of stopping the whole analysis; it is not retried on every scan.
- README: memory note. Self-test section 28 (120 s recording: correct SSB tone, memory bound).

## 1.7.45 – 2026-10-30

- The ⚠ notice counter (and the pass tile) jumped too far: the target card ended up under the sticky header
  and tabs. Jumps now stop just below them.

## 1.7.44 – 2026-10-30

- **System → PSK Reporter – last report:** below the Raspberry Pi card, the newest 15 spots of the last report
  (callsign, frequency, band, mode, SNR, locator, time heard in UTC, antenna), when it was sent, how many spots
  and packets, receiver/locator/software; notes if reporting is off, callsign/locator are missing or the internet
  is down. The last report is kept in `data/pskreporter_state.json`.

## 1.7.43 – 2026-10-30

- **Continuous watch (Live without a browser).** New tick box "Continuous watch" with optional end time in Live:
  reception keeps running with the decoders only when no browser is listening (before: Live ended 90 s after
  the last listener) – e.g. FT8 on 14.074 all night to PSK Reporter, or APRS on 144.800. Stored on the Pi
  (`data/live_state.json`): restarted automatically after a web server restart, ended at the end time or with
  Stop. **Satellite passes** no longer get skipped: the scheduler asks for the stick (`data/live_yield.json`),
  the watch pauses, the pass is recorded, then the watch continues on the same frequency. Scans pause while the
  watch runs. Header shows "Continuous watch 14.074 USB · until 07:00" (or "paused (ISS)"); a notice is shown
  while it runs.
- README: new Live screenshot (10 m FT8 via the stick with live decodes).
- Self-test section 27.

## 1.7.42 – 2026-10-30

- **Live no longer stutters after starting.** The automatic gain (stick) went up one step per second – and every
  change restarts `rtl_sdr`, an audible gap; with overload it stepped down and crept up again, which could take up
  to two minutes. Now it jumps in one step to where the peaks reach about 40 % of the range, changes at most every
  10 s, and starts at the gain it last settled on for that 10 MHz range (stored on the Pi). Also: the automatic
  decoder choice read the band plan for every 20 ms audio block (now only when frequency/mode change; band plan
  reading cached), and the browser's playback lead grows after a dropout (up to 0.8 s) and shrinks again.
- **PSK Reporter:** one spot per callsign, band and mode per report (the best SNR, with the locator from any
  cycle) – a CQ caller heard every 30 s in Live is no longer reported repeatedly; the antenna of the receiver
  that actually heard it (stored per heard station, scan and Live; separate receiver records per antenna);
  software reported with version ("HamScanPi 1.7.42").

## 1.7.41 – 2026-10-30

- **Without internet:**
  - **Mail/Push** messages are kept when the network is missing (not when a setting is wrong, e.g. a rejected
    token) and sent later, marked "(late, originally 14:32)"; up to 50 messages, at most 3 days old.
  - **PSK Reporter:** after a network error the next attempt is 5 minutes later (before: every minute, with a
    warning each time); warned once per outage, "resumed" logged when it works again; the spots of the last hour
    are reported afterwards. Also fixed: with an empty station list at the first run, reporting never started.
  - Notice **"No internet since …"** after 15 minutes (checked every minute by TCP to public DNS/HTTPS servers),
    gone by itself when the connection is back; the outage length is logged.
- Self-test section 26.

## 1.7.40 – 2026-10-30

- **RBDS** for the US and Canada: North American programme types (Rock, Top 40, Country, …) instead of the
  European RDS list, and the station call sign from the PI code (e.g. WABC) in Live and the FM station list.
- **More countries:** Austria (no Freenet, no 4 m), Switzerland (no Freenet, 4 m since 2023), United Kingdom
  (RSGB: 4 m 70.0–70.5 MHz, UK 60 m allocations, 70 cm repeater ranges with 1.6/7.6/9 MHz shifts, CB 27/81) and
  Canada (like the US; 70 cm 430–450 MHz, FRS without GMRS repeaters, no MURS; 60 m band + 4 channels). Profiles
  can now also patch single segments. The locator suggests the country (also AT, CH, GB). Sources and notes in
  each profile; national details marked where only derived – corrections welcome.
- **Automatic backup** (System → Backup): daily or weekly at a chosen hour to a USB stick or a mounted network
  share, keeping the last N files (by name, the new file is never pruned); "Back up now"; target on the SD card
  itself refused; failure becomes a notice ("Automatic backup failed"), cleared on the next success. Mount example
  for an SMB share in the card.
- **Notices in English:** every notice also gets an English title, text and advice (shown in the English
  interface, used for mail/push when the interface is English).
- Self-test section 25.

## 1.7.39 – 2026-10-30

- **Live remembers the last frequency on the Pi** – for every browser and device: frequency, demodulator, tuning
  step and "Recently heard" are stored in `data/live_state.json` (on start, tuning, stop and when changed without
  reception; written at most every 10 s and at the latest 10 s after the last change, to spare the SD card).
  Opening Live anywhere shows the last settings. Included in the backup.

## 1.7.38 – 2026-10-30

- Live: the decodes moved into their own card "Decodes" in the right column (on phones below the controls and
  the waterfall), scrolling inside the card. The tuning buttons, decoder choice and record button stay in place –
  before, the growing list sat between the controls and the listen/seek buttons and pushed them down.

## 1.7.37 – 2026-10-30

- **Live decodes to the station list and PSK Reporter.** FT8/FT4 senders (with locator) and APRS senders with
  position from Live are written to the station list (`heard`, marked "Live:"); FT8/FT4 spots go to PSK Reporter
  with the next report (at most every 5 minutes, as before) when reporting is switched on.
- **CW and RTTY in Live** as running text: every 8 s the last section goes through the decoders of the hit
  analysis (CW: strongest tone 300–1200 Hz, cut at a word gap so no character is split; RTTY: tone pair in the
  passband, only the part with signal). Noise-only sections no longer produce "??H??".
- **SSTV in Live:** while the 1100–2400 Hz band is busy, the audio of the last 150 s is decoded every 15 s – the
  picture grows in the list (one entry per picture, updated in place); images in `reports/live_sstv_*.png`,
  deleted after 30 days like the recordings.
- Automatic decoder choice extended: CW and narrow digital segments (RTTY) in USB/LSB, SSTV frequencies.
- **RTTY baud rate from the signal** (also for scan hits): the bit lengths decide between 45.45, 50 and 75 Bd –
  before, the rate with the most printable characters won, which often picked 75 Bd for a 45.45 Bd signal.
- `decode_recordings.py` can be imported (main part in `main()`).
- Self-test section 24: CW and RTTY from a live stream, growing SSTV picture, live stations in `heard`.

## 1.7.36 – 2026-10-30

- **Decoders in Live** (`bin/live_decode.py`): the demodulated audio that the browser hears also goes to
  decoders – *FT8/FT4* (audio collected on the UTC cycle, ft8_lib after every cycle; listen in USB on the dial
  frequency), *APRS/packet* and *POCSAG* (multimon-ng running continuously, APRS mode with the "⟲" mark for
  repeated packets). New row under the Live controls: decoder *automatic* (by the band-plan segment of the
  frequency: FT8/FT4 in USB, APRS and POCSAG in FM) or fixed, and a live list of the decodes (time, type, SNR and
  audio offset for FT8, text). The decoders run in their own threads only while Live is on; the audio to the
  browser never waits for them.
- **Record in Live:** "● Record" saves the audio as WAV under `recordings/live/` (at most 10 min), with a download
  link; deleted automatically after 30 days.
- Self-test section 23: APRS packets decoded from a live audio stream, FT8 cycle cut on the UTC boundary.

## 1.7.35 – 2026-10-30

- **ISS APRS on 437.825 MHz (RS0ISS, Zvezda).** According to the ARISS status page (October 2026) the Columbus
  radio (NA1SS) runs the voice repeater (145.990 up / 437.800 down) and APRS is active on the Zvezda radio
  RS0ISS on 437.825 MHz. New channel `iss_aprs70` with Doppler correction and APRS decoding like 145.825; the
  145.825 channel stays for when NA1SS returns to packet. Whoever had 145.825 selected gets 437.825 added on
  update. VIZARD-meteo (SSTV) shares 437.825 – both are recorded together when they overlap.
- **Digipeated or heard directly?** multimon-ng now runs in APRS mode (`-A`, TNC2 output, falls back on older
  versions): a digipeater marked with "*" really retransmitted the packet. Packets repeated by a satellite
  digipeater (ARISS, RS0ISS, NA1SS, PCSAT, …) end in "⟲ RS0ISS (satellite)"; ground stations heard directly
  have no mark. The pass summary counts the packets repeated by the satellite.
- Self-test section 22.

## 1.7.34 – 2026-10-30

- **Band plan by country / IARU region** (Settings → Station → Country / band plan). New `bin/bandplan.py`
  lays a country profile from `bandplans/<country>.json` over `config/scanner.json` when reading (remove /
  replace / add bands, change legal status) – the file itself stays unchanged, own band overrides are kept.
  All parts that read bands use it (scanner, scan plan, dashboard, segment guard, analysis and decoders).
  Germany (Region 1) is the default and exactly as before.
- **USA (Region 2)**, checked against the ARRL band plan: 160/80/60/40 m with US edges and segments (60 m:
  4 channels + 5351.5–5366.5 kHz segment), CB 40 channels, 6 m 50–54 MHz with repeaters −500 kHz, 2 m 144–148
  MHz (APRS 144.390, simplex 146.40–146.58/147.42–147.57, call 146.520, repeater outputs with −600 / +600 kHz),
  **1.25 m**, 70 cm 420–450 MHz (simplex 446.000, repeaters +5/−5 MHz, satellites 435–438), **33 cm**, 23 cm
  repeaters (−12 MHz) and simplex 1294.5, **FRS/GMRS**, **MURS**, **NOAA Weather Radio**, **ISM 915**, FM
  broadcast 87.9–107.9 MHz, airband as a normal band. Freenet, PMR446, LPD433, ISM 868 and 4 m are not offered.
  The locator suggests the country; Live starts on 146.520 in the US.
- System → Now: with zram the memory warning only appears when memory is really short (under 200 MB available or
  zram over 80 % full); the swap tile is coloured by zram fill level. Swapping idle pages into zram is normal.
- Self-test section 21: Germany unchanged, US bands, APRS/simplex/repeater classification, scan plan.

## 1.7.33 – 2026-10-30

- README screenshot of the scan page: header corrected (status and clocks on the right – the screenshot was
  taken before the header fix of 1.7.31).

## 1.7.32 – 2026-10-30

- **Shortwave scans with the stick.** If the main stick can do HF via direct sampling (e.g. NESDR SMArt v5) and
  the RTL-SDR is selected, the HF bands (160–10 m, up to 28.8 MHz) can be chosen as well. They run through the
  new device `rtlhf`: the stick records at 240 kS/s in direct sampling (60 kHz off-centre), HamScanPi computes
  the same 48 kHz windows as with the PMSDR (also in the PMSDR's I/Q orientation) – so voice, FT8/FT4 (cycle
  start on the UTC boundary), CW/RTTY with keep-recording and SSTV detection work unchanged. Long recordings
  (SSTV up to 120 s) are reduced second by second instead of holding ~300 MB raw data; a stalled stick ends the
  recording with an error after 6 s. The scan box says "VHF/UHF and HF up to 28.8 MHz (direct sampling – HF
  antenna needed)"; schedules can use the HF bands too.
- Translations: "Main stick:", "… days" (uptime), the antenna note under Devices.
- README: shortwave without a PMSDR; new screenshot of the system page; new settings screenshot.
- Self-test section 20: window position, length and cycle timing with a simulated stick, HF bands in the plan.

## 1.7.31 – 2026-10-30

- Header: without open notices the status pill and the clocks jumped to the left next to the logo (CSS rule for
  the notice counter also applied while it was hidden).
- Translations: notification settings ("Collect every … min", "Levels:", "at … o’clock (last 24 h as a file)"),
  "HF/VHF/UHF" in the device box, "Probe satellite" in the satellite list.
- New README screenshots: scan page (pass preview, distance, HF-capable stick) and settings (notices and log
  sending).

## 1.7.30 – 2026-10-30

- **Saving a stick now shows it.** "✓ saved" appears right next to the button of that stick (the common note
  line below the list was often out of view) and the card is briefly outlined green; the scan box (antenna,
  device name, range) is refreshed at the same time instead of only after reloading the page.

## 1.7.29 – 2026-10-30

- **Satellite passes independent of the band chip.** The pass list is always computed for the selected
  satellites; the chip "Satellites" only decides about recording. Without it the list shows "preview only" and
  "preview – not recorded". The box can be collapsed (▾/▸, remembered per browser); the pass tile and 🛰 work
  without the chip.
- **Notices** (counter at the top, System → Notices): every problem with what happened, since when, what to do
  and a command to copy; acknowledge single or all; resolved ones stay visible for 7 days. Checked every minute
  (`bin/health.py`): system restart (boot id), stick plugged in / removed / changed, main stick missing, stick
  without role, SD card and file system errors from the kernel log, read-only file system, storage almost full,
  high write load, zram full, process killed by the kernel, undervoltage, throttling, heat, clock not
  synchronised, service restarted, missing software (rtl_sdr, multimon-ng, ft8_lib, Python packages), outdated
  orbital elements, driver without direct sampling. The pass watchdog and updates also report as notices.
- **Notifications by level, area and event** (Settings → Notifications): levels error/warning/info, areas,
  "always report" for stick changes, restart, SD card, power/heat and updates; collect into one message every
  X minutes (errors always immediately); **send the log** (ERROR/WARN/INFO/DEBUG) daily at a chosen hour or now,
  as a file by mail or Telegram/ntfy.
- **Stick capabilities** (`bin/sdrcaps.py`): range by tuner and model, HF via direct sampling (NESDR SMArt v5,
  RTL-SDR Blog V3) or upconverter (Blog V4, needs the Blog driver); shown in System → Devices and the scan box.
  The direct-sampling syntax of the installed driver is read from the help text (`rtl_sdr -D` without value on
  Debian's 2.0.2, `-D 2` on other builds, `rtl_fm -E direct2`).
- **Live below 24 MHz with the stick:** direct sampling (Q branch) is switched on, gain control hidden with a
  note (tuner bypassed, HF antenna needed), lower limit 100 kHz. Device choice: HF goes to the PMSDR if present,
  otherwise to the stick if it can; otherwise a clear message.
- Self-test section 19.

## 1.7.28 – 2026-10-29

- **Stay on signals to decode more.** Until now data packets only got the short listening window plus at most
  6 s, CW/RTTY a fixed 10 s.
  - **Data packets (APRS, POCSAG):** the scan stays on the channel as long as a new packet arrives within 20 s,
    at most 60 s (continuous `rtl_sdr` stream, packet detection against the noise floor).
  - **HF CW/RTTY:** recording continues in 4 s pieces while the station still transmits (6 dB above noise in
    ±100/±300 Hz), until 6 s of silence, at most 60 s; appended to the hit recording.
  - **HF SSB voice:** at least the normal recording time, then on while someone speaks (overs alternate,
    pauses up to 6 s), at most 90 s.
  - **FT8/FT4:** after a cycle with at least 3 messages one more cycle right after (separate hit) – answers
    come along, more callsigns and QSOs.
  - VHF voice unchanged (until 4 s pause, at most 120 s).
- **Settings → Keep recording:** all limits adjustable (0 = off); stored in `config/scanner.json`.
- Self-test section 18: APRS follow with a simulated packet stream, CW continuation and limit.

## 1.7.27 – 2026-10-29

- **Backup and restore** (System → Backup, `bin/backup.py`). One file `hamscanpi-backup-<date>-<scope>.tar.gz`:
  *settings* (config, device register, satellite statistics), *+ data* (databases: hits, stations, FM
  stations, interference lines, system history – copied with SQLite's backup function, consistent while a scan
  writes) or *+ recordings* (recordings/ and reports/, streamed directly, no temporary copy on the card).
  *Include credentials* (default on) – without it mail password, tokens and access protection are left out.
  Restore: upload in the same card → contents shown (date, version, callsign, scope) → *Restore*; applied by
  the scheduler as soon as no scan or pass runs, current config/ and data/ moved to `restore-previous/<time>/`
  first, recordings added (never deleted), configuration migrated, services restarted, page reloads.
  Archives are checked (manifest, only config/, data/, recordings/, reports/, no `..` or links).
  Command line: `backup.py create|info|restore`. README: *Backup and restore*.
- Update script: new installations no longer get "NESDR SMArt v5" / "HF-360" / "Discone" as default device
  names and antennas (neutral defaults; existing entries stay).
- Self-test section 17: backup in three scopes, without credentials, restore, manipulated archive rejected.

## 1.7.26 – 2026-10-29

Checked on phone (390 px) and tablet (820 px) widths:
- 🛰 in the pass tile moved to the bottom right – on phones it covered the countdown.
- Sky view: a time label at the top edge (e.g. set near north) is placed below its point instead of running
  into the "N".
- English interface: east is "E" (was "O") in the sky view and in the direction values.

## 1.7.25 – 2026-10-29

- **Track panel follows the next pass.** Left open, it stayed on a finished pass. New option *follow the next
  pass*: one minute after set the panel switches to the next planned pass by itself (sky view, map and live
  values). On when opened from the pass tile at the top, off when opened from a row of the pass list (that pass
  stays) – can be switched in the panel.

## 1.7.24 – 2026-10-29

- **Watch a pass live** (🛰 in every row of the pass list and in the pass tile at the top): a panel slides in
  from the right with
  - **Sky view** (polar plot: centre = zenith, edge = horizon, rings at 30°/60°, N/E/S/W): the path across the
    sky with rise, highest point and set times, the satellite as a moving dot, and the **beam of a directional
    antenna** shaded (System → Devices) – so you see when it crosses your Yagi;
  - **Map** (OpenStreetMap in the browser): ground track, the part above your horizon highlighted, your
    location, the satellite live with its **footprint** (where it can be heard right now);
  - live values: countdown to rise/set, elevation, direction (with compass point), distance, **Doppler** on the
    receive frequency with the frequency to set.
  The Pi computes the track once when the panel opens (`/api/sat/track`, ~120 points, a few ms); the browser
  moves the dot every second by itself. Esc or ✕ closes the panel.
- Self-test: track computation (points, highest point matches the plan, plausible altitude, beam included).

## 1.7.23 – 2026-10-29

- **Antenna frequency range as one field** (System → Devices): "valid for (MHz)" takes `137–150`, `137-150`
  or `137,5 - 146`; empty = all frequencies. The two separate number fields rejected a typed range, so nothing
  was saved.
- Saving the main stick no longer asks "Use this stick as the main stick?" every time – only when the role
  actually changes to main stick.

## 1.7.22 – 2026-10-29

- **Shortest distance in the pass tile** at the top of the scan page ("Pass RS-44 · 72° · ↓ 1,050 km"), for the
  running and the next pass.
- **Current distance during a recording** in the scan status line ("elevation RS-44 21° (1,840 km)"), updated
  every 5 s together with the elevation.

## 1.7.21 – 2026-10-29

- README: new section **SD card or SSD** – comparison (wear, speed, power loss, power draw, USB 3 interference
  with the RTL stick), swap/zram (Raspberry Pi OS Trixie already uses zram – check with `swapon --show`), and
  **moving to an SSD** step by step (boot the whole system from the SSD, install, copy `config/`, `data/` and
  optionally `recordings/`/`reports/` from the old card).
- System → Now: with zram the memory warning no longer claims the Pi is swapping to the SD card; it explains
  that zram compresses in RAM and that a full zram makes the kernel end a process.

## 1.7.20 – 2026-10-28

- **SD card wear visible.** *System → Status* shows how much was written to the system drive in the last
  24 hours (with the daily average over up to 7 days) and whether it is an SD card, USB drive or NVMe SSD,
  plus where the swap lives (zram, file, or zram with the file as reserve). Swap on an SD card without zram
  gives a warning. The history records the MB written per minute (`/proc/diskstats`, no extra packages).
- README: new section *Protecting the SD card* – zram via `zram-tools` with the swap file kept as a
  low-priority reserve, boot from USB SSD, endurance cards.
- Self-test: storage info and write counter.

## 1.7.19 – 2026-10-28

- **System → Devices no longer overwrites unsaved input.** The device list reloaded every 15 s and only held
  back while a field had focus – entering direction, beamwidth and frequency range took longer than that and
  values were lost between fields. Now any change locks the list until *Save* (note "Change not saved yet",
  discarded after 10 min), and the list is only redrawn when the data actually changed (no flicker).
- **Tooltips** (mouse-over) on all device fields (role, ppm, gain, antenna, direction, beamwidth, elevation,
  frequency range), the HF receiver fields and the satellite gain / beam settings; also in English.

## 1.7.18 – 2026-10-28

- **Directional antennas.** System → Devices: for the main stick, direction (azimuth), beamwidth (default 70°),
  elevation (tilt) and the frequency range of the antenna can be entered (empty direction = omni). The pass
  list then shows for each pass which part runs through the beam ("in the beam 16:18–16:22, up to 18°",
  "not in the beam", "antenna: wrong band"). With *Prefer passes through the beam* (Settings → satellite
  priorities, on by default) passes with at least 1 min in the beam get up to 15 extra points, the others and
  frequencies outside the antenna range 20 points less; "always first" is not affected.
- **Antenna per hit.** Each scan window and satellite recording stores the antenna that was entered at the
  time; the hit list shows it ("📡 Yagi 4 el. 2 m, 245°"). Hits from before this version have no entry.
- Self-test: no longer produces a "Broken pipe" traceback in the funktest check (it made the GitHub run red
  because the workflow fails on any traceback); new section 16 for directional antennas.

## 1.7.17 – 2026-10-28

- **Level display no longer jumps in and out.** It disappeared whenever no fresh value was available (between
  receive windows and bands, during evaluation, outside scans), which made the scan box jump. Now it always
  stays in place: without a measurement an empty, dimmed bar ("Level – no scan running"), older values
  (up to 3 min, e.g. between bands) are shown dimmed instead of vanishing; the trend line keeps its space.

## 1.7.16 – 2026-10-28

- **Signal level display** (dB above noise – no S-meter, the RTL stick is not calibrated in dBm): bar 0–40 dB
  with scale, value, peak mark (highest value of the last 10 s) and trend line.
  - Scan box during a **satellite pass**: channel level every second with 3 minutes of history; Meteor shows
    one bar each for 137.100 and 137.900 MHz.
  - Scan box during a **band scan**: strongest signal of each receive window with its frequency, history of
    the last 60 windows.
  - **Live**: full display with own demodulation (history restarts on retuning); the simple player keeps
    the plain bar.
  The pass writes `data/meter.json` every second and removes it at the end; the scanner writes it per window.
- **Shortest distance per pass** in the satellite list (elevation column: slant range at the highest point),
  e.g. Meteor at 52° ≈ 1,000 km, at 10° ≈ 2,400 km.
- **Heard stations:** the band filter only offers bands that can contain callsigns (HF, 6 m, 2 m, 70 cm,
  satellites) – broadcast, PMR446, ISM etc. had always shown an empty list with "no stations heard yet".
  An empty result due to a filter now says so.
- Self-test: level file written and removed after the pass.

## 1.7.15 – 2026-10-28

- **Antennas configurable in the interface** (System → Devices). The antenna of the main RTL stick (its
  device entry) now applies everywhere – scan display and satellite passes; a new card *HF receiver* sets name
  and antenna of the PMSDR (stored in `config/scanner.json`). Without an own name the RTL receiver is shown
  with the name of the main stick (e.g. "Nooelec NESDR SMArt v5") instead of "RTL-SDR". A note explains that
  the antenna is only a label – with an antenna switch, update it when switching.
- **System history records the largest process per minute** (sampled every ~10 s, HamScanPi scripts by
  script name, others by program name). Tapping a point shows e.g. "largest process scanner.py 620 MB" –
  to find the cause of swap peaks like the one on 27 Oct (swap full at 905 MB for a few minutes). Existing
  history databases are extended automatically.
- New README screenshot of the scan page.
- Self-test: history row with largest process.

## 1.7.14 – 2026-10-28

- **Meteor/LRPT rated in steps.** First real reception on 27 Oct (N2-4, 60°, discone): a clearly visible
  120 kHz block on 137.900 MHz at 2–4 dB with fading – but the list said "no LRPT" and "no signal", because
  "heard" required 30 s above 6 dB. Now the power curve is smoothed over 10 s and rated: **weak** (≥ 60 s above
  1.5 dB), **good** (above 4 dB), **strong** (above 8 dB). Noise alone stays within tenths of a dB. Probe
  satellites no longer get the red "no signal" line; the list shows e.g. "LRPT weak reception on 137.900 MHz
  (5 min above 1.5 dB)", also in English.
- **Hits: spectrum only on request.** The spectrum is hidden behind "Show spectrum" and only loaded then
  (less rendering on the Pi); listening and tuning work without it.
- **Practice mode** (Hits, checkbox): mode badge, decodes, band plan notes, segment and spectrum stay hidden
  until "Reveal" – to learn signals by ear first.
- **Info buttons (ⓘ)** with short explanations next to demodulator, DNR, tuning, audio download, spectrum and
  the hit actions; tap to open, tap elsewhere or Esc to close.
- Self-test: LRPT rating (noise, weak with fading, strong).

## 1.7.13 – 2026-10-27

- **FT8 "Error [n] while unpacking!"** (output of ft8_lib for a frame received correctly but of a type it
  cannot unpack) is shown as "received but not readable" with the reason from ft8_lib's error code – type 5:
  DXpedition (Fox/Hound) or contest formats; 1–4: callsign, suffix or locator only sent as a hash.
- **Bias-T for satellite recordings and Live.** `rtlsdr.bias_t` (power for an LNA at the antenna) was only
  applied by the scanner; satellite passes and Live started `rtl_sdr`/`rtl_fm` without `-T`, which would leave
  an LNA unpowered (and attenuating).
- **Antenna and gain per pass.** The pass list and the pass log show the antenna (System → Devices, otherwise
  `antennas.rtlsdr`) and the gain used for each recording. HamScanPi cannot see the position of an antenna
  switch – the entry makes it easier to match recordings to antennas afterwards.
- **`bin/funktest.py`**: next FM satellite and Meteor passes (24 h, from 20°) with a per-minute table of
  direction, elevation and the frequency to set on a handheld (Doppler rounded to 5 kHz).
  `python3 bin/funktest.py` / `python3 bin/funktest.py <no>`.
- Self-test: funktest.py.

## 1.7.12 – 2026-10-26

- **Interface language stored on the Pi.** It used to live only in the browser (`localStorage`) and was lost
  whenever the browser deleted site data, on another address (IP vs. name) or another device – the dashboard
  then fell back to the browser language. Now it is saved in `config/web_settings.json` (`ui_lang`), delivered
  with the page and valid for every browser; a previous browser choice is taken over automatically.
- **Web server memory when listening to hits.** SSB/CW demodulation ran an FFT over the whole I/Q recording at
  full sample rate in double precision, after mixing with a full-length int64 time axis – a few 100 MB peak for
  longer HF recordings, which glibc kept in the process (web server at ~300 MB, Pi swapping ~400 MB). Now mixing
  runs in blocks in complex64, SSB/CW is reduced to 12 kHz before filtering (measured: 35 MB peak for 60 s
  instead of ~350 MB), freed memory is returned to the system after each demodulation and spectrum image,
  and the cache of played hits is limited to 40 MB.
- "SNR up to": for very short recordings the hold time is at most a quarter of the recording, so "max" can no
  longer be below the 90 % value (10 s hold for normal passes unchanged).
- GitHub self-test: installs `soundfile` (needed by the FT8 test – the cause of the red v1.7.11 run), own
  Python 3.12 via `actions/setup-python@v6`, `actions/checkout@v5`; the FT8 test now prints its error.

## 1.7.11 – 2026-10-26

- **A hanging stick no longer blocks the night.** In the night to 26 Oct the RTL stick stopped delivering
  data at the start of an ISS pass; `rtl_sdr` stayed alive, `sat_pass.py` waited on the pipe for seven hours
  and every following pass (including a Meteor at 86°) was skipped. Now:
  - the satellite recording reads with a time limit: no data for 5 s → `rtl_sdr` is stopped, the stick is
    re-registered on USB (same reset the scanner already used) and restarted; the recording continues on the
    correct time axis (Doppler) with a short gap – up to 4 times per pass, logged as `pass_stick_restart`;
  - the recording ends by the clock at the planned end even if blocks were lost;
  - scheduler watchdog: a pass process still running 10 min after its planned end is terminated
    (SIGTERM to the process group, SIGKILL after 15 s; the detached evaluation is not affected).
- **Separate gain for satellites** (Settings → Satellite priorities). "As main stick" keeps the previous
  behaviour; e.g. 40.2 dB for satellites while scans stay at 20 dB. The pass log shows the gain used.
- **"SNR up to" only counts levels held for at least 10 s.** A 2-second ground burst no longer shows as
  "SNR up to 34 dB"; the short peak is kept as `peak_db`.
- **FT8 detection without false alarms on carriers and noise.** Requires at least 5 tones, a 10–90 % tone
  span of 25–75 Hz, no single tone above 60 % of the time and tones that persist across adjacent frames
  (FT8 symbols last 0.16 s; noise peaks jump randomly). A carrier wobbling between two neighbouring bins
  (span 6 Hz) used to be reported as "FT8 98 %". The percentage now depends on level and tone variety
  (strong ~93 %, near the threshold ~75 %).
- Update window: margin before a planned pass reduced from 15 to 3 minutes (the update takes ~1 minute).
- Clicking the HamScanPi logo opens the *Scan* tab.
- Self-test section 15: stalled stick with restart, 10-s hold for "SNR up to", FT8 vs. carrier vs. noise.

## 1.7.10 – 2026-10-25

- **Updates no longer wait out the end of a pass.** If a satellite recording has at most 3 minutes left, or has
  low priority ("when the stick is free"), `hamscanpi-update.sh` ends it cleanly – the same way as *Cancel* in
  the dashboard: the recording stops in order and everything recorded so far is still evaluated (result
  "cancelled"). The update then waits only for the evaluation, which is never interrupted.
- Upcoming low-priority recordings no longer hold an update back; only full-priority passes starting within
  15 minutes do.
- While waiting, the update shows which channels are recording and how many minutes are left.
- Self-test section 14: simulated running recordings (last minutes, long remainder, low priority).

## 1.7.9 – 2026-10-25

- **Probe satellites (Meteor-M LRPT) measured properly.** Probe passes now record at 2.048 MS/s tuned to
  137.500 MHz, so **both LRPT frequencies (137.100 and 137.900 MHz)** are in view – Meteor-M switches between
  them. Each second the power in the signal core (±40 kHz) is measured on both against the free band in between.
  - The pass list shows the level per frequency and "LRPT received on …" (≥ 30 s above 6 dB) or "no LRPT".
  - New waterfall for probes: **±150 kHz per frequency**, both side by side, with dashed marks at the expected
    LRPT width (±60 kHz) and a **power-over-time curve** next to it. The ±9 kHz channel marks of FM channels no
    longer appear on probe waterfalls (they looked like signal lines).
- **Updates wait for a free window.** `hamscanpi-update.sh` no longer restarts the services in the middle of a
  satellite pass: it waits until no recording or evaluation is running and none starts within 15 minutes
  (new `bin/update_window.py`). Skip the wait with `HSP_SOFORT=1 bash hamscanpi-update.sh`.
- **Less memory for long FM passes.** The demodulated audio is written to the SD card during the recording
  instead of being kept in RAM (~110 MB per FM channel for 20 minutes), and APRS evaluation resamples in
  blocks instead of the whole pass at once (~200 MB less). Leftover `.sat_*.aud` files are cleaned up after 48 h.
- Self-test: new sections 13 (probe satellite on 137.900 with simulated LRPT) and 14 (update window).

## 1.7.8 – 2026-10-25

- **SDR sticks are recognised and told apart** (new panel *System → Devices*). HamScanPi reads manufacturer,
  product and serial number of every connected stick from `/sys` – without opening it, so also during a scan –
  and keeps a register in `data/sticks.json` (survives updates).
  - Each stick has a **role**: main stick (VHF/UHF, satellites, live), spare, or ignore; plus its own
    **ppm correction**, **gain** and an antenna note. Empty ppm/gain = values from the band plan.
  - Only the **main stick** is used, and it is opened by its **serial number** (`rtl_sdr -d <serial>`), so the
    USB port and order no longer matter. Before, every program opened "device 0".
  - The first RTL stick of an installation becomes the main stick automatically (existing setups: the NESDR).
  - **A newly connected stick is never used on its own.** It is logged, announced by mail/push (Telegram), and
    the System tab shows "!" until you choose its role. If the main stick is unplugged, HamScanPi does not fall
    back to another stick.
  - The scheduler reads each stick's tuner type once while idle (`rtl_test` under the scan lock, so a scan or
    pass always has priority).
  - Sticks with the same serial number (factory `00000001` on many cheap sticks) are flagged; with only that
    stick connected, **"Write serial number"** gives it an own one via `rtl_eeprom` (after confirmation; role and
    settings move along; effective after re-plugging).
  - RTL-SDR Blog V4 (R828D) gets a hint about the driver from rtl-sdr.com.
  - Serial numbers that `rtl_sdr -d` would read as a device number (e.g. `00000001` = octal 1) are not used as
    serial; such a stick is opened by USB order and flagged, like duplicates.
  - Other SDRs (Airspy, HackRF, SDRplay, FUNcube) are shown as "detected, not supported" instead of staying invisible.
- USB reset after a hanging stick now resets exactly the main stick.
- Mobile: notice badges on the tabs no longer widen the tab bar.
- Self-test: new section 12 (sticks, with a simulated USB tree).

## 1.7.7 – 2026-10-25

- **New tab "System"** (between Log and Settings; on phones a small chart icon). The former
  *Settings → System* panel (now "Status") and "Clean-up" moved there.
  - **Now** (refreshed every 5 s while the tab is open): CPU total and per core, clock, temperature,
    load, memory bar (programs / cache / free) and swap, with warnings for low memory, swap ≥ 100 MB and heat.
  - **HamScanPi**: what is running right now (idle / scan / live / pass), whether NESDR and PMSDR are
    connected, and HamScanPi's own processes (web interface, scheduler, scan, pass, rtl_sdr, …) with RAM
    and CPU, plus the rest of the system.
  - **Raspberry Pi**: model, OS, kernel, Python, hostname, IP, Wi-Fi level, uptime.
  - **History** (1 h / 6 h / 24 h / 7 days): CPU (average and peak), available memory and swap,
    temperature; a colour band marks scans, passes and live reception, a red mark throttling. Tap or
    hover to read the values at a point in time – e.g. to see whether the Pi was swapping during a pass.
- New `bin/sysmon.py`: reads only `/proc` and `/sys` (no extra packages, no processes started for the
  5-s view). The scheduler writes one data point per minute to its own `data/sysmon.db` (kept 7 days),
  so it never competes with a scan for the lock on `scanner.db`. Whether a scan is running is read from
  `/proc/locks` without touching the scan lock.
- Self-test: new section 11 (system values, process assignment, activity, history).

## 1.7.6 – 2026-10-25

- README: map screenshot with SNR colours.
- `devtools/build_update.sh` and `devtools/update_head.sh`: build the self-extracting update file
  `hamscanpi-update.sh` from the repository (until now this tool only existed outside the repo).

## 1.7.5 – 2026-10-24

- **Test satellites Meteor-M N2-3 and N2-4** (137.9 MHz, transmit continuously with a ~120 kHz wide LRPT
  signal that simple antennas receive): recorded like other passes, without decoding; their level is
  measured against a free part of the same receive window ("SNR up to … dB"), plus waterfall. If they
  show up, the satellite recording works from antenna to evaluation; if not, the chain itself is the
  problem. Added once to an existing individual satellite selection.
- **Memory:** spectrum and playback on hit cards read recordings as float32 and only as much as
  needed (spectrum 20 s, playback 3 min). Before, a long recording (QSO up to 120 s) was loaded
  completely in float64; the web server grew to ~700 MB, the Pi (906 MB) started swapping hard, and
  satellite passes and scans were delayed or missed.
- Hardware detection is cached for 10 s – every dashboard status request used to start several
  `systemctl is-active` processes (seen at 20–45 % CPU each on the Pi).
- Live: ◀/▶ use the selected step also with "FM wide". The 100-kHz step is now only preset when
  switching to FM wide (before it was forced, so 89.000 + 5 kHz gave 89.100).

## 1.7.4 – 2026-10-24

- Station map shows reception values: point colour by SNR (blue weak … red strong, with legend), label
  "callsign SNR" next to each point (switchable), SNR in the popup.
- README: map screenshot (FT8 evening on 80–20 m).

## 1.7.3 – 2026-10-24

- Live seek (PMSDR) in a full band (contest): the noise floor is now taken from the speech pauses and
  the quietest spots of the window. Before, the floor sat on the stations themselves, nothing reached
  the threshold, and the seek ran through the whole band and wrapped to the band start (3.500 MHz).
- Seek starts right behind the current station, computed in voice position (LSB below, USB above the
  carrier) – the next station close by is no longer skipped.
- While the window moves, the tuned frequency no longer jumps around.
- Station map larger (70 % of the window height) and resizable at its lower edge; the height is remembered.

## 1.7.2 – 2026-10-24

- Live: tuning is heard almost immediately. The audio could lag several seconds behind (the server
  buffered up to 20 s, the browser had no upper limit, and the two clocks drift apart); now at most
  ~1 s on the server and 0.6 s in the browser – excess is dropped. Retune requests go out after 80 ms
  instead of 250 ms.
- Live: button "■ Stop seek" while seeking – stays on the current station (PMSDR: without restart).

## 1.7.1 – 2026-10-24

- Fix: live shortwave stuttered on every tuning step. The dashboard sends the gain setting with every
  retune; that counted as a change and reopened the PMSDR each time (1–2 s gap, noise burst). Gain is
  ignored for the PMSDR and when unchanged – tuning within the window is now instant as intended.
- Live picks the receiver by frequency: below 30 MHz the PMSDR, from 24 MHz the stick (if connected),
  otherwise the one selected under Scan. A seek started at 7 MHz could run on the stick, which cannot go
  below 24 MHz, and jumped to 24 MHz on its own; outside the stick's range there is now a clear message.
- Live (PMSDR): when tuning leaves the window, the new window lies ahead in tuning direction, so the next
  signals are already visible.
- Live (PMSDR): the status shows when the Pi cannot keep up ("computer busy …") or when the sound card
  lost data ("… data lost").
- **Hit cards: tuning line on the spectrum** like in the live waterfall – yellow line with frequency and
  shaded field (what you hear), moves with the tuning buttons; tap on a signal to tune there (SSB: the
  middle of the voice), mouse wheel for fine tuning (10 Hz, with Shift 100 Hz).

## 1.7.0 – 2026-10-24

- **Live on shortwave (PMSDR) much faster:** tuning within the 48-kHz window only changes the offset
  in software – instant, without reopening the sound card (before: several seconds per click).
- **Seek with the PMSDR:** searches the next occupied channel in the window (SSB voice 2.4 kHz, AM, FM),
  puts the carrier from the edge of the voice (LSB/USB), holds while there is speech, moves the window
  at its edge and wraps at the band edge.
- **Waterfall orientation:** yellow line with frequency on the tuned frequency, shaded field = what you
  hear (USB right of the line, LSB left, AM/FM both sides); frequency under the mouse pointer; tap on a
  signal to tune so that it lies in the field (SSB: tap the middle of the voice); mouse wheel for fine
  tuning (SSB 50 Hz, with Shift 1 kHz).
- SSB: ◀/▶ switch to 1-kHz steps, hint "voice below 10 MHz mostly LSB, above USB".

## 1.6.4 – 2026-10-24

- Shortwave voice watch: candidates without speech pauses are rejected (level varies by less than
  9.5 dB during the dwell, `voice_min_dyn_db`). On quiet bands (15 m at night) noise near the edges of
  the receive window was taken for SSB voice; measured on real recordings: speech 11–33 dB, these false
  hits 1–8 dB.

## 1.6.3 – 2026-10-24

- Scan history: an aborted run without an end time no longer gets the hits of later runs counted
  (it now ends where the next run starts).
- English UI: FT8 summary ("18 msgs, 32 stations, 2× CQ") and carrier correction translated.
- **FT8/FT4 SNR fixed:** the decoder (ft8_lib) only prints a sync score in its SNR column, which
  showed up as +9 … +18 dB for every station – in the hit list, the station list and in the reports
  to PSK Reporter. The SNR is now measured from the recording like WSJT-X does (signal over noise in
  2500 Hz, typically −24 … +10 dB; checked against synthetic FT8 signals of known strength to ±1 dB).

## 1.6.2 – 2026-10-24

- Fix: PMSDR "sound card 'Aureon' not found" on systems with a German locale – `arecord -l` prints
  "Karte 3: …" instead of "card 3: …". arecord is now always called with `LC_ALL=C`, German output is
  recognised as well, and the sound card is also found by its USB ID.
- Clearer message when the sound card is on USB but not registered as a sound card.

## 1.6.1 – 2026-10-24

- Waterfall opens in a window inside the dashboard (title, "↗ original", close with ✕, click
  outside or Esc; Ctrl-click still opens a new tab).
- Waterfall with more contrast (16 dB range), weak lines show more clearly.

## 1.6.0 – 2026-10-24

- **Learned interference lines:** a frequency where a carrier without content (carrier only / no
  audio, no decode, not FM broadcast/APRS/FT8/…) shows up in at least 5 runs over at least 2 hours
  is learned as interference (±3 kHz). Hits there are marked "⚡ interference" and hidden in the hit
  list (checkbox "Interference lines" shows them); they create no station entries or watchlist alerts.
  Local interference detected by the satellite waterfall is added too. New list in the *Stations*
  tab with "not interference" to exclude a frequency permanently.
- **Weather satellites:** NOAA-15/18/19 were decommissioned in 2025; the band plan segment is now
  "Meteor-M LRPT 137.100 / 137.900" (also migrated in existing configs). Image decoding via SatDump
  is still to come.

## 1.5.1 – 2026-10-24

- Hit card shows only the SNR (level and noise were relative values without a fixed reference and
  only confused); spectrum axis labelled "SNR (dB)" – 0 dB is the noise floor.

## 1.5.0 – 2026-10-24

- **Waterfall per satellite pass** (Doppler-corrected, whole recording, beacon and transponder/channel
  marked): link "📊 waterfall" under the result in the pass list. A satellite signal stands as a
  straight line, a transmitter on the ground moves against the Doppler as a curve.
- **Automatic assessment** of the strongest line: "satellite heard (beacon) – line fixed at …" or
  "local interference at 145.9xx MHz (moves against the Doppler)"; needs ≥ 15 s of signal and
  ≥ 1.5 kHz of Doppler change.
- Waterfall images are deleted after 14 days.
- Hit card: "Level · noise · SNR" now consistent. The noise shown came from the scan snapshot, which
  is measured differently from the channel watch, so level minus noise gave odd values (e.g. 81 dB);
  now noise = level − SNR, and the SNR is shown explicitly.
- **SNR everywhere:** heard stations also for APRS/CW/RTTY (SNR of the hit; FT8 per message),
  FM station list (last SNR, best in brackets), watchlist alerts (mail/push/log: "· SNR 21 dB"),
  pass list ("SNR up to … dB"), live status and seek ("SNR 25 dB"), scan mail, FT8 table header.
- Simulator: NORAD 0 = transmitter on the ground (no Doppler) for tests.

## 1.4.8 – 2026-10-24

- Fix (continued): passes recorded after a plan recalculation in the middle of the pass were stored
  under a key from the recalculation time (e.g. AO-7: 13:49 instead of AOS 13:42) and still showed
  "not recorded". The scheduler now keeps passes in progress with their real AOS, and the pass list
  accepts results of the same satellite from anywhere within the pass window.

## 1.4.7 – 2026-10-24

- Fix: the pass list showed "not recorded" for passes that were recorded. Results are stored under
  the pass key (NORAD + minute of AOS); fresh orbital elements shift the AOS by a few seconds, and
  across a minute boundary the key changed, so the result was no longer found. Results are now
  matched to the same satellite within ±3 minutes.

- Signal check of linear satellites now includes their beacon (AO-73 BPSK 145.935, RS-44 CW 435.605,
  AO-7 145.9775, TO-108 145.910, MO-122 435.800; FO-29 is too far from the transponder). The beacon
  sits outside the transponder band and was not measured – AO-73 in daylight sends only its beacon,
  so "no signal" said nothing. Beacons are not counted as transponder activity.

## 1.4.6 – 2026-10-24

- Fix: a satellite pass could be lost without a trace ("not recorded"): the recording was started
  with `flock -n`, which fails if the lock is held for a moment (status check of the dashboard,
  previous pass just releasing the stick). Now it waits up to 30 s, a start that ends at once is
  retried while the pass window is open, and a pass that never got the stick is marked "skipped"
  with the reason (previous pass still recording, scan running, stick busy).
- README: new screenshots of the settings page (Telegram push, watchlist) and of satellite
  priorities with the system status.
- Hints: watchlist mentions push as well as mail; Telegram hint explains that the own bot does not
  answer and points to "Find chat ID".

## 1.4.5 – 2026-10-24

- No scan is started any more that would still be running when a satellite pass begins – now also
  for the image satellites (priority ≤ 2, recorded only when the stick is free), which were almost
  never recorded with short scan intervals. The expected scan duration is the median of the last
  runs. If no scan has run for a long time (30 min or 3 intervals), the scan goes first.
- Scan page: "Next scan" shows "after pass X (hh:mm)" while a scan is held back for a pass.
- Telegram: button "Find chat ID" next to the chat ID field – asks Telegram with the bot token for
  the chats that wrote to the bot and fills in the ID (no @userinfobot needed).

## 1.4.4 – 2026-10-24

- Signal check also for linear satellites (AO-7, AO-73, RS-44, FO-29, CAS-6, MO-122 …): strongest
  spectral line in the transponder (beacon, SSB station) above the noise, per second. Until now
  these passes showed "recorded · 0 hits" without any level, so there was no way to tell whether
  the satellite was heard at all.

## 1.4.3 – 2026-10-24

- README: screenshot of live TMC traffic messages.
- TMC: quantities, lengths and speed limits are formatted with the tables of the ECL ("delays up to
  20 minutes", "12 km", "30 km/h"); a quantity belongs to the event it follows (main or extra event);
  unfilled placeholders like "(from Q lanes)" and editorial notes in braces are removed; extra events
  repeating the main event are dropped; singular/plural in English.
- Live seek with "FM wide" stays within the FM band 87.5–108 MHz and wraps around there.
- Satellite pass list: "show past" shows the 10 most recent past passes instead of the oldest ones;
  result texts translated.

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
