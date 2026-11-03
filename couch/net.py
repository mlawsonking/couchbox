"""LAN helpers shared by the launcher and the daemon (stdlib only, IPv4 only: IPv6 is broken on this LAN)."""
from __future__ import annotations

import re
import socket

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
SUNSHINE_HTTP_PORT = 47989     # Sunshine's HTTP (pairing/serverinfo) port; 47984 is HTTPS


def mac_bytes(mac: str) -> bytes:
    if not MAC_RE.match(mac or ""):
        raise ValueError(f"not a MAC address: {mac!r}")
    return bytes.fromhex(re.sub(r"[:-]", "", mac))


def magic_packet(mac: str) -> bytes:
    """Wake-on-LAN magic packet: 6 x 0xFF then the MAC 16 times (102 bytes)."""
    return b"\xff" * 6 + mac_bytes(mac) * 16


def send_wol(mac: str, broadcast: str = "255.255.255.255", port: int = 9, sock_factory=socket.socket) -> bytes:
    pkt = magic_packet(mac)
    s = sock_factory(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(pkt, (broadcast, port))
    finally:
        s.close()
    return pkt


def resolve4(host: str, timeout: float = 2.0) -> str | None:
    """IPv4 address for a host/mDNS name via the system resolver (nss-mdns handles *.local), or None."""
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
        return infos[0][4][0] if infos else None
    except (socket.gaierror, OSError):
        return None
    finally:
        socket.setdefaulttimeout(old)


def tcp_probe(ip: str, port: int = SUNSHINE_HTTP_PORT, timeout: float = 1.0) -> str:
    """'open' (Sunshine listening), 'refused' (host is up, nothing listening) or 'timeout' (asleep/unreachable)."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return "open"
    except ConnectionRefusedError:
        return "refused"
    except OSError:
        return "timeout"
