<img src="web/icon.svg" width="96" alt="HamScanPi icon" align="right">

# HamScanPi

*Hams can Pi.*

[![selftest](https://github.com/do1gmt/hamscanpi/actions/workflows/selftest.yml/badge.svg)](https://github.com/do1gmt/hamscanpi/actions/workflows/selftest.yml)

**An unattended band scanner and satellite recorder for radio amateurs, running on a Raspberry Pi.**

HamScanPi scans the amateur bands on its own, records what it hears, decodes what it can and shows
everything in a web dashboard you open on your phone or PC. When an amateur satellite passes overhead
it interrupts the scan, follows the Doppler shift and records the pass – including SSTV images from
the ISS and from CubeSats.

It works with an RTL-SDR stick (VHF/UHF, satellites) and optionally with a
PMSDR receiver (IW3AUT) for shortwave.

![Scan page: bands, satellite passes and scan status](docs/screenshots/scan.png)

> **Status:** works daily on the author's station (Raspberry Pi, RTL-SDR, PMSDR).
> The dashboard speaks **English and German** – switch under *Settings → Station*. Missing
> translations live in `web/i18n.js`; pull requests are welcome.

## What it does

**Band scanning**
- Scans the bands you select (160 m … 23 cm, plus 11 m CB, PMR446, Freenet, broadcast, weather satellites).
  Bands that may not be monitored in many countries are disabled by default – see [Legal](#legal).
  The band plan follows IARU Region 1 and lives in `config/scanner.json`.
- Every hit is stored with its I/Q recording, spectrum and audio. Listen in the browser, retune in
  10 Hz steps (SSB), switch sideband, apply noise reduction, add notes, protect hits from clean-up.
- Decoders: **FT8/FT4** (via [ft8_lib](https://github.com/kgoba/ft8_lib)), **CW**, **RTTY**,
  **APRS** and **POCSAG** (via multimon-ng), **SSTV** on shortwave (14.230, 21.340, 28.680 USB,
  3.730 and 7.165 LSB) – including images whose start was missed and stations that are off-frequency.
- Live listening with waterfall (tap to tune), seek function, signal-to-noise display and overload warning.
- FM broadcast with RDS: station name, radio text, artist/title (RT+), clock, alternative frequencies –
  live and for every scanned station (list of FM stations).
- TMC traffic messages from RDS in plain text (road, direction, between which junctions, event,
  duration) using the German location table – live, on hit cards and as watchlist alerts ("A9").
- Band schedules: other bands by time of day and weekday.
- On repeaters and FM channels the scanner stays until the QSO ends and records it as a whole.

**Stations and alerts**
- *Stations* tab: every transmitting callsign from FT8/FT4, APRS, CW and RTTY with locator, distance,
  bearing and last heard – exportable as an ADIF SWL log.
- Watchlist: alerts for a frequency, a callsign (`DL*`), DX beyond N km or events such as SSTV from
  the ISS – in the log, on the hit card, by mail or as push to your phone (ntfy, Telegram).
- Map of heard stations, activity overview by band and hour, optional reporting of FT8/FT4 spots
  to [PSK Reporter](https://pskreporter.info).

**Satellites (RTL-SDR)**
- Switched on and off with the band chip *Satellites*; passes are only recorded while the RTL-SDR is the
  selected receiver – the selected receiver stands for the antenna that is connected (one antenna
  at a time, e.g. via an antenna switch). With the chip off, satellites never interrupt a scan.
- Orbital elements from CelesTrak (fallback: AMSAT), pass prediction with SGP4 from your locator.
- Recording starts 15 s before AOS; the Doppler shift is corrected every 0.1 s per channel.
- ISS: SSTV on 437.550 and 145.800 MHz, the 437.800 FM repeater, the 145.825 APRS digipeater.
- FM and linear-transponder satellites (SO-50, AO-123, AO-91, PO-101, AO-7, FO-29, RS-44, MO-122, …)
  and SSTV CubeSats (UmKA-1, VIZARD-meteo, Lobachevsky, QMR-KWT-2, SONATE-2, …).
- SSTV decoder: Robot 36/72, Martin 1/2, Scottie 1/2/DX, PD 90–290.
- **Scheduling:** when passes overlap, a score decides (priority × 10, elevation, time since the
  satellite was last recorded, and a penalty for satellites that stayed silent for many passes).
  If the frequencies of simultaneous passes fit into one receive window, up to three satellites are
  recorded **together**, each with its own Doppler correction.

**Dashboard**
- Scan, hits, live, log and settings – mobile first, no page reloads.
- Scan history with paging, a readable event log, scan duration estimate, adjustable scan raster,
  satellite priorities, mail notifications.

## Screenshots

| | |
|---|---|
| ![A hit with spectrum, audio player and APRS decode](docs/screenshots/hits.png) | ![Live listening with waterfall](docs/screenshots/live.png) |
| Hit card: spectrum, audio with tuning and noise reduction, decoded APRS packets | Live listening: FM broadcast with RDS (station, programme type, RT+, AF) and waterfall |
| ![Readable event log](docs/screenshots/log.png) | ![Phone: satellite passes and live listening (German UI)](docs/screenshots/mobile.png) |
| Event log with the monitored frequencies of every scan | On the phone (here with the German UI) |
| ![Settings: station, watchlist, notifications](docs/screenshots/settings.png) | ![Satellite priorities and system status](docs/screenshots/satellites.png) |
| Settings: station (ADIF, PSK Reporter), watchlist, mail and push notifications | Satellite priorities with the last 14 days, system status of the Pi |

![System: notices, system values and history, status, devices](docs/screenshots/system.png)
System page: notices with what to do, live values and 24-hour history (CPU, memory/zram, temperature),
status (storage, write load, swap, services, version) and the device register – here an NESDR SMArt v5 that
also covers HF via direct sampling.

![Stations: heard callsigns with distance and bearing, FM stations with RDS](docs/screenshots/stations.png)
*Stations: callsigns heard via APRS/FT8/CW with locator, distance and bearing (ADIF export); FM stations with RDS name, PI, programme type and radio text.*

![Map of heard stations: FT8 on 80/60/40/30/20 m from JN59, up to the US east coast](docs/screenshots/map.png)
*Map of heard stations (one evening of FT8 scans on 80–20 m with an HF-360 vertical): lines from the own locator, up to 7,900 km; point colour = SNR, border = mode.*

<img src="docs/screenshots/tmc.png" width="560" alt="Live FM reception with RDS and TMC traffic messages">

*Live FM reception with RDS and TMC traffic messages in plain text (German location table of BASt).*

## Hardware

| Part | Notes |
|---|---|
| Raspberry Pi 4 or 5 | 2 GB RAM or more. A Pi 3 works for scanning, satellite recording with several channels may be too slow. |
| RTL-SDR stick | any RTL2832U stick (RTL-SDR Blog V3/V4, Nooelec NESDR, …) – VHF/UHF and satellites |
| Antenna | a discone or a vertical works for strong passes; a turnstile or QFH is better for satellites |
| PMSDR (optional) | shortwave 160–10 m, needs a USB sound card for I/Q and its control program – see [docs/pmsdr.md](docs/pmsdr.md) |

**Band plan by country:** *Settings → Station → Country / band plan* selects the band plan used for scanning and
classifying hits. **Germany (IARU Region 1)** is the default and unchanged. **Austria**, **Switzerland** and the **United Kingdom** (Region 1) and **Canada** (Region 2) are
available as well (Freenet only in Germany, no 4 m in Austria, UK 4 m 70.0–70.5 MHz, UK 60 m allocations and CB
27/81, Canada like the US without MURS and with 70 cm 430–450 MHz). For the US and Canada FM broadcast uses
**RBDS** (North American programme types, station call sign from the PI code). **USA (IARU Region 2)** follows the
ARRL band plan: 160–10 m with US edges (80 m to 4.0, 40 m to 7.3 MHz), the 60 m channels and the 5351.5–5366.5 kHz
segment, 6 m to 54 MHz, 2 m to 148 MHz with APRS on 144.390, simplex 146.520 and repeaters with −600/+600 kHz,
1.25 m, 70 cm 420–450 MHz with ±5 MHz repeaters, 33 cm, 23 cm repeaters, FRS/GMRS, MURS, NOAA Weather Radio,
ISM 915 MHz, FM broadcast 87.9–107.9 MHz, and the airband as a normal band. The locator suggests a country. The
profile is a data file (`bandplans/us.json`, overlay over `config/scanner.json`, which stays unchanged); more
countries can be added the same way – pull requests welcome.

**Shortwave without a PMSDR:** sticks with direct sampling on the Q branch (Nooelec NESDR SMArt v5, RTL-SDR
Blog V3) also receive 100 kHz – 28.8 MHz. HamScanPi recognises them (*System → Devices*: "HF via direct
sampling"), switches direct sampling on below 24 MHz (Live and scan; the driver's syntax `-D` / `-D 2` /
`-E direct2` is detected automatically) and then offers the HF bands for the stick as well – the same voice,
FT8/FT4, CW/RTTY and SSTV detection as with the PMSDR. There is no gain control in this mode, and an HF antenna
(long wire, dipole) has to be connected to the stick. The RTL-SDR Blog V4 needs the Blog driver for HF.

## Installation

```bash
git clone https://github.com/do1gmt/hamscanpi.git
cd hamscanpi
./install.sh            # optional: ./install.sh 8081 for another port
```

The installer sets up the packages, the RTL-SDR driver blacklist, the local configuration and two
systemd services (`hamscanpi-web`, `hamscanpi-scheduler`). Then open `http://<pi-address>:8080/`
and enter your locator under *Settings → Station*.

Update later with `bin/update.sh` – it pulls the new version, completes your local configuration and
restarts the services. The dashboard shows a notice under *System → Status* when a new version is out.
Changes are listed in [CHANGELOG.md](CHANGELOG.md).

### Files

| Path | Content |
|---|---|
| `config/scanner.default.json` | default band plan and receiver settings (copied to `config/scanner.json` on first start) |
| `config/scanner.json` | your local copy – PPM correction, gain, sound card, `allow_restricted_bands`; HF receiver name and antenna are set under *System → Devices* (stored here as `device_labels`/`antennas`), the antenna of each RTL stick in its device entry |
| `config/web_settings.json` | dashboard settings, locator, mail account (**contains your SMTP password, never commit it**) |
| `config/auth.json` | dashboard password (hashed) and trusted networks – never committed |
| `data/`, `recordings/`, `reports/`, `log/` | database, I/Q recordings, images and decodes, logs |

## SD card or SSD

HamScanPi itself runs the same on both – same paths, same behaviour. The difference is wear, speed and power.

HamScanPi runs around the clock and writes recordings, its database, the system history and logs.
*System → Status* shows how much was written to the system drive in the last 24 hours (and the daily
average), whether it is an SD card, USB drive or NVMe SSD, and where the swap lives.

| | SD card | SSD (USB) |
|---|---|---|
| Wear from continuous writing | the weak point – a normal card lasts roughly 1–3 years of 24/7 use, *endurance* cards (dashcam grade) much longer | many times more durable |
| Speed (database, evaluation, hit list, boot) | adequate | noticeably faster |
| Power loss | often loses its file system | usually survives better |
| Power draw | none extra | extra from the Pi's USB – with RTL stick, PMSDR and sound card use the official 5.1 V/3 A supply or a powered adapter/hub; *System → Status* reports undervoltage |
| Radio interference | none | USB 3 radiates broadband noise: SSD on a blue USB 3 port with a short, well-shielded cable, RTL stick on a black USB 2 port with an extension, as far away as possible – then check the noise floor in *Live* |

A few hundred MB written per day is fine for a good card for years; several GB per day make an SSD
worthwhile.

### Swap

Raspberry Pi OS Trixie already uses **zram** (swap compressed in RAM) – nothing is swapped to the card:

```
swapon --show            # /dev/zram0 only? Then zram is active – nothing to do.
```

On older systems with a swap file on the card, install zram with the swap file kept as a low-priority reserve
(only written when zram is full):

```
sudo apt install -y zram-tools
printf 'ALGO=zstd\nPERCENT=50\nPRIORITY=100\n' | sudo tee /etc/default/zramswap
sudo systemctl restart zramswap
swapon --show            # /dev/zram0 with priority 100, the swap file below it
```

If zram is full and there is no reserve, the kernel has to end a process – the system history shows the
largest process per minute to find the cause.

### Backup and restore

*System → Backup* downloads everything important as one file – settings; optionally with the data (hits,
stations, FM stations, interference lines, system history) and the recordings (audio, I/Q, waterfalls,
images, can be large). The databases are copied consistently even while a scan is writing. *Include
credentials* (on by default) also stores the mail password and Telegram/ntfy token in plain text – keep the
file safe. To restore, upload the file in the same card: HamScanPi shows what is in it, restores as soon as
no scan or pass is running, moves the current settings and data to `restore-previous/<time>/` first, adds
recordings (never deletes them) and restarts its services.

**Automatic backup:** in the same card, a backup can be written daily or weekly at a chosen hour to a USB stick
(appears under `/media/<user>/…`) or a mounted network share (e.g. `/mnt/nas`, mounting example in the card),
keeping the last N files. A target on the SD card itself is refused – it would not help if the card fails.
A failed backup becomes a notice.

From the command line, e.g. after losing the card (fresh install, then):

```
python3 bin/backup.py create --scope data          # settings | data | all, --no-secrets, -o file
python3 bin/backup.py info hamscanpi-backup-….tar.gz
python3 bin/backup.py restore hamscanpi-backup-….tar.gz
sudo systemctl restart hamscanpi-scheduler hamscanpi-web
```

### Moving to an SSD

Boot the **whole system** from the SSD (Raspberry Pi 4 and 5 can do this directly) rather than putting only
HamScanPi on it – then logs and everything else live on the SSD too, and the services never start before
their drive is mounted.

1. Write Raspberry Pi OS to the SSD with the Raspberry Pi Imager (same user name as before, e.g. `gmt`).
2. Shut the Pi down, remove the SD card, connect the SSD to a blue USB 3 port and boot.
3. Install HamScanPi as described under *Installation* (`install.sh`).
4. Stop the services and copy your data from the old card (in a USB card reader, here mounted at `/media/$USER/rootfs`):
   ```
   sudo systemctl stop hamscanpi-scheduler hamscanpi-web
   OLD=/media/$USER/rootfs/home/$USER/hamscanpi
   cp -a $OLD/config/. ~/hamscanpi/config/       # settings, band plan, passwords, Telegram
   cp -a $OLD/data/. ~/hamscanpi/data/           # database, stick register, history, satellite results
   cp -a $OLD/recordings $OLD/reports ~/hamscanpi/   # only if you want to keep the old hits
   sudo systemctl start hamscanpi-scheduler hamscanpi-web
   ```
5. Check *System → Devices* (main stick still recognised by its serial number) and *System → Status*
   (drive now "USB drive").

Keep the old card as an emergency spare – put it back in and the Pi starts as before.

## Access protection

By default the dashboard is open to everyone who can reach it. Set a password under
*Settings → Access protection* (or during `./install.sh`). Then every device has to log in once
(session cookie, 30 days); after 5 wrong passwords an address is blocked for 5 minutes. You can
list networks that need no login, e.g. your home network `192.168.178.0/24`. The Pi itself
(localhost) never needs to log in.

Forgot the password? On the Pi: `python3 bin/auth.py set` (or `python3 bin/auth.py off`).

> If you make the dashboard reachable from the internet, put it behind HTTPS (e.g. a reverse proxy
> or a VPN such as WireGuard/Tailscale). The built-in server speaks plain HTTP. Behind a reverse
> proxy every request comes from the proxy's address, so do not list that address as trusted.

## Data sources

- Orbital elements: [CelesTrak](https://celestrak.org) (fallback: AMSAT).
- TMC traffic messages: *Location Code List* LCL 22.0 and *Event Code List* ECL 4.01 of the
  Bundesanstalt für Straßenwesen (BASt), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/);
  converted to `share/tmc/` (see the README there).

## Legal

HamScanPi only receives. Which transmissions you may listen to, record and decode depends on the
law of your country.

### Restricted bands

Bands whose reception is forbidden in many countries – such as the **airband** – are marked
`"legal": "restricted"` in the band plan. **By default they are switched off completely:** they do
not appear in the dashboard, are never scanned, live listening and the seek function skip them,
and old hits from these bands are hidden.

Only where the law of your country allows it, you can enable them in `config/scanner.json`:

```json
"allow_restricted_bands": true
```

They then appear under *more* (German UI: *weitere*), marked with ⚠. In **Germany** listening to the airband is not
allowed (§ 5 TDDDG, formerly TTDSG) – leave the setting at `false` there. You are responsible for
the bands you enable.

## Testing without hardware

`devtools/selftest.sh` runs the satellite and SSTV chain against simulated signals (ISS pass with
Doppler, three satellites in one recording, APRS, shortwave SSTV) in a temporary copy – your own
installation is not touched. See [docs/testing.md](docs/testing.md).

## Credits

- Pass prediction: [sgp4](https://pypi.org/project/sgp4/) · orbital elements: [CelesTrak](https://celestrak.org), [AMSAT](https://www.amsat.org)
- FT8/FT4: [ft8_lib](https://github.com/kgoba/ft8_lib) by Karlis Goba, YL3JG
- APRS/POCSAG: [multimon-ng](https://github.com/EliasOenal/multimon-ng)
- Satellite frequencies and status: AMSAT, ARISS, SatNOGS DB

## License

GNU General Public License v3.0 – see [LICENSE](LICENSE).
