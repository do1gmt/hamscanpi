# PMSDR (shortwave)

The PMSDR is a shortwave receiver by IW3AUT. It delivers I/Q audio through a USB sound card and is
tuned through its USB control board. Hesperos DX does not talk to the control board directly – it
sends tuning commands to a **control program** through a FIFO.

## Control program

Hesperos DX uses the Linux command-line program **`pmsdr`** (version 0.26, GPL-3, by IW3AUT and IW0HDV).
**You do not have to build it yourself:** the source archive is bundled in `share/pmsdr/`, and the
installer (and every update) builds and installs it as `/usr/local/bin/pmsdr` if it is missing – after
checking its SHA-256 sum (`7131124daf136e0b16a10efc50200da484b8078c0faac53d01a96e75a5fca947`) and applying
one correction (`size_t` for `getline()` – GCC 14 on Debian 13 rejects the original, older compilers built it
with a silent memory overwrite on 64-bit systems). Build packages: `build-essential`, `libusb-1.0-0-dev`,
`libreadline-dev` (installed by the set-up).

The program reads tuning commands (`f <frequency in Hz>`) from a FIFO, `/tmp/PMSDRcommands` by default
(environment variable `PMSDR_FILE`). It does not create the FIFO itself – the service does that before it
starts. Note: `-u` is *not* the FIFO but a UDP port option of this program.

### Service (set up automatically)

`/etc/systemd/system/pmsdr-control.service`:

```ini
[Unit]
Description=PMSDR Control FIFO
After=network.target

[Service]
Type=simple
User=<your user>
Environment=PMSDR_FILE=/tmp/PMSDRcommands
ExecStartPre=/bin/sh -c '[ -p "/tmp/PMSDRcommands" ] || { rm -f "/tmp/PMSDRcommands"; mkfifo -m 660 "/tmp/PMSDRcommands"; }'
ExecStart=/usr/local/bin/pmsdr
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

## What Hesperos DX expects

| Setting in `config/scanner.json` | Meaning | Default |
|---|---|---|
| `fifo` | FIFO the control program reads; Hesperos DX writes `f <frequency in Hz>\n` | `/tmp/PMSDRcommands` |
| `alsa_name` | part of the sound card name in `arecord -l` | `Aureon` |
| `alsa_device` | ALSA device number on that card | `0` |
| `sample_rate` | I/Q sample rate | `48000` |
| `iq_conjugate` | swap the spectrum if USB and LSB come out mirrored | `true` |
| `frequency_correction_ppm` | crystal correction, measure it on a known carrier | `0` |
| `pmsdr_soundcard_usb` | USB IDs of the sound card (`["0ccd:00b1"]`); `[]` = detect by `alsa_name` | Aureon 7.1 USB |

The control program must run as a service named **`pmsdr-control`**: the dashboard shows “plugged in – control
program missing” when `/usr/local/bin/pmsdr` does not exist (install the update) and “plugged in – control not
running” when the service is not active (unplug the PMSDR and plug it in again).

## Hotplug (set up automatically)

With this udev rule the control service starts when the PMSDR is plugged in and stops when it is
unplugged (`/etc/udev/rules.d/90-hesperosdx-pmsdr.rules`):

```
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="04d8", ATTR{idProduct}=="000c", MODE="0666", TAG+="systemd", ENV{SYSTEMD_ALIAS}="/sys/subsystem/usb/pmsdr", ENV{SYSTEMD_WANTS}+="pmsdr-control.service"
```

and in `/etc/systemd/system/pmsdr-control.service.d/hotplug.conf`:

```ini
[Unit]
BindsTo=sys-subsystem-usb-pmsdr.device
After=sys-subsystem-usb-pmsdr.device

[Service]
RestartSec=5
```

Then `sudo systemctl daemon-reload && sudo udevadm control --reload-rules`. With hotplug the service
is not enabled at boot – udev starts it when the PMSDR is plugged in.

## Status

The PMSDR path is used daily on the author's station but has only been set up there. If you get it
running with another control program or sound card, please open an issue or a pull request with
your settings.
