<#
.SYNOPSIS
  couchbox: Sunshine global prep-cmd that switches the PRIMARY display to the stream's resolution/refresh while a
  client streams, and restores the user's mode afterwards. The workstation's primary is a 3440x1440 ultrawide;
  Sunshine captures the primary, so without this a 16:9 TV gets a letterboxed picture.
.DESCRIPTION
  Set-StreamDisplay.ps1 do        called by Sunshine when a stream starts. Uses SUNSHINE_CLIENT_WIDTH /
                                  SUNSHINE_CLIENT_HEIGHT / SUNSHINE_CLIENT_FPS (set by Sunshine from Moonlight's
                                  request). Saves the current mode in a state file (only if none is saved yet),
                                  then switches the primary to the best matching mode.
  Set-StreamDisplay.ps1 undo      called by Sunshine when the stream ends: restores the saved mode, deletes the state.
  Set-StreamDisplay.ps1 restore   safety net at logon (scheduled task "couchbox display restore"): same as undo;
                                  a no-op when nothing is saved.
  Idempotent: a second "do" never overwrites the saved original mode. The change is not persisted to the registry
  (a reboot alone also restores the user's mode). Installed by Setup-Sunshine.ps1 -Apply into Sunshine's config
  folder. Mode selection and state handling are Pester-tested; the ChangeDisplaySettingsEx call itself is
  hardware-only. Alternative (documented in docs/RUNBOOK.md, not installed): a virtual display driver.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)][ValidateSet('do', 'undo', 'restore')][string]$Action,
    [string]$StatePath = (Join-Path $PSScriptRoot 'stream-display-state.json'),
    [int]$Width = [int]('0' + $env:SUNSHINE_CLIENT_WIDTH),
    [int]$Height = [int]('0' + $env:SUNSHINE_CLIENT_HEIGHT),
    [int]$Fps = [int]('0' + $env:SUNSHINE_CLIENT_FPS)
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'CouchKit.psm1') -Force
if ($Fps -le 0) { $Fps = 60 }

$state = Read-StreamDisplayState -Path $StatePath
$current = Get-CouchCurrentMode
$selected = $null
if ($Action -eq 'do') { $selected = Select-StreamMode -Modes @(Get-CouchDisplayModes) -Width $Width -Height $Height -Fps $Fps }
$plan = Get-StreamDisplayPlan -Action $Action -State $state -Current $current -Selected $selected
if ($null -ne $plan.SaveState) { Write-StreamDisplayState -Path $StatePath -Mode $plan.SaveState }
if ($null -ne $plan.SetMode) {
    $rc = Set-CouchDisplayMode -Mode $plan.SetMode
    if ($rc -ne 0) {
        Write-Warning ('ChangeDisplaySettingsEx returned ' + $rc + ' for ' + $plan.Message + '; state kept for the next undo')
        exit 0          # never fail the stream because of the display
    }
}
if ($plan.ClearState) { Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue }
Write-Host ('couchbox Set-StreamDisplay ' + $Action + ': ' + $plan.Message)
exit 0
