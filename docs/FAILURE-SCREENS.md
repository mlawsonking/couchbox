# Failure screens

Every failure shows big **kid words** on the TV, **A Try again / B Home**, and a small **For grown-ups** line with the details. This file is generated from `couch/launcher/state.py` by `scripts/gen-failure-screens.py`; a test fails if they drift apart. Screenshots: `tests/goldens/1080p/lost.png` (and every other screen).

Other signals that are not full screens: the **status strip** on HOME (`Game PC: asleep/awake`, `No gamepad`, `Wi-Fi link: slow` when jitter > 10 ms or loss > 0.5%, `Box needs a check` when couch-doctor is red) and a yellow **Settings problem** line when `/etc/couch/couch.toml` is broken (defaults are used).

## WAKE_TIMEOUT

- **TV says:** The game PC is still asleep.
- **For grown-ups (example):** No answer from gamepc.local 120s after Wake-on-LAN to AA:BB:CC:DD:EE:FF. Check the PC is plugged in, WoL is on (NIC + BIOS) and Fast Startup is off.
- **When:** WAKING for 120 s without the PC answering (WoL resent every 10 s)
- **Usual causes:** PC unplugged/off at the wall; WoL off in BIOS or NIC; Fast Startup on; wrong MAC in couch.toml; PC on Wi-Fi only
- **Fix:** Run `Check-Sunshine.ps1` on the PC; `Set-WakeOnLan.ps1 -Apply`; check the BIOS setting; compare the MAC it prints with `/etc/couch/couch.toml`.

## SUNSHINE_TIMEOUT

- **TV says:** The game PC is awake but not ready to play.
- **For grown-ups (example):** gamepc.local answers but Sunshine did not respond within 60s. Is the Sunshine service running on the PC?
- **When:** the PC answers but Sunshine's port 47989 stays closed for 60 s
- **Usual causes:** Sunshine service stopped/crashed; firewall rule missing; Sunshine still starting after a Windows update
- **Fix:** On the PC: `Check-Sunshine.ps1`; restart SunshineService; `Setup-Sunshine.ps1 -Apply` re-adds missing rules.

## STREAM_FAILED

- **TV says:** The game would not start.
- **For grown-ups (example):** moonlight-qt failed 3 times in a row starting 'Celeste' (last exit code 1).
- **When:** moonlight-qt exited with an error 3 times in a row within 15 s of starting
- **Usual causes:** the app name in couch.toml no longer exists in Sunshine; the game crashes on launch; decoder/display mode not supported
- **Fix:** `journalctl -u couch-kiosk`, `/var/log/couch/moonlight.log`; try the Desktop tile; lower `[stream]` to `1080p60`.

## STREAM_LOST

- **TV says:** We lost the game PC.
- **For grown-ups (example):** The stream of 'Celeste' stopped unexpectedly (moonlight-qt exit code 1). Network or PC problem.
- **When:** a stream that ran > 15 s ended with an error
- **Usual causes:** the PC went to sleep or rebooted; Wi-Fi/mesh drop between garage and living room; PC network unplugged
- **Fix:** Retry. If it repeats: `couch-doctor` network line (jitter/loss); consider a lower quality rung.

## HOST_GONE

- **TV says:** We lost the game PC.
- **For grown-ups (example):** gamepc.local stopped answering for 30s during the stream; the stream was closed.
- **When:** while streaming, the PC stopped answering for 30 s and moonlight-qt did not notice
- **Usual causes:** network path down; PC hard-hung
- **Fix:** Retry; check the PC; `couch-doctor --measure` for the link.

## NOT_PAIRED

- **TV says:** This box needs a grown-up to connect it to the game PC.
- **For grown-ups (example):** moonlight-qt is not paired with gamepc.local. Open the Start menu > Pair and type the PIN into Sunshine's web page on the PC.
- **When:** moonlight-qt reports it is not paired with the PC
- **Usual causes:** first use; Sunshine's paired-clients list was reset; the NUC was reinstalled
- **Fix:** Start menu > Pair, type the PIN in Sunshine's web page on the PC (https://localhost:47990 > PIN).

## PAIR_TIMEOUT

- **TV says:** Pairing took too long.
- **For grown-ups (example):** No PIN was entered in Sunshine on gamepc.local within 180s.
- **When:** the PIN screen waited 180 s
- **Usual causes:** nobody typed the PIN on the PC
- **Fix:** Start > Pair again.

## PAIR_FAILED

- **TV says:** Pairing did not work.
- **For grown-ups (example):** moonlight-qt pair gamepc.local failed: (moonlight-qt's last error line)
- **When:** moonlight-qt pair exited with an error
- **Usual causes:** wrong PIN; PC asleep; Sunshine not running
- **Fix:** Wake the PC, check Sunshine, Pair again.

## NO_HOST

- **TV says:** This box does not know which game PC to use.
- **For grown-ups (example):** /etc/couch/couch.toml: (moonlight-qt's last error line)
- **When:** couch.toml has no usable host
- **Usual causes:** fresh install with the example config; typo
- **Fix:** Edit `/etc/couch/couch.toml` (`[host] name`, `mac`); the launcher reloads it within a second.
