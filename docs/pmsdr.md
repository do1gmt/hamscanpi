# PMSDR (shortwave)

The PMSDR is a shortwave receiver by IW3AUT. It delivers I/Q audio through a USB sound card and is
tuned through its USB control board. HamScanPi does not talk to the control board directly – it
sends tuning commands to a **control program** through a FIFO.

## Control program

The author's station uses the Linux command-line program **`pmsdr`** (version 0.26, built from the
source archive `pmsdr-0.26.tar.gz`, installed as `/usr/local/bin/pmsdr`). Started with `-u`, it
reads tuning commands from a FIFO:

```
/usr/local/bin/pmsdr -u /tmp/PMSDRcommands
```

**Download:** the PMSDR software is published by IW3AUT on
<https://iw3aut.altervista.org/downloads.htm> – look for the Linux command-line program; this setup uses
version 0.26 (`pmsdr-0.26.tar.gz`).

> **Certificate warning:** at the time of writing the site's HTTPS certificate is not valid, so
> browsers show a security warning. The page itself is the official source; you can continue
> past the warning (in most browsers: *Advanced → Continue*). Because the connection is then not
> verified, check the downloaded archive before building it – compare its SHA-256 checksum
> (`sha256sum pmsdr-0.26.tar.gz`) with the one below, and do not run anything from it as root
> except the final `make install` step.

SHA-256 of the archive used on the author's station:

```
7131124daf136e0b16a10efc50200da484b8078c0faac53d01a96e75a5fca947  pmsdr-0.26.tar.gz
```

Check it with `echo "7131124daf136e0b16a10efc50200da484b8078c0faac53d01a96e75a5fca947  pmsdr-0.26.tar.gz" | sha256sum -c`
– it must print `pmsdr-0.26.tar.gz: OK`.

Build it from source as described in the archive's README and install the binary to
`/usr/local/bin/pmsdr`. If `install.sh` finds that binary, it offers to set up the service and the
hotplug rule below for you.

### Service

`/etc/systemd/system/pmsdr-control.service`:

```ini
[Unit]
Description=PMSDR Control FIFO
After=network.target

[Service]
Type=simple
User=<your user>
ExecStart=/usr/local/bin/pmsdr -u /tmp/PMSDRcommands
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

## What HamScanPi expects

| Setting in `config/scanner.json` | Meaning | Default |
|---|---|---|
| `fifo` | FIFO the control program reads; HamScanPi writes `f <frequency in Hz>\n` | `/tmp/PMSDRcommands` |
| `alsa_name` | part of the sound card name in `arecord -l` | `Aureon` |
| `alsa_device` | ALSA device number on that card | `0` |
| `sample_rate` | I/Q sample rate | `48000` |
| `iq_conjugate` | swap the spectrum if USB and LSB come out mirrored | `true` |
| `frequency_correction_ppm` | crystal correction, measure it on a known carrier | `0` |
| `pmsdr_soundcard_usb` | USB IDs of the sound card (`["0ccd:00b1"]`); `[]` = detect by `alsa_name` | Aureon 7.1 USB |

The control program must run as a service named **`pmsdr-control`**: the dashboard shows
“steckt – Steuerung läuft nicht” when the PMSDR is plugged in but this service is not active.

## Hotplug (optional)

With this udev rule the control service starts when the PMSDR is plugged in and stops when it is
unplugged (`/etc/udev/rules.d/90-hamscanpi-pmsdr.rules`):

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
