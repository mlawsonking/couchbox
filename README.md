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
installer/install.sh          idempotent NUC installer (--dry-run, --no-services, --no-dkms), Ubuntu 24.04 only
installer/pins.env            pinned moonlight-qt AppImage (sha256), xone + xpadneo (tag + commit), dongle firmware
installer/make-usb.sh         stock 24.04 live-server ISO -> unattended couchbox installer ISO (--disk required)
installer/autoinstall/        user-data template + disk-guard.sh (refuses to guess a disk)
installer/files/              systemd units, journald cap, unattended-upgrades (security only), couch.toml example
couch/bin/kiosk               what cage runs on the TV: the Couch launcher, falling back to moonlight-qt's GUI
couch/launcher/               the 10-foot launcher (pygame-ce): config, state machine, input, renderer, backend
couch/net.py                  WoL magic packet, IPv4 resolve, Sunshine port probe (stdlib; shared with the daemon)
couch/requirements.lock       pygame-ce 2.5.2, hash-pinned: the only pip dependency, in /opt/couch/venv
couch/daemon/                 couch-daemon (stdlib): allow-listed actions on 127.0.0.1:7777, read-only /health
couch/doctor/couch-doctor     read-only health matrix v1 (stdlib Python), --json, --measure
couch/bin/couch-update        update with automatic rollback; --import of the workstation's Steam library + art
couch/bin/couch-maint         root timer: gamepad-driver check per kernel, nightly reboot window
tests/                        bats, pytest, container rehearsal, fixtures, vendored subiquity autoinstall schema
scripts/check-secrets.sh      repo gate: keys/tokens/passwords/MACs/home IPs
```

## Install (short form; docs/RUNBOOK.md arrives in P5)

Either build a USB stick with `installer/make-usb.sh --iso <ubuntu-24.04-live-server-amd64.iso> --disk /dev/<nuc-disk>
--out couchbox.iso` (the NUC's disk is wiped; you type the console admin password, it is hashed into the image only),
or install plain Ubuntu Server 24.04 and run `git clone <this repo> && sudo ./couchbox/installer/install.sh`.
Run `sudo installer/install.sh --dry-run` first to see the plan. Health: `/opt/couch/bin/couch-doctor` (or `--json`).

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
| systemd units valid | yes: `systemd-analyze verify` in the rehearsal | cage actually taking DRM master on tty1, no login prompt, no cursor |
| autoinstall user-data valid | yes: rendered for 3 variants, validated against subiquity's JSON schema + yamllint | the USB really installing unattended (ISO repack via xorriso not exercised in CI: needs the 2.6 GB ISO) |
| disk guard refuses ambiguity | yes: bats with a fake `lsblk` | the NUC's real `lsblk` output |
| xone / xpadneo DKMS build + dongle firmware | pins verified (commit + firmware sha256); `--no-dkms` in CI | DKMS build on the NUC kernel, dongle pairing, BT pairing |
| couch-doctor v0 matrix (display, gamepad x3 paths, decode caps, services) | yes: pytest + bats on fixture sysroots (good + 5 failure variants) | real DRM connectors, real vainfo, real pads |
| Launcher state machine: every transition and every timeout (wake 120 s, Sunshine 60 s, 3 strikes, host gone 30 s, pair 180 s) | yes: pytest, plus a test asserting the full edge set is covered | real timings against the real workstation (acceptance 3: <= 90 s asleep, <= 10 s awake) |
| Gamepad mapping (D-pad/stick/A/B/Start, Guide held 3 s, two pads, raw-joystick fallback, keyboard/mouse ignored) | yes: synthetic SDL events, including through the real SDL event queue | the dongle/BT/wired pads' actual SDL mappings; Guide visible while moonlight has focus (`SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS`) |
| Every screen renders (HOME, WAKING, CONNECTING, STREAMING, LOST, PAIR, MENU + attract) | yes: golden PNGs at 1920x1080 and 3840x2160, pixel-diffed | how it looks on the TV from the couch |
| Frame budget < 8 ms at 4K on CPU | yes: p95 of steady-state frames per screen (actual: < 1 ms on CI CPUs) | the NUC's CPU |
| moonlight-qt child supervision (argv, exit codes, not-paired detection, a hung child that ignores SIGTERM is killed and reaped in < 5 s) | yes: a fake moonlight-qt CLI | real moonlight-qt exit codes/messages (the not-paired text match is a guess at its wording) |
| Bad config (syntax error, wrong types, bad MAC, IP instead of name, art URLs) never crashes; hot reload | yes: pytest | - |
| venv holds only pygame-ce, from the hash-pinned lock; the launcher renders every screen as user `couch` in Ubuntu 24.04 | yes: container rehearsal (`--selftest`) | under cage on real DRM (Wayland first, XWayland fallback) |
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
| Cold boot -> launcher on the TV, gamepad-navigable | - | yes (acceptance 1, 2) |

## Decisions made by the builder (open questions from BRIEF.md)

- **Box art on the NUC:** the workstation exporter's `steam-library.json` + art is imported once into
  `/var/lib/couch/art` (`couch-update --import <path|url>`, P3) and read from disk; tiles without art get a generated
  title card. Not relying on moonlight-qt's private box-art cache: its location/format is an implementation detail
  that can change with any Moonlight update, and it is only filled after the GUI has loaded the host once.
- **Child process vs. seat handover:** moonlight-qt runs as a **child of the launcher inside the same cage session**
  (cage shows the newest toplevel full-screen; when the child exits the launcher is on top again). No seat/VT
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
  launcher still runs the moonlight-qt child so it stays inside the cage session. Daemon `stream`/`quit`/`pair`/
  `restart` from loopback (e.g. Fable over SSH) queue a request the launcher picks up within 1 s, so remote tests go
  through exactly the same state machine as the gamepad. Without the daemon the launcher falls back to sending WoL
  and running moonlight-qt itself.
- **Restart the box:** the launcher holds the active logind session on seat0, so polkit (`polkitd`, installed) lets
  it `systemctl reboot` without any sudo rule. The daemon never needs root.
- **Watchdog:** `couch-daemon` is `Type=notify` with `WatchdogSec=30` and pets the watchdog only while both its HTTP
  and background threads are alive. The kiosk has no watchdog: when it falls back to moonlight-qt's GUI nothing could
  pet it; crashes are handled by `Restart=always` and the 3-strike launcher fallback.
- **Display sleep:** no DPMS tool for cage is in Ubuntu's archive, so after 60 min the launcher shows black and calls
  `/opt/couch/bin/couch-display-power` only if someone installs one (needs the real TV to choose CEC vs. DPMS).
- **Quality:** the hourly `doctor --measure` (skipped while streaming) runs a 10 s iperf3 probe to the workstation
  and records the highest ladder rung whose bitrate is at most half the measured throughput (one rung lower on a
  jittery or lossy link) in `/var/lib/couch/link.json`; `display_mode = "auto"` uses it, default 1080p60 15 Mbps.
- **Wired gamepads and xone:** xone blacklists the in-kernel `xpad` (they conflict over Xbox One/Series pads), so
  with xone installed wired Xbox One/Series pads are served by `xone_wired`; `xpad` serves wired pads only when xone
  is not installed (`--no-dkms`). Xbox 360 wired pads would need `xpad-noone` (not installed; documented).
- **Admin access:** `couchadmin` is key-only (password locked) with `systemd-journal`/`adm` groups and **no sudo**, so
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
- [ ] P4 workstation PowerShell kit
- [ ] P5 CI + RUNBOOK / TESTPLAN / FAILURE-SCREENS / PLAN-B-WINDOWS
