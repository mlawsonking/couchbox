<#
.SYNOPSIS
  couchbox: install iperf3 as a LAN-only measurement endpoint so couch-doctor on the NUC can measure the real
  garage-to-living-room path (throughput, which picks the stream quality).
.DESCRIPTION
  Dry run by default; -Apply to change (elevated). Installs iperf3 via winget, registers a scheduled task
  "couchbox iperf3" that runs "iperf3 -s" at startup as LOCAL SERVICE, and adds ONE firewall rule: TCP 5201 inbound
  from the local subnet on Private networks. Remove with: Unregister-ScheduledTask "couchbox iperf3";
  Remove-NetFirewallRule -DisplayName "couchbox iperf3".
#>
[CmdletBinding()]
param([switch]$Apply, [switch]$SkipWinget)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'CouchKit.psm1') -Force

$port = Get-CouchConstant 'ProbePort'
if (-not $SkipWinget) {
    $wargs = @('install', '--exact', '--id', 'ar51an.iPerf3', '--accept-package-agreements', '--accept-source-agreements', '--silent')
    Write-CouchPlan -Apply:$Apply -Message ('winget ' + ($wargs -join ' '))
    if ($Apply) { [void](Invoke-CouchWinget -Arguments $wargs) }
}
$exe = Get-CouchIperfPath
if ($null -ne (Get-CouchProbeTask)) {
    Write-Host '  ok      scheduled task "couchbox iperf3" exists'
} else {
    Write-CouchPlan -Apply:$Apply -Message ('register scheduled task "couchbox iperf3": ' + $exe + ' -s -p ' + $port + ' at startup as LOCAL SERVICE')
    if ($Apply) {
        if (-not $exe) { throw 'iperf3.exe not found after winget install; open a new shell and re-run' }
        Register-CouchProbeTask -Exe $exe -Port $port
    }
}
if ($null -ne (Get-CouchFirewallRule -DisplayName 'couchbox iperf3')) {
    Write-Host '  ok      firewall rule "couchbox iperf3" exists'
} else {
    Write-CouchPlan -Apply:$Apply -Message ('add firewall rule "couchbox iperf3" TCP ' + $port + ' Private/LocalSubnet')
    if ($Apply) { New-CouchFirewallRule -DisplayName 'couchbox iperf3' -Protocol TCP -Ports @($port) }
}
