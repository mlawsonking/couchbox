#!/usr/bin/env python3
"""List files written after REFERENCE that are neither package-owned (dpkg), nor package/system-generated, nor on
the installer's allow-list (BRIEF.md guardrail 4). Exit 1 if any remain.  stray_writes.py REFERENCE_FILE"""
import glob
import os
import re
import sys

ref = os.stat(sys.argv[1]).st_mtime
ALLOW = re.compile(r"^/(opt/couch(/|$)|etc/couch(/|$)|var/lib/couch(/|$)|var/log/couch(/|$)|home/couchadmin(/|$)"
                   r"|etc/systemd/system/couch-[^/]+$|etc/systemd/system/[^/]+\.wants/couch-[^/]+$"
                   r"|etc/systemd/system/getty@tty1\.service$|etc/systemd/journald\.conf\.d/couch\.conf$"
                   r"|etc/apt/apt\.conf\.d/(52couch-unattended-upgrades|21couch-auto-upgrades)$"
                   r"|etc/modprobe\.d/couch-xone-blacklist\.conf$|usr/src/(xone|hid-xpadneo)-|lib/firmware/xone_dongle_)")
# Written by apt/dpkg/maintainer scripts/user database updates as a consequence of installing the named packages.
SYSTEM = re.compile(r"^/(var/lib/(apt|dpkg|systemd|ucf|python|pam|polkit-1|dbus|shells\.state|sgml-base|xml-core"
                    r"|bluetooth|avahi-autoipd|alsa|dhcp|private|fonts|vim)|var/cache|var/log/(apt|dpkg|alternatives"
                    r"|bootstrap|journal)|var/log/[^/]+\.log$|etc/(passwd|group|shadow|gshadow|subuid|subgid)-?$"
                    r"|etc/(ld\.so\.cache|machine-id|localtime|timezone|modules|mtab|vconsole\.conf)$|var/log/README$|etc/alternatives/|etc/ssl/|etc/ca-certificates|etc/fonts/"
                    r"|etc/systemd/(system|user)/[^/]+\.(wants|requires)/|etc/systemd/(system|user)/[^/]+$"
                    r"|etc/rc[0-6S]\.d/|etc/init\.d/|etc/xml/|etc/sgml/|etc/default/|etc/apt/apt\.conf\.d/(20|50)"
                    r"|etc/dconf/|etc/mime\.types|etc/avahi/|etc/dbus-1/|etc/udev/|etc/X11/|etc/python3|usr/|lib/"
                    r"|etc/apparmor\.d/|etc/sudoers|etc/environment|etc/shells$|etc/perl|etc/vulkan|etc/OpenCL"
                    r"|etc/nsswitch\.conf$|etc/profile\.d/|etc/bash_completion\.d|etc/logrotate\.d/|etc/cron"
                    r"|etc/modprobe\.d/|etc/sensors|etc/security/|etc/pam\.d/|etc/libibverbs|etc/libnl|etc/gai)")
SKIP_TREES = ("/proc", "/sys", "/dev", "/run", "/tmp", "/work", "/src", "/root")

owned = set()
for lst in glob.glob("/var/lib/dpkg/info/*.list"):
    with open(lst, encoding="utf-8", errors="replace") as fh:
        owned.update(line.rstrip("\n") for line in fh)

stray = []
for top, dirs, files in os.walk("/", topdown=True):
    if top in SKIP_TREES or any(top.startswith(t + "/") for t in SKIP_TREES):
        dirs[:] = []
        continue
    for name in files:
        path = os.path.join(top, name)
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if st.st_mtime < ref or path in owned or ALLOW.match(path) or SYSTEM.match(path):
            continue
        stray.append(path)
if stray:
    print("written outside the installer allow-list:")
    print("\n".join(sorted(stray)))
    sys.exit(1)
print("no stray writes")
