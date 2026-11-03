# couchbox - a NUC on one TV input that streams the workstation's games (Moonlight client -> Sunshine host), gamepad-only

Commissioned by Michael (owner) 2026-11-03. Authored by Fable (the Claude session on his media server). Built by YOU,
the cloud session on this repo. Cloud credits expire 2026-11-05 ~midnight: work the phases IN ORDER and **open a pull
request to `main` at the end of each phase** so partial delivery still lands. `GAMEPLAN.md` has the full reasoning and
the decisions - they are made; do not re-open them. `facts/lan-facts.json` has the few facts you may rely on.

## The one-sentence goal
A NUC under the living-room TV boots straight into a 10-foot launcher; an Xbox-style gamepad (USB dongle) is the only
input; one press wakes Michael's Windows 11 gaming workstation if needed and starts the game stream full-screen; quitting
the game returns to the launcher; it keeps working for months with nobody touching it.

## Fixed decisions (summary; GAMEPLAN.md section 2 has the why)
- NUC OS: **Ubuntu Server 24.04 LTS**, minimal, no desktop. Kiosk = systemd unit running **cage** (Wayland) -> our app.
- Launcher: **Python 3.12 + pygame-ce (SDL2)**, the only pip dependency, pinned + hashed in `/opt/couch/venv`.
- Streaming: **moonlight-qt** AppImage pinned + sha256, driven by CLI (`moonlight-qt stream <host> "<app>" --quit-after`,
  `moonlight-qt pair <host> --pin NNNN`, `moonlight-qt list <host>`). Phase 1 runs moonlight-qt itself full-screen as
  the fallback before the launcher exists.
- Daemon: Python stdlib only. Actions on 127.0.0.1:7777 (allow-list), `/health` read-only on the LAN.
- Workstation kit: PowerShell **5.1-compatible, pure ASCII**, dry-run by default, `-Apply` to change. Michael runs it.
- Gamepad: **xone** DKMS (Xbox Wireless Adapter dongle) + Bluetooth (xpadneo) fallback + wired (in-kernel xpad). All
  three paths must be exercised in code and in `couch-doctor`.
- Box art: Steam public CDN by appid (no key), fallback generated title cards. No keyed or metered service anywhere.
- Network reality: the workstation is in the garage, the NUC ~100 ft away in the living room, no cable run - at least one
  hop crosses a 1st-gen Google Wifi mesh that also carries a busy media server. So: default **1080p60 HEVC ~15 Mbps**, a
  config quality ladder (720p60 10 -> 1080p60 15/20 -> 1440p60 30 -> 4K60 40 Mbps), `couch-doctor` measures jitter/loss/
  throughput to the host (iperf3 on the workstation, installed LAN-only by the kit), and the launcher shows a
  link-quality badge (jitter > 10 ms or loss > 0.5%). Never assume a wired path.
- Hardware unknowns (NUC model, iGPU generation, TV caps) are NOT known yet: everything adapts at install time via
  `couch-doctor` (vainfo decode caps -> default display mode), nothing is hard-coded to a model.

## Phases (each ends in a PR; CI green before you open it)
### P1 - Bootable kiosk with the fallback built in
- `installer/install.sh`: idempotent, `--dry-run`, `--no-services`/`--no-dkms` (for container rehearsal), refuses
  anything but Ubuntu 24.04, logs to `/var/log/couch/install.log`. Installs: cage, seatd/libinput bits, mesa/VA-API
  (intel-media-va-driver), vainfo, python3-venv, dkms + xone (+ firmware fetch, with checksum), moonlight-qt AppImage
  (pinned URL + sha256, into `/opt/couch/bin`), unattended-upgrades security-only, journald size caps. Creates system
  user `couch` (video/input/render groups, no shell login) and admin user `couchadmin` with an SSH public key taken from
  `installer/authorized_keys` (Fable will fill it; empty file = skip). Masks `getty@tty1`; installs
  `couch-kiosk.service` (`cage -- /opt/couch/bin/kiosk`, `Restart=always`, `RestartSec=2`, hardening directives) where
  `/opt/couch/bin/kiosk` in P1 execs moonlight-qt full-screen.
- `installer/autoinstall/user-data` (Ubuntu autoinstall/cloud-init) + `installer/make-usb.sh` that builds an unattended
  installer image from the stock 24.04 server ISO; **requires `--disk /dev/...`** and refuses silently-picking when more
  than one disk is present; late-command clones/copies this repo and runs `install.sh`.
- `couch/doctor/couch-doctor` v0: display (DRM connectors), gamepad (SDL controllers + /dev/input), decode caps, services.
- Tests: shellcheck, bats, container rehearsal (`ubuntu:24.04`, assert files/units/config, second run = no change),
  `systemd-analyze verify`, autoinstall YAML validated against the subiquity schema.

### P2 - The Couch launcher (pygame-ce)
- Screens: HOME (tile grid, box art, focus ring, status strip), WAKING, CONNECTING, STREAMING (launcher hidden; the
  moonlight child owns the screen), LOST (kid words + parent detail + Retry), PAIR (shows the PIN; waits), RESTART menu.
- Input: D-pad/left stick navigate, A select, B back, Start = menu, Guide/Xbox button held 3 s while streaming =
  force-quit the stream (only if moonlight's own quit chord fails). Everything reachable with those; nothing else.
- State machine `IDLE -> WAKING -> WAIT_SUNSHINE -> STREAMING -> IDLE | LOST`, every transition with a timeout; the
  launcher supervises the moonlight-qt child (kills zombies, 3 consecutive failures -> LOST). Attract dimming after
  10 min, DPMS off after 60 min, any button wakes.
- Config `/etc/couch/couch.toml` (stdlib tomllib): host name, MAC, display mode (auto|1080p60|4k60|4k120|custom),
  bitrate, codec preference, tiles (name -> Sunshine app name, art path/url), pair PIN policy, nightly reboot time,
  optional ntfy URL. Hot-reload on change.
- Tests: pytest for the state machine (every transition + timeout), tile model, input mapping via synthetic SDL events,
  bad-config handling; **headless golden renders** (SDL dummy driver) of all screens at 1920x1080 and 3840x2160,
  pixel-diffed in CI. Frame-time budget asserted (< 8 ms/frame at 4K on CPU rendering - keep draws cheap: cached
  surfaces, no per-frame text re-render).

### P3 - couch-daemon + self-healing
- Actions (127.0.0.1 only): `wake` (WoL to the configured MAC; exact packet bytes tested), `apps` (cached
  `moonlight-qt list`), `stream <app>` (only apps from the list), `quit`, `pair`, `restart`, `doctor`. Anything else = 404.
- Host discovery without a fixed IP: mDNS/hostname first, ARP table by MAC second, last-known IP cache third.
- Watchdog (`sd_notify`), post-kernel-update hook that verifies the xone module loads and flags red if not,
  `couch-update` (new Moonlight/Couch with rollback to the previous dir), nightly reboot timer (config), `/health`
  JSON + tiny HTML on the LAN (read-only), `couch-doctor` v1 = the full matrix (display, gamepad x3 paths, decode caps,
  wired/wireless + ping jitter + 10 s throughput probe to the host, pairing state, Sunshine reachable, services, disk,
  last 3 streams with durations/exit codes). Optional ntfy POST on red (URL from config only).
- Tests: pytest on a real `ThreadingHTTPServer`, mock `moonlight-qt` binary recording argv, WoL bytes, allow-list, LAN vs
  loopback enforcement.

### P4 - Workstation kit (PowerShell 5.1, ASCII, Pester on fixtures)
- `Setup-Sunshine.ps1 [-Apply]`: winget install/upgrade `LizardByte.Sunshine`; firewall rules for Sunshine's ports only;
  merge into `apps.json` (keeps every existing entry and unknown keys; adds Steam Big Picture, Desktop, and per-game
  entries from the exporter); backups beside every touched file. Use Sunshine's documented app forms (verify against
  the current docs; note the version you targeted).
- `Export-SteamLibrary.ps1`: parses `steamapps\appmanifest_*.acf` across all library folders (`libraryfolders.vdf`),
  emits `steam-library.json` (appid, name, installed) and downloads box art from the Steam CDN
  (`library_600x900.jpg` by appid) into Sunshine's assets folder; no key.
- `Set-WakeOnLan.ps1 [-Apply]`: NIC "Wake on Magic Packet" + Fast Startup off; BIOS step documented, never attempted.
- `Check-Sunshine.ps1`: doctor for that side (service, ports listening, firewall, apps.json valid, WoL property, GPU
  encoder present).
- `Install-LinkProbe.ps1 [-Apply]`: iperf3 via winget as a scheduled task bound to the LAN interface, firewall rule for
  TCP 5201 from the private LAN only, so `couch-doctor` can measure the garage-to-living-room path.
- `Set-StreamDisplay.ps1 do|undo`: the host's PRIMARY monitor is a 3440x1440 ultrawide and Sunshine captures the primary,
  so a 16:9 TV would be letterboxed. Ship a Sunshine **global prep-cmd** pair (`do` = switch the primary to the stream
  resolution/refresh passed by Moonlight via Sunshine's `SUNSHINE_CLIENT_WIDTH/HEIGHT/FPS` env vars, `undo` = restore the
  saved mode) using `ChangeDisplaySettingsEx` via Add-Type P/Invoke, idempotent, with the saved mode in a state file and a
  safety restore on service start. Pester-test the mode selection and state handling; the P/Invoke call itself is
  hardware-only (say so). Document the virtual-display-driver alternative in the RUNBOOK but do not install one.
- Facts already known about the host (facts/lan-facts.json "workstation"): Sunshine 2026.516.x already installed and
  running with an EMPTY sunshine.conf and two existing apps ("Desktop", "Steam Big Picture") - the merge keeps both;
  Steam has two library folders (28 manifests incl. tools); the NIC currently links at 100 Mbps - cap the default
  bitrate ladder at 50 Mbps and let the doctor report the link speed.
- Tests: Pester (pwsh in CI) with fixture `.acf`/`.vdf`/`apps.json`; dry-run writes nothing; PS5.1 syntax lint
  (no `??`, `?.`, ternary, `-notlike` on arrays, no Unicode).

### P5 - Proof and docs
- `.github/workflows/ci.yml` running every gate; `docs/RUNBOOK.md` (USB install, first boot, pairing, add a game,
  re-pair, every failure screen, how Fable validates over SSH, rollback), `docs/TESTPLAN.md` (couch steps for Michael,
  numbered, pass/fail boxes), `docs/FAILURE-SCREENS.md`, `docs/PLAN-B-WINDOWS.md` (documented, not built).

## Guardrails (hard requirements)
1. The NUC never holds a workstation login, Windows password, Steam credential or any API key. Pairing is Moonlight's
   PIN + certificate exchange, once.
2. Daemon = allow-list only; actions bind to 127.0.0.1; `/health` is read-only. No free-form commands anywhere.
3. WoL only to the MAC in config. `stream` only to apps `moonlight-qt list` returned.
4. Installer touches only: the packages it names, `/opt/couch`, `/etc/couch`, `/var/lib/couch`, `/var/log/couch`,
   `couch-*` systemd units, the `getty@tty1` mask, unattended-upgrades config. `--dry-run` prints the plan; second run
   is a no-op; never formats or partitions anything (only `make-usb.sh`'s autoinstall does, and only with `--disk`).
5. Workstation scripts: dry-run default, `-Apply` required, backups beside every touched file; touch ONLY Sunshine
   (install/config/its firewall rules), the iperf3 probe (install, its one firewall rule, its task), the NIC WoL
   property and Fast Startup. Never Steam's files, other firewall rules, Windows Update, or display settings.
6. Dependencies: pygame-ce only (pinned + hashes). Daemon/doctor stdlib. No npm/Electron/snaps/flatpaks.
7. Every failure state has a kid-readable screen AND a parent-readable detail line.
8. No secrets, home IPs or MACs in the repo. Config examples use `AA:BB:CC:DD:EE:FF` / `gamepc.local`. CI greps for
   token/password/MAC/`192.168.` outside `docs/` examples and `facts/` and fails on a hit.
9. No network dependency at runtime beyond the LAN (box-art download is a workstation-side, setup-time step; the
   launcher reads art from disk).

## What you must NOT do
- Do not try to reach any of Michael's machines; you cannot. Build against mocks (a fake `moonlight-qt` binary, fixture
  `.acf`/`apps.json`, SDL dummy video driver) behind small interfaces Fable can swap for the live hardware.
- Do not pick a NUC model, a TV mode or a dongle model in code - detect and default.
- Do not add settings a kid could reach from the couch beyond Pair (PIN-gated on the workstation) and Restart.

## Open questions you decide (write the answer in README.md)
- How tiles get box art on the NUC: Sunshine serves app art to Moonlight over the protocol and moonlight-qt caches it
  locally after `list`/GUI load; OR the workstation exporter's `steam-library.json` + art is copied to
  `/var/lib/couch/art` once by `couch-update --import <url|path>`. Pick what is robust; fallback = generated cards.
- Attract-mode visuals, sounds (small WAVs generated by a script, no third-party assets), palette (10-foot,
  high-contrast, colour-blind safe).
- Whether the launcher runs moonlight-qt as a child under the same cage session (preferred) or hands the seat over.

## Done means
GAMEPLAN.md section 1, items 1-10, as verified on the real hardware by Fable (SSH from the media server) and Michael
(couch). In the cloud: every CI gate green on each phase's PR, README states what was verified here vs. what only the
hardware can prove (same table style as Spin to Watch's README).
