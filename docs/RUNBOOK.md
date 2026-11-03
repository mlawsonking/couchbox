# couchbox RUNBOOK

Everything a grown-up (Michael) or the remote validator (Fable, over SSH) needs: install, first boot, pairing, adding
games, re-pairing, every failure screen, validation, updates and rollback. Commands assume the repo's paths.
Placeholders: `gamepc.local` (the workstation's name), `AA:BB:CC:DD:EE:FF` (its MAC), `couchbox.local` (the NUC).

## 0. What runs where

| Machine | What | Owner of the change |
|---|---|---|
| Workstation (Windows 11, garage) | Sunshine (host), iperf3 probe, WoL settings | `workstation/*.ps1`, run by Michael, elevated |
| NUC (Ubuntu Server 24.04, living room TV) | cage kiosk -> Couch launcher -> moonlight-qt; couch-daemon; couch-maint timer | `installer/install.sh` |
| Media server | Fable: SSH validation as `couchadmin`, reads `http://couchbox.local:7777/health` | - |

## 1. Workstation first (M3)

Open **Windows PowerShell 5.1 as Administrator** in the repo's `workstation` folder. Every script prints its plan
first; nothing changes without `-Apply`.

1. `Set-ExecutionPolicy -Scope Process Bypass` (this window only).
2. `.\Export-SteamLibrary.ps1` (look at the plan), then `.\Export-SteamLibrary.ps1 -Apply`. Creates
   `couchbox-export\steam-library.json` + `couchbox-export\art\<appid>.jpg` and PNG copies for Sunshine. Reads
   Steam's files only.
3. `.\Setup-Sunshine.ps1 -Apply -LibraryJson .\couchbox-export\steam-library.json`. Installs/upgrades Sunshine
   (winget `LizardByte.Sunshine`), adds the two `couchbox Sunshine` firewall rules (Private network, local subnet),
   and merges Desktop, Steam Big Picture and one entry per installed game into Sunshine's `apps.json` (backup
   `apps.json.couchbox-<time>.bak` beside it; your own entries are never touched).
4. Open `https://localhost:47990` once and set Sunshine's admin user/password (stays on the PC).
5. `.\Set-WakeOnLan.ps1 -Apply`. Enables Wake on Magic Packet, turns Fast Startup off, prints the line for the
   NUC's config: `mac = "AA:BB:CC:DD:EE:FF"`. Previous values: `wol-backup-<time>.json` beside the script.
6. **BIOS/UEFI (manual, once):** reboot into setup, enable "Wake on LAN" / "Power On By PCI-E/PCI". The kit
   never touches firmware. The PC must be on a **cable** to its mesh point for wake to work reliably.
7. `.\Install-LinkProbe.ps1 -Apply`. iperf3 as a startup task + one firewall rule (TCP 5201, local subnet) so the
   NUC can measure the garage-to-living-room link.
8. `.\Check-Sunshine.ps1` must end with every line OK (exit 0). WARN lines say what to run.

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

Options: `--dry-run` (plan only), `--no-services` and `--no-dkms` (container rehearsal only). The installer refuses
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
- **Hand-made tiles:** add `[[tile]]` blocks to `couch.toml` (`name`, `app` = exact Sunshine app name, optional
  `art` = file in `/var/lib/couch/art`).

## 5. Re-pair

Needed after a NUC reinstall or if someone removed the client in Sunshine (the TV shows "This box needs a grown-up
to connect it to the game PC"). In Sunshine's web page you may first remove the old "couchbox" client, then repeat
section 3.

## 6. Failure screens

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
```

From the LAN (read-only): `http://couchbox.local:7777/health` (JSON) and `http://couchbox.local:7777/` (page).
Every other path from the LAN is 404. The daemon reruns the doctor every 5 minutes and the 10 s throughput probe
hourly (never during a stream); an `ntfy_url` gets one POST each time the box turns red.

Files worth reading: `/var/log/couch/{install,launcher,kiosk,moonlight}.log`, `/var/lib/couch/streams.jsonl` (last
streams), `link.json` (measured link + chosen quality), `xone-status.json` (gamepad driver vs. kernel).

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

- **Xbox Wireless Adapter (dongle):** plug into the NUC; press the adapter's button, then the pad's pair button.
  Driver: xone (DKMS). Firmware is fetched and checksum-verified by the installer.
- **Bluetooth (fallback):** `sudo bluetoothctl` > `scan on` > hold the pad's pair button > `pair <MAC>` >
  `trust <MAC>` > `connect <MAC>`. Driver: xpadneo (DKMS).
- **Wired USB:** always works (xone_wired for Xbox One/Series pads). Xbox 360 wired pads are not supported while
  xone is installed (they would need `xpad-noone`).
- A second pad works the same way; unplug/replug of the adapter returns input within seconds (SDL hot-plug).
