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
# Intel NUC7i5BNK, Iris Plus 640 (Gen9.5 Kaby Lake) with intel-media-va-driver (iHD): HEVC 8/10-bit, VP9, no AV1.
VAINFO_KBL = """libva info: VA-API version 1.20.0
libva info: Trying to open /usr/lib/x86_64-linux-gnu/dri/iHD_drv_video.so
vainfo: Driver version: Intel iHD driver for Intel(R) Gen Graphics - 24.1.0 ()
vainfo: Supported profile and entrypoints
      VAProfileMPEG2Main              : VAEntrypointVLD
      VAProfileH264Main               : VAEntrypointVLD
      VAProfileH264High               : VAEntrypointVLD
      VAProfileH264High               : VAEntrypointEncSliceLP
      VAProfileHEVCMain               : VAEntrypointVLD
      VAProfileHEVCMain               : VAEntrypointEncSlice
      VAProfileHEVCMain10             : VAEntrypointVLD
      VAProfileVP9Profile0            : VAEntrypointVLD
      VAProfileVP9Profile2            : VAEntrypointVLD
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
# The confirmed receiver: a generic 2.4 GHz dongle that presents itself as a wired Xbox 360 pad (xpad).
PAD = '''I: Bus=0003 Vendor=045e Product=028e Version=0114
N: Name="Microsoft X-Box 360 pad"
H: Handlers=event5 js0

'''
GENERIC_PAD = '''I: Bus=0003 Vendor=0079 Product=18d4 Version=0110
N: Name="Generic 2.4G Gamepad"
H: Handlers=event6 js1

'''
FAKE_VENV_PY = """#!/bin/sh
# fake launcher venv python for couch-doctor tests: prints SDL's view from $COUCH_SYSROOT/sdl.json
cat "$COUCH_SYSROOT/sdl.json"
"""
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
    w(root, "vainfo.txt", {"h264only": VAINFO_H264, "nuc7": VAINFO_KBL}.get(variant, VAINFO_HEVC))
    pads = "" if variant in ("nopad", "ms_adapter_noxone", "ms_adapter_unloaded") else PAD
    if variant == "unmapped":
        pads += GENERIC_PAD
    w(root, "proc/bus/input/devices", KBD + pads)
    w(root, "sys/bus/usb/devices/1-2/idVendor", "045e\n")
    w(root, "sys/bus/usb/devices/1-2/idProduct", "02fe\n" if variant.startswith("ms_adapter") else "028e\n")
    for m in ("xpad", "hid_generic", "usbhid"):
        w(root, f"sys/module/{m}/.keep")
    if variant == "ms_adapter_unloaded":
        w(root, "usr/src/xone-0.5.8/.keep")
    if variant in ("unmapped", "sdl"):
        import json as _json
        w(root, "opt/couch/venv/bin/python", FAKE_VENV_PY)
        os.chmod(os.path.join(root, "opt/couch/venv/bin/python"), 0o755)
        devs = [{"name": "Xbox 360 Controller", "guid": "0300b6595e0400008e02000014010000", "mapped": True}]
        if variant == "unmapped":
            # runtime GUID of 0079:18d4 v0110 with a non-zero name-CRC field (as SDL reports it)
            devs.append({"name": "Generic 2.4G Gamepad", "guid": "0300" + "3412" + "7900" + "0000" + "d418" + "0000" +
                         "1001" + "0000", "mapped": False})
        w(root, "sdl.json", _json.dumps(devs))
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
