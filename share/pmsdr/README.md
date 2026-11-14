# PMSDR control program (bundled)

`pmsdr-0.26.tar.gz` is the Linux control program for the PMSDR 2.0/2.1 by Martin Pernter IW3AUT and
Andrea Montefusco IW0HDV, licensed under the **GNU General Public License v3** (see `COPYING` inside the
archive). It is bundled unchanged so that a fresh Pi can build it without downloading it from the author's
site (<https://iw3aut.altervista.org/downloads.htm>), whose certificate is not valid.

SHA-256: `7131124daf136e0b16a10efc50200da484b8078c0faac53d01a96e75a5fca947`

`bin/setup_system.sh` checks this sum, unpacks the archive, applies one correction and builds it:

- `pmsdr.c`: `unsigned int nbytes` → `size_t nbytes`. `getline()` writes a `size_t` (8 bytes on 64-bit
  systems) into that variable; GCC 14 (Debian 13) rejects the original code, older compilers built it with
  a silent memory overwrite.

The program is installed as `/usr/local/bin/pmsdr`; the service `pmsdr-control` creates the command FIFO
`/tmp/PMSDRcommands` before it starts (the program itself does not – without it, it would read from an empty
standard input and stop).
