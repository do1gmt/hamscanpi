#!/usr/bin/env python3
"""Meldung an systemd (sd_notify) ohne Zusatzpaket: READY=1, WATCHDOG=1, STATUS=…
Ohne NOTIFY_SOCKET (von Hand gestartet) passiert nichts."""
import os
import socket


def notify(msg):
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(msg.encode())
        return True
    except OSError:
        return False


def watchdog_enabled():
    return bool(os.environ.get("WATCHDOG_USEC")) and bool(os.environ.get("NOTIFY_SOCKET"))
