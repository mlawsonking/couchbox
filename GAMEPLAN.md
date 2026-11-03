# GAMEPLAN - "Couchbox": a NUC on one TV input that streams the workstation's games via Moonlight/Sunshine, gamepad-only

Authored by Fable 2026-11-03 for Michael. Built by a NEW cloud session on a NEW repo (`mlawsonking/couchbox`), separate
from plexwheel / Cloudfiddling. Cloud credits expire 2026-11-05 ~midnight: the brief is ordered so partial delivery is
still useful. Companion: `wg\couchbox-src\BRIEF.md` (what the cloud session builds, verbatim in the repo).

## 0. Terms (one correction to the ask)
- **Sunshine = HOST.** It runs on the Windows 11 *workhorse* (the GPU box) and encodes the game.
- **Moonlight = CLIENT.** It runs on the *NUC* under the TV and decodes the stream.
- Michael's sentence said "sunshine side to my moonlight instance on my workhorse" - read as: Sunshine on the workhorse,
  Moonlight on the NUC. If he already has Moonlight on the workhorse (e.g. for a laptop), it stays; it is irrelevant here.

## 1. Goal and what "done" means
A NUC lives on one HDMI input. Power on (or wake with the gamepad) -> within ~20 s the TV shows a 10-foot launcher.
The Xbox-style gamepad (its dongle in the NUC) is the only input. One press on a tile wakes the workstation if asleep,
waits for Sunshine, and starts the stream full-screen. Quit the game -> back to the launcher. It survives reboots,
NUC OS updates, workstation reboots, router IP churn, gamepad battery swaps and months of nobody touching it.

Acceptance (Michael on the couch; Fable can also drive most of it over SSH from the media server):
1. Cold boot -> launcher ready <= 25 s; no login prompt, no desktop, no mouse cursor, no update nag, ever.
2. Gamepad navigates every screen; a second gamepad also works; unplug/replug the dongle -> input returns within 5 s.
3. "Steam Big Picture" tile: workstation asleep -> tile shows "waking..." -> stream starts within 90 s. Workstation
   already up -> stream within 10 s.
4. Per-game tiles (box art) launch that game directly in the stream.
5. Quit the game / press the launcher's quit chord -> back to the launcher in <= 5 s, no zombie Moonlight.
6. Yank the workstation's network mid-stream -> kid-readable "lost the game PC" screen with Retry, no hang.
7. Reboot the router (new DHCP leases everywhere) -> next launch still works (host found by name/mDNS + MAC-pinned
   wake, not by a hard-coded IP).
8. `couch-doctor` prints an all-green matrix (display, gamepad, decode caps, network path, Moonlight pairing, Sunshine
   reachable, services, disk, last 3 streams) and the LAN health page agrees.
9. Nothing on the NUC can be broken from the couch: no settings a kid can reach except "Pair" (needs a PIN typed on the
   workstation) and "Restart the box".
10. Repo greps clean: no tokens, no passwords, no home IPs/MACs in code (only in `/etc/couch/couch.toml` on the NUC).

## 2. Decisions (made; the cloud session does not re-open them)
| Decision | Choice | Why |
|---|---|---|
| NUC OS | **Ubuntu Server 24.04 LTS** (minimal, no desktop) + `cage` Wayland kiosk compositor | Unattended-kiosk is a solved problem on Linux (systemd units with Restart=always, no login shell, no update nags); Moonlight-qt is first-class on Linux; and - decisive - the cloud session can TEST a Linux build (containers, shellcheck, systemd-analyze verify, headless renders) and cannot test a Windows kiosk at all. Windows is Plan B, documented not built (`docs/PLAN-B-WINDOWS.md`). |
| Launcher | **"Couch" - Python 3.12 + pygame-ce (SDL2)**, runs full-screen under cage | SDL2 owns gamepad handling (built-in controller DB, hot-plug, Xbox dongle via xone/xpad, Bluetooth via xpadneo) - no browser, no Electron, no snap-Chromium-under-Wayland pain. Headless-renderable (SDL dummy driver) so the cloud can golden-screenshot every screen at 1080p and 2160p. |
| Streaming engine | **moonlight-qt** (pinned AppImage + sha256, updated only by `couch-update` with rollback) driven by its CLI: `moonlight-qt stream <host> "<app>" --quit-after` | Mature, hardware-decoded, handles pairing/certs; `--quit-after` returns control to the launcher. Phase 1 is literally `cage moonlight-qt` so there is a working fallback before the launcher exists. |
| Workstation side | PowerShell 5.1, pure ASCII, `-WhatIf`-style dry run by default, `-Apply` to change | Installs/configures Sunshine (winget `LizardByte.Sunshine`), builds the apps list from the installed Steam library, enables Wake-on-LAN, opens only Sunshine's firewall ports. Michael runs it himself (his machine, his UAC). |
| Gamepad on Linux | **xone** (DKMS, Xbox Wireless Adapter dongle) installed by the installer, **Bluetooth (xpadneo) as fallback**, wired xpad always | The dongle is a Microsoft proprietary radio; xone is the maintained driver. DKMS rebuilds on kernel updates and the post-update hook VERIFIES the module loaded, alerting if not. |
| Box art | From Steam's public CDN by appid (no API key) via the workstation exporter; fallback = generated title cards | No metered or keyed service anywhere. |
| Config | One file `/etc/couch/couch.toml` (stdlib `tomllib`) | Host name, MAC, display mode, bitrate, tiles, nightly reboot time, optional ntfy URL. |
| Health | `couch-doctor` CLI + read-only LAN page `http://<nuc>:7777/health` (JSON + tiny HTML) | Fable's daily audit on the media server can pull it; the TV shows a small badge when it is red. |
| Updates | `unattended-upgrades` security-only; `couch-update` (manual/weekly) for Moonlight + Couch with rollback; optional nightly reboot 04:30 | Boring on purpose. |
| Network | **At least one wireless hop is unavoidable** (workstation in the garage, NUC ~100 ft away in the living room, no cable run), so the stream is tuned to the MEASURED link, never assumed: `couch-doctor` measures ping jitter + a 10 s throughput probe to the workstation (iperf3 installed on the workstation by the kit, LAN-only), the launcher defaults to **1080p60 HEVC at ~15 Mbps** and exposes a quality ladder (720p60 10 Mbps -> 1080p60 15/20 -> 1440p60 30 -> 4K60 40) in config, and a link-quality badge appears when jitter > 10 ms or loss > 0.5%. | The 1st-gen Google Wifi mesh backhaul is the weak link (it already degrades under this media server's torrent load - memory google-wifi-root-bottleneck). Honest numbers: a 1st-gen Google Wifi hop typically gives 100-250 Mbps and 3-10 ms, with spikes to 50+ ms under load; 1080p60 at 15 Mbps survives that, 4K often will not. Hardware fixes are Michael's call (section 8). |

## 3. Architecture
```
TV (HDMI) <- NUC: Ubuntu 24.04 ------------------------------------------------------------ LAN (wired) ---- Workstation (Win11, GPU)
   cage (Wayland kiosk) ----- runs ----> couch-launcher (pygame)   <-- gamepad (xone dongle / BT / wired)      Sunshine (host, :47989/47984/47990, UDP 47998-48000)
                                          |  tiles: Steam Big Picture / Desktop / per-game / Pair / Restart     apps.json  <- Setup-Sunshine.ps1 (from Steam library)
                                          |  state machine: IDLE -> WAKING -> WAIT_SUNSHINE -> STREAMING -> IDLE   WoL enabled (NIC + BIOS + Fast Startup off)
                                          +--> moonlight-qt stream <host> "<app>" --quit-after  ===== video/audio/input =====>
   couch-daemon (python, 127.0.0.1:7777 actions; :7777/health read-only on LAN)
          wake(MAC) | list apps (moonlight-qt list) | pair (shows PIN on TV) | restart | doctor
   systemd: couch-kiosk.service (cage -> launcher, Restart=always, WatchdogSec), couch-daemon.service, couch-doctor.timer
```

## 4. Phases (the cloud session works in this order and opens a PR at the end of each)
- **P1 - Bootable kiosk, fallback included.** `install.sh` (idempotent, `--dry-run`, logs, refuses non-24.04): packages,
  moonlight-qt AppImage pinned+sha256, cage, xone DKMS + firmware, `couch` system user, autologin-free kiosk via systemd
  (no getty on tty1, `couch-kiosk.service` = `cage -- /opt/couch/bin/kiosk` which in P1 just runs moonlight-qt
  full-screen), unattended-upgrades security-only, journald limits, `couch-doctor` v0. Plus Ubuntu **autoinstall**
  (cloud-init) YAML + `make-usb.sh` to build an unattended installer USB (`--disk` explicit; refuses >1 disk otherwise).
- **P2 - The Couch launcher.** pygame-ce 10-foot UI: tile grid with box art, focus ring, D-pad/stick nav, A=select,
  B=back, Start=Restart/Pair menu (PIN-gated), status strip (workstation up/asleep, Sunshine ok, gamepad battery if
  exposed, health badge), screens: HOME / WAKING / CONNECTING / STREAMING(hidden) / LOST / PAIR / RESTART. State machine
  with timeouts; supervises the moonlight-qt child (kills zombies, 3 strikes -> LOST screen). Attract-mode dimming after
  10 min, display sleep after 60 min (DPMS), wake on any button.
- **P3 - couch-daemon + self-healing.** WoL by MAC with ARP/mDNS host discovery (no fixed IP); `moonlight-qt list`
  caching; pair flow; watchdog (sd_notify); post-kernel-update hook that verifies xone loaded; `couch-update` with
  rollback; nightly reboot (config); `/health` + `couch-doctor` v1 (the full matrix); optional ntfy POST on red
  (URL from config, never in repo).
- **P4 - Workstation kit (PowerShell 5.1, ASCII, Pester-tested on fixtures).** `Setup-Sunshine.ps1` (-Apply): winget
  install/upgrade Sunshine, firewall rules for Sunshine only, `apps.json` merge (never clobbers hand-made entries) with
  Steam Big Picture + Desktop + per-game entries from `Export-SteamLibrary.ps1` (parses `steamapps\*.acf`, box art
  from the Steam CDN into Sunshine's assets folder), `Set-WakeOnLan.ps1` (NIC magic-packet + Fast Startup off; BIOS
  is a documented manual step), `Check-Sunshine.ps1` (doctor for that side). Backups of every file it touches.
- **P5 - Proof.** Test matrix below green in CI (GitHub Actions on the repo), `docs/RUNBOOK.md` (install from USB,
  first pairing, every failure screen, how to re-pair, how to add a game, how Fable validates over SSH),
  `docs/TESTPLAN.md` (couch steps for Michael), `docs/PLAN-B-WINDOWS.md`.

## 5. Test framework (what the cloud can prove without the hardware)
| Layer | Tool | Gate |
|---|---|---|
| Shell (install.sh, make-usb.sh, couch-doctor, hooks) | shellcheck + bats; install rehearsal inside `ubuntu:24.04` container with `--no-services --no-dkms` | zero shellcheck warnings; rehearsal asserts every file/unit/config lands and a 2nd run changes nothing (idempotence) |
| systemd units | `systemd-analyze verify`; unit-file lint (Restart, WatchdogSec, ProtectSystem, NoNewPrivileges) | pass |
| autoinstall YAML | yamllint + the subiquity schema (`autoinstall-validate` or vendored JSON schema) | pass |
| Launcher (python) | pytest: state machine (every transition + timeout), tile model, input mapping (synthetic SDL events), config parsing incl. bad TOML; headless golden renders (SDL dummy) of all 7 screens at 1920x1080 and 3840x2160, pixel-diffed | 100% of transitions covered; goldens stable |
| Daemon | pytest on a real `ThreadingHTTPServer`: allow-listed actions only (anything else 404), loopback-only for actions, LAN-only for /health, WoL packet bytes exact for a MAC, mock `moonlight-qt` binary recording its argv | pass |
| Workstation PS1 | Pester (pwsh in CI) on fixture `.acf` + `apps.json`: merge never drops existing entries, dry-run writes nothing, ASCII-only + PS5.1-syntax lint (no `??`, no ternary, no `-notlike` on arrays, etc.) | pass |
| Secrets | gitleaks-style grep in CI for token/password/MAC/192.168 patterns outside `docs/` examples | zero hits |
| On hardware (Fable over SSH from the media server, then Michael on the couch) | `couch-doctor --json`; `docs/TESTPLAN.md` | acceptance list in section 1 |

## 6. Guardrails (non-negotiable; the brief repeats them)
- The NUC never holds a workstation login, Windows password, Steam credential or API key. Pairing is Moonlight's own
  PIN + certificate exchange, once.
- Daemon actions are an allow-list (`wake`, `stream <app-from-list>`, `quit`, `pair`, `restart`, `doctor`); no free-form
  commands; actions bind to 127.0.0.1; the LAN page is read-only.
- WoL only to the MAC in config. `stream` only to apps returned by `moonlight-qt list`.
- Installer touches only: packages it names, `/opt/couch`, `/etc/couch`, `/var/lib/couch`, `couch-*` systemd units,
  the getty@tty1 mask, unattended-upgrades config. `--dry-run` prints the plan; second run is a no-op.
- Workstation scripts: dry-run by default; `-Apply` required; backups next to every touched file; they touch ONLY
  Sunshine (install, config, its firewall rules), the NIC WoL property and Fast Startup. Never Steam's files, never
  other firewall rules, never Windows Update, never display settings.
- No pip beyond pygame-ce (pinned, hashed, in `/opt/couch/venv`); daemon is stdlib. No npm, no Electron, no snaps.
- Every failure state has a kid-readable screen and a parent-readable detail line (same rule as Spin to Watch).
- Secrets/IPs/MACs live only in `/etc/couch/couch.toml` on the NUC (mode 0640) - never in the repo.

## 7. Michael's gates (the only human steps; everything else is built first)
- **M0 (now):** create the empty private repo `mlawsonking/couchbox`, start a cloud session on it (suggested name
  "Couchfiddling"), tell Fable "repo's up" -> Fable pushes BRIEF.md + facts; then paste the one-line kickoff into the
  session. Credits: give this session the larger share; plexwheel Round 2 priorities 3-4 are polish.
- **M1:** NUC facts when known (model/CPU gen, RAM, NIC, has Bluetooth?) - `couch-doctor` adapts, but decode caps
  (HEVC 4K, AV1) depend on the iGPU generation; and the dongle model (Xbox Wireless Adapter = USB-A stick).
- **M2:** write the installer USB (`make-usb.sh` output, or plain Ubuntu Server 24.04 + `git clone && sudo ./install.sh`),
  boot the NUC on the TV input, plug the dongle. Give Fable SSH access (the installer adds the media server's public
  key to the `couchadmin` user - Fable generates it on the media server, no password).
- **M3 (workstation, elevated):** `Setup-Sunshine.ps1 -Apply`, `Set-WakeOnLan.ps1 -Apply`, BIOS "Wake on LAN" on, then
  type the PIN from the TV into Sunshine's web UI once.
- **M4:** couch acceptance run with `docs/TESTPLAN.md`.

## 8. Risks and the planned answer
- Xbox dongle on Linux (xone is out-of-tree): installer verifies after reboot; BT pairing path documented; a wired USB
  cable always works. If xone cannot be made reliable on this kernel, the dongle stays on the workstation and the NUC
  uses Bluetooth - the brief requires both paths tested in code.
- Google Wifi mesh latency (garage <-> living room, ~100 ft, no cable): the software answer is adaptive quality + the
  doctor's measurement + a server-side "game mode" (follow-up: the launcher asks the media server's Cortex to drop qBit
  to its alternate limits for the duration of a stream, so torrent traffic stops competing on the mesh). The hardware
  answer, in order of preference: (1) MoCA 2.5 adapters if coax reaches both rooms (~1 Gbps, ~3 ms, the gold
  standard; ~$150 a pair); (2) a dedicated point-to-point 5/6 GHz bridge pair between the rooms (~$100-200, 300-900
  Mbps, 2-5 ms, immune to the mesh); (3) a WiFi 6E/7 mesh with a dedicated backhaul band replacing the Google Wifi
  (already on the roadmap for Plex); (4) powerline as a last resort (unpredictable: 20-300 Mbps depending on the
  wiring). Pick after `couch-doctor` has numbers from the current mesh.
- HDR / 4K120: depends on NUC iGPU + TV; config exposes it, default 1080p60 (or 4K60 if decode caps say so).
- Sunshine's apps.json format drifts between versions: the merge is schema-tolerant and keeps unknown keys; tests pin
  the current documented form.
- Credits run out mid-build: PR per phase; P1 alone already gives a working kiosk.

## 9. Repo layout the cloud session creates
```
couchbox/
  BRIEF.md  GAMEPLAN.md  README.md  facts/lan-facts.json
  installer/ install.sh  make-usb.sh  autoinstall/user-data  files/{systemd,etc,udev}/...
  couch/     launcher/ (pygame app)  daemon/  doctor/  bin/  pyproject + requirements.lock
  workstation/ Setup-Sunshine.ps1 Export-SteamLibrary.ps1 Set-WakeOnLan.ps1 Check-Sunshine.ps1 tests/*.Tests.ps1 fixtures/
  tests/     (pytest, bats, goldens/)   .github/workflows/ci.yml
  docs/      RUNBOOK.md TESTPLAN.md PLAN-B-WINDOWS.md FAILURE-SCREENS.md
```
