# Plan B: a Windows kiosk on the NUC (documented, not built)

The decision (GAMEPLAN.md section 2) is Ubuntu Server 24.04 + cage. This page exists so that, if Linux on the NUC
turns out to be a dead end (for example the Xbox adapter cannot be made reliable with xone AND Bluetooth is not
acceptable), Michael has a tested-in-principle fallback. **Nothing here is built or tested in this repo.**

## When to switch

Only if, after the RUNBOOK's gamepad section and the TESTPLAN, one of these stays true for a week:

- The Xbox Wireless Adapter drops pads repeatedly under xone and Bluetooth (xpadneo) is not good enough.
- The NUC's iGPU has no working VA-API HEVC/H.264 decode on Linux (`couch-doctor` decode line red) but does on
  Windows.
- HDR is a must and the Linux stack cannot deliver it on this TV.

## Shape of the Windows build

| Piece | Linux (built) | Windows (plan B) |
|---|---|---|
| OS | Ubuntu Server 24.04 | Windows 11 Pro (or IoT Enterprise LTSC if licensable) |
| Kiosk | cage + `couch-kiosk.service` | Assigned Access / Shell Launcher v2 replacing explorer.exe with the launcher; auto-logon of a local standard account |
| Launcher | Couch (pygame-ce) | the same Couch code: pygame-ce runs on Windows; the state machine, renderer and tests are platform-neutral |
| Stream client | moonlight-qt AppImage | moonlight-qt Windows installer (pinned + sha256), same CLI: `Moonlight.exe stream <host> "<app>" --quit-after` |
| Gamepad | xone / xpadneo / xpad | native Xbox Wireless Adapter driver (works out of the box today on the workstation) |
| Daemon | `couch-daemon` systemd unit | the same Python daemon as a Windows service (e.g. via `pythonservice`/NSSM), loopback actions + LAN `/health` |
| Doctor | `couch-doctor` (sysfs, vainfo) | rewrite the probes: DXGI adapters, Media Foundation decoders, `Get-PnpDevice` for pads |
| Updates | unattended-upgrades security only, `couch-update` | Windows Update for Business: active hours, deferral, no restart during kiosk use; `couch-update` re-pointed at the Windows paths |
| Install | `install.sh` + autoinstall USB | an `autounattend.xml` + a PowerShell provisioning script in the style of `workstation/` (dry run, -Apply) |

## What changes in the code

- `couch/launcher/backend.py`: process-group kill becomes a job object (`CREATE_NEW_PROCESS_GROUP` +
  `taskkill /T /F` fallback).
- `couch/launcher/app.py`: restart uses `shutdown /r /t 0` through the daemon (the kiosk account is not admin).
- `couch/doctor/couch-doctor`: a Windows implementation of each check; the JSON shape stays identical so
  `/health`, the TV badge and Fable's audit do not change.
- `installer/`: replaced; `couch/`, `tests/` (state machine, input, goldens, daemon) carry over unchanged.

## Costs and risks

- Windows Update reboots and "finish setting up your device" screens are the classic kiosk failure: needs
  policy-level control (Pro or better) and a test month.
- No cloud CI equivalent for the kiosk shell; Shell Launcher behaviour can only be proven on hardware.
- A Windows licence for the NUC.
- Larger attack surface on the LAN than a minimal Ubuntu server.
