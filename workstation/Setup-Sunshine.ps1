<#
.SYNOPSIS
  couchbox: install/upgrade Sunshine, open ONLY Sunshine's ports (Private profile, local subnet), and merge the
  couchbox apps (Desktop, Steam Big Picture, one per installed Steam game) into Sunshine's apps.json.
.DESCRIPTION
  Dry run by default: prints every change it would make and changes nothing. Add -Apply to make the changes
  (run from an elevated Windows PowerShell 5.1). Every file it touches gets a timestamped backup beside it.
  Existing apps.json entries are never removed or edited; unknown keys are kept. Targets Sunshine v2026.1103.23515.
.EXAMPLE
  .\Setup-Sunshine.ps1                      # see the plan
  .\Setup-Sunshine.ps1 -Apply               # do it
  .\Setup-Sunshine.ps1 -Apply -LibraryJson .\couchbox-export\steam-library.json
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$SunshineConfigDir,
    [string]$LibraryJson,
    [switch]$SkipWinget,
    [switch]$SkipFirewall,
    [switch]$SkipDisplay
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'CouchKit.psm1') -Force

if (-not $SunshineConfigDir) { $SunshineConfigDir = Get-CouchSunshineConfigDir }
$mode = 'DRY RUN (add -Apply to make changes)'
if ($Apply) { $mode = 'APPLY' }
Write-Host ('couchbox Setup-Sunshine - ' + $mode)

# 1. Sunshine itself
if (-not $SkipWinget) {
    $args1 = @('install', '--exact', '--id', 'LizardByte.Sunshine', '--accept-package-agreements', '--accept-source-agreements', '--silent')
    Write-CouchPlan -Apply:$Apply -Message ('winget ' + ($args1 -join ' ') + '  (upgrades if already installed)')
    if ($Apply) {
        $rc = Invoke-CouchWinget -Arguments $args1
        if ($rc -ne 0) {
            $rc2 = Invoke-CouchWinget -Arguments @('upgrade', '--exact', '--id', 'LizardByte.Sunshine', '--accept-package-agreements', '--accept-source-agreements', '--silent')
            Write-Host ('  winget install rc=' + $rc + ', upgrade rc=' + $rc2 + ' (non-zero upgrade usually means: already current)')
        }
    }
}

# 2. Firewall: only Sunshine's ports, only from the local subnet, only on Private networks.
if (-not $SkipFirewall) {
    $rules = @(
        @{ Name = 'couchbox Sunshine TCP'; Protocol = 'TCP'; Ports = (Get-CouchConstant 'SunshineTcpPorts') },
        @{ Name = 'couchbox Sunshine UDP'; Protocol = 'UDP'; Ports = (Get-CouchConstant 'SunshineUdpPorts') }
    )
    foreach ($r in $rules) {
        if ($null -ne (Get-CouchFirewallRule -DisplayName $r.Name)) {
            Write-Host ('  ok      firewall rule "' + $r.Name + '" exists')
        } else {
            Write-CouchPlan -Apply:$Apply -Message ('add firewall rule "' + $r.Name + '" ' + $r.Protocol + ' ' + ($r.Ports -join ',') + ' Private/LocalSubnet')
            if ($Apply) { New-CouchFirewallRule -DisplayName $r.Name -Protocol $r.Protocol -Ports $r.Ports }
        }
    }
}

# 3. apps.json merge
$appsPath = Join-Path $SunshineConfigDir 'apps.json'
$existing = ''
if (Test-Path -LiteralPath $appsPath) { $existing = [System.IO.File]::ReadAllText($appsPath) }
$new = @(New-SunshineDefaultApps)
if ($LibraryJson) {
    $lib = [System.IO.File]::ReadAllText($LibraryJson) | ConvertFrom-Json
    $artDir = Join-Path $SunshineConfigDir 'couchbox-art'
    foreach ($g in @($lib.games)) {
        if (-not $g.installed) { continue }
        $png = Join-Path $artDir ([string]$g.appid + '.png')
        $img = $null
        if (Test-Path -LiteralPath $png) { $img = $png }
        $new += New-SunshineGameApp -AppId ([int]$g.appid) -Name ([string]$g.name) -ImagePath $img
    }
}
$merged = Merge-SunshineApps -ExistingJson $existing -NewApps $new
if ($merged.Added.Count -eq 0) {
    Write-Host ('  ok      ' + $appsPath + ' already has every couchbox app')
} else {
    Write-CouchPlan -Apply:$Apply -Message ('add ' + $merged.Added.Count + ' app(s) to ' + $appsPath + ': ' + ($merged.Added -join ', '))
    if ($Apply) {
        if (-not (Test-Path -LiteralPath $SunshineConfigDir)) { New-Item -ItemType Directory -Path $SunshineConfigDir | Out-Null }
        $bak = Backup-CouchFile -Path $appsPath
        if ($bak) { Write-Host ('          backup: ' + $bak) }
        Write-Utf8NoBom -Path $appsPath -Text $merged.Json
    }
}
# 4. Stream display: global prep-cmd do/undo so the 3440x1440 ultrawide primary switches to the TV's mode.
if (-not $SkipDisplay) {
    $kitDir = Join-Path $SunshineConfigDir 'couchbox'
    $script = Join-Path $kitDir 'Set-StreamDisplay.ps1'
    foreach ($f in @('Set-StreamDisplay.ps1', 'CouchKit.psm1')) {
        $src = Join-Path $PSScriptRoot $f
        $dst = Join-Path $kitDir $f
        $same = (Test-Path -LiteralPath $dst) -and ([System.IO.File]::ReadAllText($src) -eq [System.IO.File]::ReadAllText($dst))
        if ($same) {
            Write-Host ('  ok      ' + $dst + ' up to date')
        } else {
            Write-CouchPlan -Apply:$Apply -Message ('copy ' + $f + ' to ' + $kitDir)
            if ($Apply) {
                if (-not (Test-Path -LiteralPath $kitDir)) { New-Item -ItemType Directory -Path $kitDir | Out-Null }
                $bak = Backup-CouchFile -Path $dst
                if ($bak) { Write-Host ('          backup: ' + $bak) }
                Copy-Item -LiteralPath $src -Destination $dst -Force
            }
        }
    }
    $confPath = Join-Path $SunshineConfigDir 'sunshine.conf'
    $conf = ''
    if (Test-Path -LiteralPath $confPath) { $conf = [System.IO.File]::ReadAllText($confPath) }
    $m = Merge-SunshineConf -Text $conf -Do (Get-StreamDisplayCommand -ScriptPath $script -Verb 'do') -Undo (Get-StreamDisplayCommand -ScriptPath $script -Verb 'undo')
    if (-not $m.Changed) {
        Write-Host ('  ok      ' + $confPath + ' already has the couchbox display prep-cmd')
    } else {
        Write-CouchPlan -Apply:$Apply -Message ('add global_prep_cmd (Set-StreamDisplay.ps1 do/undo) to ' + $confPath + ' (other settings kept)')
        if ($Apply) {
            $bak = Backup-CouchFile -Path $confPath
            if ($bak) { Write-Host ('          backup: ' + $bak) }
            Write-Utf8NoBom -Path $confPath -Text $m.Text
            Write-Host '          restart SunshineService (or reboot) for sunshine.conf changes to apply'
        }
    }
    if ($null -ne (Get-CouchRestoreTask)) {
        Write-Host '  ok      scheduled task "couchbox display restore" exists'
    } else {
        Write-CouchPlan -Apply:$Apply -Message 'register logon task "couchbox display restore" (puts the display back if a stream ended uncleanly)'
        if ($Apply) { Register-CouchRestoreTask -ScriptPath $script }
    }
}
Write-Host 'Next: open https://localhost:47990 on this PC once to set the Sunshine admin password; pair from the TV (Menu > Pair).'
