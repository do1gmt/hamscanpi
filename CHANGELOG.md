# Changelog

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
