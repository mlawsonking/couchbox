# couchbox

A NUC on one TV input that boots straight into a gamepad-only launcher and streams a Windows gaming workstation's
games via Moonlight (client, on the NUC) and Sunshine (host, on the workstation). Self-healing, no desktop, no mouse,
nothing a kid can break.

- `BRIEF.md` - what the cloud session builds, in phase order, with the guardrails and test gates. Start here.
- `GAMEPLAN.md` - the decisions and why, the acceptance list, the test framework, Michael's gates, the risks.
- `facts/lan-facts.json` - the few facts about the home network you may rely on (read-only; nothing in it goes into
  code).
- `installer/authorized_keys` - the media server's SSH public key, installed for the key-only `couchadmin` user so
  the author can validate the NUC remotely. Public key only.

## Layout

```
installer/install.sh          idempotent NUC installer (--dry-run, --no-services, --with-xone, --with-xpadneo), Ubuntu 24.04 only
installer/pins.env            pinned moonlight-qt AppImage (sha256), xone + xpadneo (tag + commit), dongle firmware
installer/make-usb.sh         stock 24.04 live-server ISO -> unattended couchbox installer ISO (--disk required)
installer/autoinstall/        user-data template + disk-guard.sh (refuses to guess a disk)
installer/files/              systemd units, journald cap, unattended-upgrades (security only), couch.toml example
couch/bin/kiosk               what the kiosk's X server runs on the TV: the Couch launcher, falling back to moonlight-qt's GUI
couch/bin/couch-streamtest    SSH tool: start a stream on the TV and prove it holds 60 s (kiosk pid stable, clean quit)
couch/bin/couch-display-power the launcher's TV sleep/wake hook (xset dpms)
couch/launcher/               the 10-foot launcher (pygame-ce): config, state machine, input, renderer, backend
couch/net.py                  WoL magic packet, IPv4 resolve, Sunshine port probe (stdlib; shared with the daemon)
couch/requirements.lock       pygame-ce 2.5.2, hash-pinned: the only pip dependency, in /opt/couch/venv
couch/daemon/                 couch-daemon (stdlib): allow-listed actions on 127.0.0.1:7777, read-only /health
couch/doctor/couch-doctor     read-only health matrix v1 (stdlib Python), --json, --measure
couch/bin/couch-update        update with automatic rollback; --import of the workstation's Steam library + art
couch/bin/couch-maint         root timer: gamepad-driver check per kernel, nightly reboot window
workstation/                  Windows kit (PowerShell 5.1, ASCII, dry run by default, -Apply to change):
                                Setup-Sunshine.ps1, Export-SteamLibrary.ps1, Set-WakeOnLan.ps1, Check-Sunshine.ps1,
                                Install-LinkProbe.ps1, Set-StreamDisplay.ps1, CouchKit.psm1 (shared, mockable),
                                tests/ (Pester + 5.1 lint)
tests/                        bats, pytest, container rehearsal, fixtures, vendored subiquity autoinstall schema
scripts/check-secrets.sh      repo gate: keys/tokens/passwords/MACs/home IPs
```

## Docs

- `docs/RUNBOOK.md`: workstation kit, USB install, first boot, pairing, adding games, re-pairing, every failure
  screen, Fable's SSH validation, updates and rollback, gamepads.
- `docs/TESTPLAN.md`: the 10 acceptance items as numbered couch steps with PASS/FAIL boxes.
- `docs/FAILURE-SCREENS.md`: every failure screen (kid line, grown-up line, causes, fix), generated from the code.
- `docs/PLAN-B-WINDOWS.md`: the Windows-kiosk fallback, documented, not built.

## CI gates (`.github/workflows/ci.yml`, all must be green)

lint (secret gate, shellcheck, yamllint of the rendered autoinstall), bats, pytest (state machine, input, config,
renderer goldens + 4K frame budget, backend, daemon on a real HTTP server, doctor, maint/update, docs in sync),
container rehearsal x2 (`ubuntu:24.04` plain and systemd-chroot style), workstation (PS 5.1 lint + Pester).

## Verified here vs. needs the real hardware

| What | Verified in the cloud (CI) | Needs the real hardware |
|---|---|---|
| install.sh lands every file/dir/unit/user with exact modes and owners | yes: container rehearsal in pristine `ubuntu:24.04`, real apt installs of every named package | - |
| install.sh idempotence (2nd run = `changes: 0`; dry run after install plans 0) | yes: rehearsal, both modes | - |
| install.sh `--dry-run` changes nothing | yes: rehearsal (pristine) + bats on host | - |
| install.sh writes only its allow-listed paths | yes: rehearsal walks the filesystem, subtracts dpkg-owned files | - |
| moonlight-qt sha256 pin enforced (wrong hash refused, nothing installed) | yes, with a fixture AppImage | the real 6.2.0 AppImage hash was verified once in the build session; extraction + playback on the NUC |
| Refuses non-24.04 / unknown options; never partitions | yes: bats | - |
| Installer chroot (autoinstall) path defers DKMS + start to first boot | yes: rehearsal `systemd` mode (systemd installed, not PID 1) | the actual first boot completing |
| systemd units valid | yes: `systemd-analyze verify` in the rehearsal | Xorg (via the setuid Xorg.wrap, xinit on tty1) actually taking DRM master, no login prompt, no cursor |
| autoinstall user-data valid | yes: rendered for 3 variants, validated against subiquity's JSON schema + yamllint | the USB really installing unattended (ISO repack via xorriso not exercised in CI: needs the 2.6 GB ISO) |
| disk guard refuses ambiguity | yes: bats with a fake `lsblk` | the NUC's real `lsblk` output |
| Default install needs no out-of-tree gamepad driver and never blacklists `xpad` (the couch's receiver is 045E:028E -> xpad); a stale couchbox xpad blacklist is removed | yes: bats (dry-run plans with and without `--with-xone`) + rehearsal (file absent, stale file removed, idempotent) | the receiver on the NUC |
| Optional xone / xpadneo (`--with-xone`, `--with-xpadneo`) DKMS build + adapter firmware | pins verified (commit + firmware sha256); not built in CI | DKMS build on the NUC kernel, BT pairing |
| Unknown pads: first-run mapping screen -> SDL_GAMECONTROLLERCONFIG line in `/etc/couch/gamepads.txt`, applied without restart; re-mapping only via the PIN-gated menu | yes: pytest with synthetic SDL joystick events and a fake SDL device layer (hot-plug, pad off/on, off mid-mapping, inverted axes, d-pad as axes, skip, abort) | real SDL re-reading the mapping on a real unknown pad |
| udev rule (joystick nodes to group input, hidraw via uaccess) | lands with the right mode (rehearsal) | device permissions on the NUC |
| couch-doctor gamepad list: VID:PID, SDL GUID (database form), mapped yes/NO | yes: fixtures for the confirmed receiver and an unknown pad; GUID of 045E:028E equals SDL's database line | real `/proc/bus/input/devices` |
| Decode rules for the NUC7i5BNK (Gen9.5): vainfo decides; HEVC unlocks 4K60, AV1 absent; 4K60 rung HEVC-only; AV1 opt-in; HDR never requested; `intel-media-va-driver` (iHD) installed | yes: iHD Kaby Lake vainfo fixture, argv tests, bats + rehearsal package check | 4K60 HEVC playback on the TV |
| Host vanishing mid-stream (the PC has bugchecked repeatedly): LOST screen, Retry walks wake -> Sunshine -> stream | yes: state-machine test (moonlight exits / moonlight hangs) | the real PC rebooting mid-game |
| couch-doctor v0 matrix (display, gamepad x3 paths, decode caps, services) | yes: pytest + bats on fixture sysroots (good + 5 failure variants) | real DRM connectors, real vainfo, real pads |
| Launcher state machine: every transition and every timeout (wake 120 s, Sunshine 60 s, 3 strikes, host gone 30 s, pair 180 s) | yes: pytest, plus a test asserting the full edge set is covered | real timings against the real workstation (acceptance 3: <= 90 s asleep, <= 10 s awake) |
| Gamepad mapping (D-pad/stick/A/B/Start, Guide held 3 s, two pads, raw-joystick fallback, keyboard/mouse ignored) | yes: synthetic SDL events, including through the real SDL event queue | the dongle/BT/wired pads' actual SDL mappings; Guide visible while moonlight has focus (`SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS`) |
| Every screen renders (HOME, WAKING, CONNECTING, STREAMING, LOST, PAIR, MENU + attract) | yes: golden PNGs at 1920x1080 and 3840x2160, pixel-diffed | how it looks on the TV from the couch |
| Frame budget < 8 ms at 4K on CPU | yes: p95 of steady-state frames per screen (actual: < 1 ms on CI CPUs) | the NUC's CPU |
| moonlight-qt child supervision (argv, exit codes, not-paired detection, a hung child that ignores SIGTERM is killed and reaped in < 5 s) | yes: a fake moonlight-qt CLI | real moonlight-qt exit codes/messages (the not-paired text match is a guess at its wording) |
| Bad config (syntax error, wrong types, bad MAC, IP instead of name, art URLs) never crashes; hot reload | yes: pytest | - |
| venv holds only pygame-ce, from the hash-pinned lock; the launcher renders every screen as user `couch` in Ubuntu 24.04 | yes: container rehearsal (`--selftest`) | on the real Xorg kiosk (SDL x11) |
| Launcher crash loop -> moonlight-qt GUI fallback, then retry | yes: bats | - |
| Daemon allow-list: only the listed actions, anything else 404; actions loopback-only; LAN gets /health and / only; `X-Couch` header required; body size cap | yes: pytest on a real `ThreadingHTTPServer`, including requests from this machine's real non-loopback address | - |
| WoL packet bytes exact, only ever to the configured MAC (a MAC in the request body is ignored) | yes | the workstation actually waking (NIC + BIOS + Fast Startup, acceptance 3) |
| `stream` only for apps `moonlight-qt list` returned (mock binary records argv); measured quality rung applied | yes | real `moonlight-qt list` output format (parser accepts plain lines and CSV) |
| Host discovery: name/mDNS -> ARP by MAC -> last-known IP cache | yes: pytest | router reboot / new leases (acceptance 7) |
| Remote validation path: `POST /stream {launch:true}` drives the launcher's normal state machine | yes: real daemon + DaemonBackend + App | on the TV |
| Watchdog (`sd_notify` READY/WATCHDOG), ntfy POST only on transition to red, health.json for the TV badge | yes: pytest (real AF_UNIX notify socket) | systemd actually restarting a wedged daemon |
| doctor v1 matrix: network (wired/wireless, ping jitter/loss, 10 s iperf3 -> recommended rung), Sunshine, pairing, services, drivers, disk, last 3 streams | yes: fixture sysroots + fake ping/iperf3/moonlight | real numbers over the garage-to-living-room mesh hop |
| Post-kernel-update driver check; nightly reboot window (skips while streaming, right after boot) | yes: pytest | a real kernel update with DKMS |
| couch-update: rollback point, failed install or failed verification -> previous installer re-run; `--import` validates JSON, digits-only appids, JPEG magic | yes: pytest + rehearsal (`--status`, `--import`) | a real moonlight-qt version bump |
| Daemon runs as `couch` with the target's stdlib python3, serves /health, enforces the allow-list | yes: container rehearsal | - |
| Stream display for the 3440x1440 ultrawide: mode selection (exact, refresh fallback, largest fitting 16:9), do/undo/restore state (original never overwritten), sunshine.conf `global_prep_cmd` merge keeping other settings and prep commands | yes: Pester; the do/undo round trip with Sunshine's env vars and mocked P/Invoke | the actual `ChangeDisplaySettingsEx` call on the ultrawide, Sunshine running the prep-cmd |
| Host facts: merge keeps the two existing apps unchanged; Set-WakeOnLan `-WhatIf` says "already correct" and `-Apply` is a no-op on the host's values; Check-Sunshine reports link speed + Realtek Gigabit Lite/EEE/Power Saving as INFO only | yes: Pester with the host's values as fixtures | the host itself |
| Workstation kit: dry run writes nothing and calls nothing (every side effect is a mocked CouchKit wrapper); -Apply backs up, then changes | yes: Pester runs the real scripts against fixtures | Windows 11 with UAC, real winget/firewall/NIC cmdlets |
| apps.json merge keeps every existing entry, unknown per-app and top-level keys; adds only missing names (case-insensitive); idempotent; written without a BOM | yes: Pester with a hand-made fixture (custom emulator entry, elevated prep-cmd, extra keys) | Sunshine actually loading it (targeted v2026.1103.23515) |
| Steam library across all library folders (`libraryfolders.vdf`), installed = StateFlags bit 4, redistributables skipped, Steam files never written | yes: Pester with fixture `.vdf`/`.acf` (escaped quotes, comments, two libraries) | a real Steam install |
| Only Sunshine's ports (TCP 47984/47989/48010, UDP 47998-48000/48002/48010) and one iperf3 rule (TCP 5201), all Private profile + LocalSubnet, group "couchbox" | yes: Pester asserts the exact rule calls | Windows Firewall |
| PS 5.1 compatibility: ASCII only, no BOM, no `??` `?.` ternary `&&`/`||`, no 6+-only parameters, `ConvertTo-Json` always with `-Depth` | yes: AST lint under pwsh, proven to catch a fixture full of PS7-only code | parsing by real Windows PowerShell 5.1 |
| Cold boot -> launcher on the TV, gamepad-navigable | - | yes (acceptance 1, 2) |

## Decisions made by the builder (open questions from BRIEF.md)

- **Box art on the NUC:** the workstation exporter's `steam-library.json` + art is imported once into
  `/var/lib/couch/art` (`couch-update --import <path|url>`, P3) and read from disk; tiles without art get a generated
  title card. Not relying on moonlight-qt's private box-art cache: its location/format is an implementation detail
  that can change with any Moonlight update, and it is only filled after the GUI has loaded the host once.
- **Child process vs. seat handover:** moonlight-qt runs as a **child of the launcher on the same X server**
  (a bare Xorg without a window manager maps the newest window on top; when the child exits the launcher is
  visible again). No seat/VT
  handover, so there is never a moment where the TV shows a console.
- **Palette:** near-black navy background, white text, and Okabe-Ito colour-blind-safe hues (yellow focus ring,
  bluish-green OK, orange warning, vermillion error). State is never shown by colour alone: every badge has a word
  and a shape (dot = fine, triangle = attention). Text is at least 26 px at 1080p and scales with the TV.
- **Sounds:** five short, quiet sine blips (move, select, back, error, ready) synthesised at start-up by
  `couch/launcher/sounds.py`; no audio files ship, no third-party assets. No sound card = silent, never a crash.
- **Attract mode:** after 10 min without input on HOME/menu the screen dims to 40%; after 60 min it goes black and
  calls `/opt/couch/bin/couch-display-power off` if present (the DPMS hook lands with the daemon in P3). The first
  button press only wakes the screen; it never also launches a game.
- **Font:** pygame's bundled font, so golden renders are identical everywhere and nothing extra is installed.
- **Launcher vs. daemon split (P3):** the daemon is the gate (WoL to the configured MAC only, `stream` only for apps
  from `moonlight-qt list`, host discovery, doctor/health/ntfy loop) and runs as the unprivileged `couch` user; the
  launcher still runs the moonlight-qt child so it stays inside the kiosk's X session. Daemon `stream`/`quit`/`pair`/
  `restart` from loopback (e.g. Fable over SSH) queue a request the launcher picks up within 1 s, so remote tests go
  through exactly the same state machine as the gamepad. Without the daemon the launcher falls back to sending WoL
  and running moonlight-qt itself.
- **Restart the box:** the launcher holds the active logind session on seat0, so polkit (`polkitd`, installed) lets
  it `systemctl reboot` without any sudo rule. The daemon never needs root.
- **Watchdog:** `couch-daemon` is `Type=notify` with `WatchdogSec=30` and pets the watchdog only while both its HTTP
  and background threads are alive. The kiosk has no watchdog: when it falls back to moonlight-qt's GUI nothing could
  pet it; crashes are handled by `Restart=always` and the 3-strike launcher fallback.
- **Display sleep:** after 60 min idle the launcher shows black and calls
  `/opt/couch/bin/couch-display-power` only if someone installs one (needs the real TV to choose CEC vs. DPMS).
- **Workstation kit details (P4):**
  - Sunshine app forms follow the current docs and its shipped Windows `apps.json`: Desktop = `image-path` only;
    Steam Big Picture = `cmd: steam://open/bigpicture` with `prep-cmd` undo `steam://close/bigpicture`,
    `auto-detach`, `wait-all`; per game = `detached: ["steam://rungameid/<appid>"]`.
  - Box art: Steam's CDN serves JPEG (`library_600x900.jpg`, Cloudflare host first, Akamai fallback); the NUC uses
    the JPEGs; a PNG copy goes to `<Sunshine config>\couchbox-art` for Sunshine's own app images (converted with
    System.Drawing, which ships with Windows PowerShell 5.1). Games without art get a generated card on the TV.
  - Exporter is also dry-run by default, for consistency: it only ever READS Steam's files.
  - Wake-on-LAN keeps the previous NIC/Fast Startup values in `wol-backup-<time>.json` beside the script (registry
    and NIC settings are not files); the BIOS step is documented, never attempted. Wi-Fi adapters get a warning: most
    cannot wake the PC, so wake works best with the workstation on a cable to its mesh point.
  - iperf3 comes from winget id `ar51an.iPerf3`, runs as `LOCAL SERVICE` from a startup task; uninstall commands are
    in the script header.
- **Quality:** the hourly `doctor --measure` (skipped while streaming) runs a 10 s iperf3 probe to the workstation
  and records the highest ladder rung whose bitrate is at most half the measured throughput (one rung lower on a
  jittery or lossy link) in `/var/lib/couch/link.json`; `display_mode = "auto"` uses it, default 1080p60 15 Mbps.
- **Gamepads (corrected 2026-11-03):** the couch's receiver presents as a wired Xbox 360 pad (045E:028E), so the
  in-kernel `xpad` driver + SDL's database are the whole primary path. xone/xpadneo are opt-in installer flags. Note
  that `--with-xone` blacklists `xpad` and would break this receiver; P1-P3 installed xone by default, which this
  change undoes (and the installer removes a stale couchbox blacklist). Unknown pads get the first-run mapping
  screen; re-mapping is behind `[gamepad] setup_pin` so a kid cannot reach it.
- **NUC7i5BNK:** nothing is hard-coded to the model. couch-doctor's vainfo decides the decode ceiling (HEVC decode
  -> 4K60 allowed; the 4K60 rung always uses HEVC). AV1 is used only if configured explicitly; HDR is never
  requested (moonlight-qt's default is SDR). The installer installs `intel-media-va-driver` (iHD) alongside i965 and
  Mesa so any Intel/AMD iGPU works; libva picks iHD for Gen9.5.
- **Stream display:** a Sunshine `global_prep_cmd` (not per-app) so every app, including the two pre-existing ones,
  gets the switch. The mode change is not persisted (a reboot also restores); the logon task covers a stream that
  ended without `undo`. A virtual display driver is documented as the alternative, not installed.
- **Admin access:** `couchadmin` is key-only (shadow password `*`: no password, never `passwd -l`, which makes sshd refuse keys too) with `systemd-journal`/`adm` groups and **no sudo**, so
  the remote key can read health and logs but not change the box. The console admin created by autoinstall has sudo,
  a password Michael types at USB-build time, and no SSH key.
- **moonlight-qt is extracted, not mounted:** the AppImage is unpacked into `/opt/couch/moonlight/<version>` (no FUSE
  dependency), with `/opt/couch/moonlight/current` as the switch that `couch-update` flips and rolls back.
- **Paths beyond the brief's list, and why:** `/etc/modprobe.d/couch-xone-blacklist.conf`, `/usr/src/{xone,hid-xpadneo}-*`
  and `/lib/firmware/xone_dongle_*.bin` (all part of the xone/xpadneo DKMS install the brief requires),
  `/etc/systemd/journald.conf.d/couch.conf` (the required journald cap), and `/home/couchadmin`.

## Status by phase

- [x] P1 bootable kiosk with the moonlight-qt fallback (installer, autoinstall, doctor v0)
- [x] P2 pygame-ce launcher with headless golden renders
- [x] P3 daemon + self-healing
- [x] P4 workstation PowerShell kit
- [x] P5 CI + RUNBOOK / TESTPLAN / FAILURE-SCREENS / PLAN-B-WINDOWS

## Why Xorg, not cage (2026-11-08)

The first build ran the kiosk under `cage` (wlroots Wayland compositor). On the real NUC cage 0.1.5 segfaulted in
libwayland-server the moment moonlight-qt's Xwayland window appeared - every stream died after the first decoded
frame (`The X11 connection broke` in moonlight.log, `code=dumped, status=11/SEGV` for couch-kiosk, the launcher back
on the tiles 4 s later). moonlight-qt's AppImage only has the xcb Qt plugin, so it is an X11 client either way; the
kiosk is now a bare Xorg server started by `xinit` on tty1 (`installer/files/systemd/couch-kiosk.service`), with
`/etc/X11/Xwrapper.config` allowing the service user to start it. `couch/bin/couch-streamtest` proves a stream holds;
`couch-doctor` marks a stream that took the kiosk down as red (`streams` line, with moonlight's last lines) and warns
on kiosk restart storms (`services` line).
