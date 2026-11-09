#!/usr/bin/env python3
"""Regenerate docs/FAILURE-SCREENS.md from couch/launcher/state.py REASONS (tests fail if the doc drifts)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from couch.launcher.state import REASONS  # noqa: E402

WHEN = {
    "WAKE_TIMEOUT": ("WAKING for 120 s without the PC answering (WoL resent every 10 s)",
                     "PC unplugged/off at the wall; WoL off in BIOS or NIC; Fast Startup on; wrong MAC in couch.toml; PC on Wi-Fi only",
                     "Run `Check-Sunshine.ps1` on the PC; `Set-WakeOnLan.ps1 -Apply`; check the BIOS setting; compare the MAC it prints with `/etc/couch/couch.toml`."),
    "SUNSHINE_TIMEOUT": ("the PC answers but Sunshine's port 47989 stays closed for 60 s",
                         "Sunshine service stopped/crashed; firewall rule missing; Sunshine still starting after a Windows update",
                         "On the PC: `Check-Sunshine.ps1`; restart SunshineService; `Setup-Sunshine.ps1 -Apply` re-adds missing rules."),
    "STREAM_FAILED": ("moonlight-qt exited with an error, or logged a fatal connection message (\"No video received from "
                      "host\", \"Connection terminated\", ...) in the first 25 s, 3 times in a row; each failed attempt is stopped "
                      "(its Sunshine session ended too) and retried after 2 s. The line shown is the real reason",
                      "the app name in couch.toml no longer exists in Sunshine; the game crashes on launch; decoder/display mode not supported; the PC's video capture did not start (no UDP video from the host: firewall / ports 47998, 48000)",
                      "`journalctl -u couch-kiosk`, `/var/log/couch/moonlight.log`; try the Desktop tile; lower `[stream]` to `1080p60`."),
    "STREAM_LOST": ("a stream that ran > 15 s ended with an error",
                    "the PC went to sleep or rebooted; Wi-Fi/mesh drop between garage and living room; PC network unplugged",
                    "Retry. If it repeats: `couch-doctor` network line (jitter/loss); consider a lower quality rung."),
    "HOST_GONE": ("while streaming, the PC stopped answering for 30 s and moonlight-qt did not notice",
                  "network path down; PC hard-hung",
                  "Retry; check the PC; `couch-doctor --measure` for the link."),
    "NOT_PAIRED": ("moonlight-qt reports it is not paired with the PC",
                   "first use; Sunshine's paired-clients list was reset; the NUC was reinstalled",
                   "Start menu > Pair, type the PIN in Sunshine's web page on the PC (https://localhost:47990 > PIN)."),
    "PAIR_TIMEOUT": ("the PIN screen waited 180 s", "nobody typed the PIN on the PC", "Start > Pair again."),
    "PAIR_FAILED": ("moonlight-qt pair exited with an error", "wrong PIN; PC asleep; Sunshine not running",
                    "Wake the PC, check Sunshine, Pair again."),
    "NO_HOST": ("couch.toml has no usable host", "fresh install with the example config; typo",
                "Edit `/etc/couch/couch.toml` (`[host] name`, `mac`); the launcher reloads it within a second."),
}


def render() -> str:
    out = ["# Failure screens", "",
           "Every failure shows big **kid words** on the TV, **A Try again / B Home**, and a small **For grown-ups** line "
           "with the details. This file is generated from `couch/launcher/state.py` by `scripts/gen-failure-screens.py`; "
           "a test fails if they drift apart. Screenshots: `tests/goldens/1080p/lost.png` (and every other screen).", "",
           "Other signals that are not full screens: the **status strip** on HOME (`Game PC: asleep/awake`, "
           "`No gamepad`, `Wi-Fi link: slow` when jitter > 10 ms or loss > 0.5%, `Box needs a check` when couch-doctor "
           "is red) and a yellow **Settings problem** line when `/etc/couch/couch.toml` is broken (defaults are used).", ""]
    for code, (kid, parent) in REASONS.items():
        when, causes, fix = WHEN[code]
        example = parent.format(host="gamepc.local", mac="AA:BB:CC:DD:EE:FF", app="Celeste", strikes=3, code=1,
                                secs=120 if code == "WAKE_TIMEOUT" else (60 if code == "SUNSHINE_TIMEOUT" else
                                                                         (30 if code == "HOST_GONE" else 180)),
                                detail=("No video received from host. Check your firewall and port forwarding rules for port(s): UDP 47998, "
                                        "UDP 48000" if code == "STREAM_FAILED" else "(moonlight-qt's last error line)"))
        out += [f"## {code}", "", f"- **TV says:** {kid}", f"- **For grown-ups (example):** {example}", f"- **When:** {when}",
                f"- **Usual causes:** {causes}", f"- **Fix:** {fix}", ""]
    return "\n".join(out)


if __name__ == "__main__":
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "FAILURE-SCREENS.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render())
    print(path)
