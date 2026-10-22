# PMSDR (shortwave)

The PMSDR is a shortwave receiver by IW3AUT. It delivers I/Q audio through a USB sound card and is
tuned through its USB control board. HamScanPi does not talk to the control board directly – it
sends tuning commands to a **control program** through a FIFO.

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

```
[Unit]
BindsTo=sys-subsystem-usb-pmsdr.device
After=sys-subsystem-usb-pmsdr.device
```

## Status

The PMSDR path is used daily on the author's station but has only been set up there. If you get it
running with another control program or sound card, please open an issue or a pull request with
your settings.
