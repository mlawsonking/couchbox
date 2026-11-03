"""Minimal sd_notify(3) for Type=notify units and WatchdogSec (stdlib only)."""
from __future__ import annotations

import os
import socket


def notify(state: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(state.encode())
        return True
    except OSError:
        return False


def watchdog_interval() -> float | None:
    """Seconds between WATCHDOG=1 pings (half of WatchdogSec), or None when no watchdog is configured."""
    usec = os.environ.get("WATCHDOG_USEC")
    if not usec or (os.environ.get("WATCHDOG_PID") and os.environ["WATCHDOG_PID"] != str(os.getpid())):
        return None
    return int(usec) / 2e6
