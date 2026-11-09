# couchbox RUNBOOK

Everything a grown-up (Michael) or the remote validator (Fable, over SSH) needs: install, first boot, pairing, adding
games, re-pairing, every failure screen, validation, updates and rollback. Commands assume the repo's paths.
Placeholders: `gamepc.local` (the workstation's name), `AA:BB:CC:DD:EE:FF` (its MAC), `couchbox.local` (the NUC).

## 0. What runs where

| Machine | What | Owner of the change |
|---|---|---|
| Workstation (Windows 11, garage) | Sunshine (host), iperf3 probe, WoL settings | `workstation/*.ps1`, run by Michael, elevated |
| NUC (Ubuntu Server 24.04, living room TV) | Xorg kiosk (xinit on tty1) -> Couch launcher -> moonlight-qt; couch-daemon; couch-maint timer | `installer/install.sh` |
| Media server | Fable: SSH validation as `couchadmin`, reads `http://couchbox.local:7777/health` | - |

## 1. Workstation first (M3)

Open **Windows PowerShell 5.1 as Administrator** in the repo's `workstation` folder. Every script prints its plan
first; nothing changes without `-Apply`.

1. `Set-ExecutionPolicy -Scope Process Bypass` (this window only).
2. `.\Export-SteamLibrary.ps1` (look at the plan), then `.\Export-SteamLibrary.ps1 -Apply`. Creates
   `couchbox-export\steam-library.json` + `couchbox-export\art\<appid>.jpg` and PNG copies for Sunshine. Reads
   Steam's files only.
3. `.\Setup-Sunshine.ps1 -Apply -LibraryJson .\couchbox-export\steam-library.json` (or the full path that step 2
   printed; relative paths resolve against the folder you are in). Installs/upgrades Sunshine
   (winget `LizardByte.Sunshine`), adds the two `couchbox Sunshine` firewall rules (Private network, local subnet),
   and merges one entry per installed game into Sunshine's `apps.json` (backup `apps.json.couchbox-<time>.bak`
   beside it). The two apps Sunshine already has ("Desktop", "Steam Big Picture") and any hand-made entries are
   kept exactly as they are. It also sets up the **stream display switch** (next step).
   - **Stream display (ultrawide primary):** Sunshine captures the primary monitor, a 3440x1440 ultrawide, so a 16:9
     TV would get black bars. Setup copies `Set-StreamDisplay.ps1` (+ `CouchKit.psm1`) to
     `C:\Program Files\Sunshine\config\couchbox\`, adds a `global_prep_cmd` do/undo pair to `sunshine.conf`
     (backup beside it; other settings kept) and a logon task "couchbox display restore". When the TV starts a
     stream, `do` switches the primary to the TV's resolution/refresh (Sunshine passes `SUNSHINE_CLIENT_WIDTH/HEIGHT
     /FPS`; a 4K request on this monitor gets the largest 16:9 mode it has, e.g. 2560x1440) and saves your mode;
     `undo` restores it when the stream ends; the logon task restores it if a stream ended uncleanly. Restart
     SunshineService once after setup. Opt out with `-SkipDisplay`.
   - **Alternative (documented, not installed):** a virtual display driver (an IDD such as the open-source
     "Virtual Display Driver") gives Sunshine a 16:9 virtual monitor so the ultrawide never changes mode. More
     moving parts (a kernel-mode-adjacent driver, Sunshine `output_name` config); consider it only if mode
     switching misbehaves with a game. The existing "Virtual Desktop Monitor" device (from the Virtual Desktop VR
     app) is NOT such a driver; leave it alone.
4. Open `https://localhost:47990` once and set Sunshine's admin user/password (stays on the PC).
5. `.\Set-WakeOnLan.ps1 -WhatIf` first. On this PC (facts: magic packet already enabled, Fast Startup already off)
   it prints **"already correct"** and `-Apply` changes nothing. Otherwise `-Apply` enables Wake on Magic Packet,
   turns Fast Startup off and keeps the previous values in `wol-backup-<time>.json`. It always prints the line for
   the NUC's config: `mac = "AA:BB:CC:DD:EE:FF"`.
6. **BIOS/UEFI (manual, once):** reboot into setup, enable "Wake on LAN" / "Power On By PCI-E/PCI". The kit
   never touches firmware. The PC must be on a **cable** to its mesh point for wake to work reliably.
7. `.\Install-LinkProbe.ps1 -Apply`. iperf3 as a startup task + one firewall rule (TCP 5201, local subnet) so the
   NUC can measure the garage-to-living-room link.
8. `.\Check-Sunshine.ps1` must end with every line OK (exit 0). WARN lines say what to run. `link INFO` lines are
   advice only: the link speed, and Realtek "Gigabit Lite" / "Energy-Efficient Ethernet" / "Power Saving Mode" if
   they are on (they can downshift a gigabit link to 100 Mbps on a marginal cable; turning them off is your call,
   the kit never changes them). There is no host-side bitrate cap: the quality ladder follows the measured path.

## 2. Install the NUC (M2)

### Option A: unattended USB (wipes the NUC's disk)

On any Linux machine with `xorriso` and `openssl`:

```
installer/make-usb.sh --iso ubuntu-24.04.x-live-server-amd64.iso --disk /dev/nvme0n1 --out couchbox.iso \
    --hostname couchbox --timezone America/Chicago [--wifi-ssid "Home Net"]
sudo dd if=couchbox.iso of=/dev/sdX bs=4M conv=fsync status=progress     # sdX = the USB stick; double-check
```

- `--disk` is required. Use the NUC's internal disk name, or `--disk only` when the NUC has exactly one internal
  disk. The installer's disk guard **stops** (it never guesses) if the disk is missing or ambiguous.
- You type the console admin password (and Wi-Fi passphrase); only the hash/passphrase go into the image.
- Boot the NUC from the stick: it installs unattended, reboots, and on **first boot** `couch-firstboot.service`
  finishes the install (DKMS build of the gamepad drivers for the installed kernel, services). Allow ~10 minutes.

### Option B: plain Ubuntu Server 24.04

Install Ubuntu Server 24.04 (minimized), then:

```
git clone <this repo> couchbox && cd couchbox
sudo installer/install.sh --dry-run      # read the plan
sudo installer/install.sh                # second run prints "changes: 0"
```

Options: `--dry-run` (plan only), `--no-services` (container rehearsal only), `--with-xone` (Microsoft Xbox Wireless
Adapter only: it blacklists the in-kernel `xpad` driver, which the couch's receiver needs - do NOT use it for that
receiver), `--with-xpadneo` (Xbox pads over Bluetooth), `--no-dkms` (accepted, does nothing: DKMS drivers are
opt-in). The installer refuses
anything but Ubuntu 24.04, logs to `/var/log/couch/install.log`, and touches only its own paths (README lists them).

### Configure

`sudo nano /etc/couch/couch.toml` (mode 0640, root:couch): `[host] name = "gamepc.local"` (the PC's Windows
name; never an IP) and `mac = "AA:BB:CC:DD:EE:FF"` from step 1.5. The launcher and daemon reload it within a
second. Other keys: `[stream]` display_mode (`auto` follows the measured link; default 1080p60 HEVC 15 Mbps),
`[pair] policy`, `[maintenance] nightly_reboot` ("04:30" or ""), optional `ntfy_url` (alerts only when the box
turns red). Then plug the Xbox Wireless Adapter into the NUC.

## 3. First pairing

1. On the TV: press **Start**, choose **Pair with the game PC**, press **A**. A 4-digit PIN appears.
2. On the PC: `https://localhost:47990` > **PIN** > type the 4 digits and a name ("couchbox") > Send.
3. The TV returns to HOME by itself. If the PIN screen times out (3 min) or fails, see the failure list below.

## 4. Box art and games on the TV

- **Add a game:** install it in Steam on the PC, then re-run `.\Export-SteamLibrary.ps1 -Apply` and
  `.\Setup-Sunshine.ps1 -Apply -LibraryJson .\couchbox-export\steam-library.json`, then copy `couchbox-export` to the
  NUC and run `sudo /opt/couch/bin/couch-update --import /path/to/couchbox-export` (or `--import http://<pc>:<port>/`
  if you serve the folder on the LAN). The tile appears within a second. Art is read from `/var/lib/couch/art`
  only; tiles without art get a coloured card with the game's initial.
- **Every Sunshine app gets a tile automatically.** The launcher shows the `[[tile]]` blocks first (Desktop and Steam
  Big Picture keep their place), then the Steam-library tiles with art, then a coloured card for every other app
  couch-daemon lists from Sunshine, sorted by name. New, renamed and removed apps show up within about 20 s without a
  restart; the last good list is kept in `/var/lib/couch/apps-cache.json`, so the tiles are there after a reboot even
  while the PC is asleep. `[tiles] hide = [...]` in `couch.toml` keeps apps out (default `["SteamVR"]`; `[]` shows all;
  an explicit `[[tile]]` always shows). Nothing new becomes playable: the daemon only launches what it lists.
- **Hand-made tiles:** add `[[tile]]` blocks to `couch.toml` (`name`, `app` = exact Sunshine app name, optional
  `art` = file in `/var/lib/couch/art`).

## 5. Re-pair

Needed after a NUC reinstall or if someone removed the client in Sunshine (the TV shows "This box needs a grown-up
to connect it to the game PC"). In Sunshine's web page you may first remove the old "couchbox" client, then repeat
section 3.

## 6. Failure screens

A stream whose connection fails does not return by itself in moonlight-qt (it stays behind an error dialog); the
launcher reads moonlight's log for the first 25 s of every stream and treats "No video received from host",
"Connection terminated", "Starting ... failed" and similar as a failed start: it stops the stream (and ends the
Sunshine session), retries after 2 s, and on the third failure shows the screen below with the real reason.

A game still running on the PC after the launcher or the X server died (a kiosk restart, a crash) shows a blue
**Resume** badge on its tile for `[stream] resume_window_min` minutes (default 15). Press the tile to resume it. When
the window passes untouched the box ends it once (`streams.jsonl`: `outcome: stranded-ended`). `0` ends it at once.
Only a session started by this box is ever offered or ended.

Every failure has a kid line, A = Try again, B = Home, and a small "For grown-ups" line. The full list with causes
and fixes is `docs/FAILURE-SCREENS.md` (generated from the code). Quick map:

| TV says | First thing to check |
|---|---|
| The game PC is still asleep. | PC power, BIOS WoL, `Check-Sunshine.ps1`, MAC in couch.toml |
| The game PC is awake but not ready to play. | SunshineService running on the PC |
| The game would not start. | the tile's app name exists in Sunshine; try Desktop |
| We lost the game PC. | network between garage and living room; `couch-doctor` network line |
| This box needs a grown-up to connect it... | re-pair (section 5) |
| Pairing took too long / did not work. | type the PIN within 3 minutes; PC awake |

If the launcher itself crashes 3 times within a minute, the TV shows moonlight-qt's own menu instead (still
gamepad-driven); the next restart tries the launcher again. A stream that will not quit: hold the **Xbox/Guide
button for 3 seconds**.

## 7. Health and remote validation (Fable over SSH)

`couchadmin` logs in with the media server's key only (no password, no sudo; can read the journal):

```
ssh couchadmin@couchbox.local
/opt/couch/bin/couch-doctor                 # human matrix; --json for machines; exit 0 green / 1 yellow / 2 red
journalctl -u couch-kiosk -u couch-daemon -u couch-maint --since -1h
curl -s http://127.0.0.1:7777/status        # host found by name/arp/cache, Sunshine port state
curl -s http://127.0.0.1:7777/apps          # what moonlight-qt list returned
curl -s -XPOST -H 'X-Couch: 1' http://127.0.0.1:7777/wake
curl -s -XPOST -H 'X-Couch: 1' -d '{"app":"Desktop","launch":true}' http://127.0.0.1:7777/stream   # TV starts it
curl -s -XPOST -H 'X-Couch: 1' http://127.0.0.1:7777/quit
curl -s -XPOST -H 'X-Couch: 1' -d '{"measure":true}' http://127.0.0.1:7777/doctor               # 10 s iperf3
/opt/couch/bin/couch-streamtest --hold 60     # starts "Desktop" on the TV, proves it holds 60 s, quits it; exit 0 = PASS
```

`couch-streamtest` is the proof for the stream-crash class seen 2026-11-05/08 (every stream died ~1 s in because the
kiosk compositor segfaulted when Moonlight's window appeared): it fails with "kiosk restarted during the stream" and
prints moonlight.log's last lines plus the couch-kiosk journal. The doctor's `streams` line goes red for the same
class (the next launcher start records a stream whose launcher died as `kiosk-crash`), and its `services` line
warns when couch-kiosk has restarted 3+ times since boot. Run it with the TV free: it really streams.

From the LAN (read-only): `http://couchbox.local:7777/health` (JSON) and `http://couchbox.local:7777/` (page).
Every other path from the LAN is 404. The daemon reruns the doctor every 5 minutes and the 10 s throughput probe
hourly (never during a stream); an `ntfy_url` gets one POST each time the box turns red.

Files worth reading: `/var/log/couch/{install,launcher,kiosk,moonlight}.log`, `/var/log/Xorg.0.log` (the kiosk's X
server), `journalctl -b -u couch-kiosk` (crashes/restarts), `/var/lib/couch/streams.jsonl` (last streams, `outcome:
kiosk-crash` when the launcher died under one), `link.json` (measured link + chosen quality), `xone-status.json` (gamepad driver vs. kernel).

## 8. Updates and rollback

- **Ubuntu security updates** install themselves (unattended-upgrades, security origin only, no auto reboot).
  After a kernel update the `zz-couch-xone-check` hook verifies the gamepad drivers were rebuilt; if not, the
  doctor goes red ("gamepad driver missing for kernel ...") and wired/Bluetooth pads still work. Fix:
  `sudo dkms autoinstall && sudo reboot`, or boot the previous kernel from GRUB.
- **Nightly reboot** (optional, `nightly_reboot`): within 15 minutes after the configured time, only if up > 2 h
  and no stream is running.
- **Update Couch / moonlight-qt:** `git clone <repo> /tmp/couchbox-new` (or pull), then
  `sudo /opt/couch/bin/couch-update --from /tmp/couchbox-new`. It saves a rollback point, runs the new installer,
  verifies (launcher selftest + moonlight-qt starts) and **rolls back automatically** if anything fails.
- **Manual rollback:** `sudo /opt/couch/bin/couch-update --rollback` (re-runs the previous installer; previous
  moonlight-qt versions are kept in `/opt/couch/moonlight/`, so no download). `couch-update --status` shows what
  is installed and whether a rollback point exists.
- **Reinstall from scratch:** the USB stick again (section 2). Pairing must then be redone (section 5).

## 9. Gamepads

- **The couch's receiver (default path, no extra driver):** the generic 2.4 GHz dongle presents itself as a wired
  "Xbox 360 Controller" (USB 045E:028E). The kernel's `xpad` driver claims it and SDL maps it out of the box. Plug it
  into any USB port; turn the pad on.
- **Unknown pads:** a pad SDL has no mapping for gets a one-time **"New controller!"** screen the first time it is
  plugged in: press each button as asked (wait 10 s to skip one the pad doesn't have). The result is saved as an
  SDL mapping line in `/etc/couch/gamepads.txt` and used immediately and on every start. Until then the pad still
  navigates with its first buttons and D-pad. To redo it later: set `[gamepad] setup_pin = "1234"` (any 4 digits)
  in `couch.toml`, then Menu > **Set up a controller** asks for the code (D-pad up/down/left/right, A).
- **What the box sees:** `couch-doctor` lists every pad with VID:PID, SDL GUID and mapped yes/NO.
- **Hot-plug:** unplugging/replugging the receiver or switching a pad off and on needs no restart.
- **Bluetooth (optional):** `sudo installer/install.sh --with-xpadneo`, then `sudo bluetoothctl` > `scan on` > hold
  the pad's pair button > `pair <MAC>` > `trust <MAC>` > `connect <MAC>`. The NUC7 has Bluetooth 4.2.
- **Microsoft Xbox Wireless Adapter (optional, not the couch's dongle):** `--with-xone` (fetches the checksum-verified
  firmware). It blacklists `xpad`, so 045E:028E receivers stop working while it is installed; re-running the
  installer without `--with-xone` removes the blacklist.
