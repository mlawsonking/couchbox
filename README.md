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
couch/bin/kiosk               what cage runs on the TV (P1: moonlight-qt's own GUI; later the Couch launcher)
couch/doctor/couch-doctor     read-only health matrix (stdlib Python), --json
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
| Cold boot -> moonlight-qt GUI on the TV, gamepad-navigable | - | yes (acceptance 1, 2) |

## Decisions made by the builder (open questions from BRIEF.md)

- **Box art on the NUC:** the workstation exporter's `steam-library.json` + art is imported once into
  `/var/lib/couch/art` (`couch-update --import <path|url>`, P3) and read from disk; tiles without art get a generated
  title card. Not relying on moonlight-qt's private box-art cache: its location/format is an implementation detail
  that can change with any Moonlight update, and it is only filled after the GUI has loaded the host once.
- **Child process vs. seat handover:** moonlight-qt runs as a **child of the launcher inside the same cage session**
  (cage shows the newest toplevel full-screen; when the child exits the launcher is on top again). No seat/VT
  handover, so there is never a moment where the TV shows a console.
- **Sounds / palette / attract mode:** decided and documented with the launcher in P2.
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
- [ ] P2 pygame-ce launcher with headless golden renders
- [ ] P3 daemon + self-healing
- [ ] P4 workstation PowerShell kit
- [ ] P5 CI + RUNBOOK / TESTPLAN / FAILURE-SCREENS / PLAN-B-WINDOWS
