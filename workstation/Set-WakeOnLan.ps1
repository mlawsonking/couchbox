<#
.SYNOPSIS
  couchbox: enable Wake-on-LAN (magic packet) on the wired/wireless adapter in use and turn Windows Fast Startup
  off (Fast Startup's hybrid shutdown stops most NICs from waking). Prints the MAC to put in the NUC's couch.toml.
.DESCRIPTION
  Dry run by default; -Apply to change (elevated). Previous values are saved to a backup JSON beside this script.
  The BIOS/UEFI "Wake on LAN" / "Power on by PCI-E" setting is a manual step (see docs/RUNBOOK.md); this script
  never touches firmware, Windows Update or display settings.
#>
[CmdletBinding()]
param([switch]$Apply, [string]$AdapterName, [string]$BackupDir = $PSScriptRoot)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'CouchKit.psm1') -Force

$adapters = @(Get-CouchWolAdapter)
if ($AdapterName) { $adapters = @($adapters | Where-Object { $_.Name -eq $AdapterName }) }
if ($adapters.Count -eq 0) { throw 'no connected physical network adapter found (use -AdapterName)' }
$fs = Get-CouchFastStartup
$backup = [ordered]@{ taken = (Get-Date).ToString('s'); fastStartup = $fs; adapters = $adapters }
$backupPath = Join-Path $BackupDir ('wol-backup-' + (Get-Date).ToString('yyyyMMdd-HHmmss') + '.json')
$changes = 0
foreach ($a in $adapters) {
    Write-Host ('Adapter "' + $a.Name + '" (' + $a.Media + ') MAC ' + $a.Mac + '  -> couch.toml: mac = "' + ($a.Mac -replace '-', ':') + '"')
    if ($a.Magic -eq '1' -and $a.PowerMgmt -eq 'Enabled') {
        Write-Host '  ok      Wake on Magic Packet already enabled'
    } else {
        $changes++
        Write-CouchPlan -Apply:$Apply -Message ('enable Wake on Magic Packet on "' + $a.Name + '" (was ' + $a.Magic + '/' + $a.PowerMgmt + ')')
    }
    if ($a.Media -match '802\.11|Wireless') { Write-Warning 'This is a Wi-Fi adapter: most cannot wake from S5/S4. Wired is strongly preferred for wake.' }
}
if ($fs -eq 0) {
    Write-Host '  ok      Fast Startup is off'
} else {
    $changes++
    Write-CouchPlan -Apply:$Apply -Message ('turn Fast Startup off (HiberbootEnabled ' + $fs + ' -> 0)')
}
if ($Apply -and $changes -gt 0) {
    Write-Utf8NoBom -Path $backupPath -Text ($backup | ConvertTo-Json -Depth 5)
    Write-Host ('          backup of previous values: ' + $backupPath)
    foreach ($a in $adapters) { if (-not ($a.Magic -eq '1' -and $a.PowerMgmt -eq 'Enabled')) { Set-CouchWol -AdapterName $a.Name } }
    if ($fs -ne 0) { Set-CouchFastStartup -Value 0 }
}
Write-Host 'Manual step: enable "Wake on LAN" (or "Power On By PCI-E") in the BIOS/UEFI. This script never touches firmware.'
