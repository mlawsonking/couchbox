#!/usr/bin/env python3
"""Build a fake NUC sysroot for couch-doctor tests.  make_sysroot.py DIR VARIANT
Variants: good (TV + dongle pad + HEVC), nodisplay, nopad, dongle_nodriver, h264only, nogpu."""
import os
import sys

VAINFO_HEVC = """libva info: VA-API version 1.20.0
vainfo: Driver version: Intel iHD driver for Intel(R) Gen Graphics - 24.1.0 ()
vainfo: Supported profile and entrypoints
      VAProfileH264Main               : VAEntrypointVLD
      VAProfileH264High               : VAEntrypointVLD
      VAProfileH264High               : VAEntrypointEncSliceLP
      VAProfileHEVCMain               : VAEntrypointVLD
      VAProfileHEVCMain10             : VAEntrypointVLD
      VAProfileAV1Profile0            : VAEntrypointVLD
"""
VAINFO_H264 = """vainfo: Driver version: Intel i965 driver for Intel(R) Haswell - 2.4.1
      VAProfileH264Main               : VAEntrypointVLD
      VAProfileH264High               : VAEntrypointVLD
"""
PING_GOOD = """PING 10.0.0.9 (10.0.0.9) 56(84) bytes of data.
64 bytes from 10.0.0.9: icmp_seq=1 ttl=128 time=3.10 ms
64 bytes from 10.0.0.9: icmp_seq=2 ttl=128 time=3.40 ms
64 bytes from 10.0.0.9: icmp_seq=3 ttl=128 time=2.90 ms
64 bytes from 10.0.0.9: icmp_seq=4 ttl=128 time=3.20 ms

--- 10.0.0.9 ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 603ms
"""
PING_BAD = """64 bytes from 10.0.0.9: icmp_seq=1 ttl=128 time=4.0 ms
64 bytes from 10.0.0.9: icmp_seq=2 ttl=128 time=48.0 ms
64 bytes from 10.0.0.9: icmp_seq=3 ttl=128 time=5.0 ms
64 bytes from 10.0.0.9: icmp_seq=5 ttl=128 time=61.0 ms

--- 10.0.0.9 ping statistics ---
5 packets transmitted, 4 received, 20% packet loss, time 803ms
"""
PAD = '''I: Bus=0003 Vendor=045e Product=0b12 Version=0509
N: Name="Microsoft Xbox Controller"
H: Handlers=event5 js0

'''
KBD = '''I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
H: Handlers=kbd event0

'''


def w(root, rel, text=""):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def build(root, variant="good"):
    connected = variant != "nodisplay"
    w(root, "sys/class/drm/card1-HDMI-A-1/status", "connected\n" if connected else "disconnected\n")
    w(root, "sys/class/drm/card1-HDMI-A-1/modes", "3840x2160\n1920x1080\n1280x720\n" if connected else "")
    w(root, "sys/class/drm/card1-DP-1/status", "disconnected\n")
    if variant != "nogpu":
        w(root, "dev/dri/renderD128")
    w(root, "vainfo.txt", VAINFO_H264 if variant == "h264only" else VAINFO_HEVC)
    w(root, "proc/bus/input/devices", KBD + ("" if variant in ("nopad", "dongle_nodriver") else PAD))
    w(root, "sys/bus/usb/devices/1-2/idVendor", "045e\n")
    w(root, "sys/bus/usb/devices/1-2/idProduct", "02fe\n")
    if variant != "dongle_nodriver":
        for m in ("xone_dongle", "xone_wired", "hid_xpadneo"):
            w(root, f"sys/module/{m}/.keep")
        w(root, "lib/firmware/xone_dongle_02fe.bin", "fw")
    w(root, "sys/class/bluetooth/hci0/.keep")
    w(root, "systemctl.state", "active enabled masked\n")
    w(root, "var/lib/couch/.keep")
    w(root, "sys/class/net/lo/operstate", "unknown\n")
    w(root, "sys/class/net/eno1/operstate", "up\n" if variant != "nonet" else "down\n")
    w(root, "sys/class/net/wlp2s0/operstate", "down\n")
    w(root, "sys/class/net/wlp2s0/wireless/.keep")
    w(root, "ping.txt", PING_BAD if variant == "jittery" else PING_GOOD)
    w(root, "etc/couch/couch.toml", '[host]\nname = "localhost"\nmac = "AA:BB:CC:DD:EE:FF"\n')


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "good")
