<#
.SYNOPSIS
  couchbox: read-only doctor for the workstation side. Changes nothing.
  Service, ports listening, firewall rules, apps.json valid, Wake-on-LAN + Fast Startup, GPU encoder, iperf3 probe,
  link speed and NIC power-saving properties (INFO only: a recommendation, never a failure).
#>
[CmdletBinding()]
param([string]$SunshineConfigDir)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'CouchKit.psm1') -Force
if (-not $SunshineConfigDir) { $SunshineConfigDir = Get-CouchSunshineConfigDir }

$results = New-Object System.Collections.Generic.List[object]
function Add-Result([string]$Check, [string]$Status, [string]$Summary) {
    $results.Add([pscustomobject]@{ Check = $Check; Status = $Status; Summary = $Summary })
}

$svc = Get-CouchService -Name 'SunshineService'
if ($null -eq $svc) { Add-Result 'service' 'FAIL' 'SunshineService not installed (run Setup-Sunshine.ps1 -Apply)' }
elseif ([string]$svc.Status -ne 'Running') { Add-Result 'service' 'FAIL' ('SunshineService is ' + $svc.Status) }
else { Add-Result 'service' 'OK' 'SunshineService running' }

$listening = @(Get-CouchListeningPort)
$missing = @()
foreach ($p in (Get-CouchConstant 'SunshineTcpPorts')) { if (-not ($listening -contains $p)) { $missing += $p } }
if ($missing.Count -eq 0) { Add-Result 'ports' 'OK' 'Sunshine TCP ports listening' } else { Add-Result 'ports' 'FAIL' ('not listening: ' + ($missing -join ',')) }

$fw = @()
foreach ($n in @('couchbox Sunshine TCP', 'couchbox Sunshine UDP')) { if ($null -eq (Get-CouchFirewallRule -DisplayName $n)) { $fw += $n } }
if ($fw.Count -eq 0) { Add-Result 'firewall' 'OK' 'couchbox Sunshine rules present' } else { Add-Result 'firewall' 'WARN' ('missing: ' + ($fw -join ', ')) }

$appsPath = Join-Path $SunshineConfigDir 'apps.json'
try {
    $raw = [System.IO.File]::ReadAllBytes($appsPath)
    if ($raw.Length -ge 3 -and $raw[0] -eq 0xEF -and $raw[1] -eq 0xBB -and $raw[2] -eq 0xBF) { throw 'has a UTF-8 BOM' }
    $doc = [System.Text.Encoding]::UTF8.GetString($raw) | ConvertFrom-Json
    $names = @($doc.apps | ForEach-Object { $_.name })
    $need = @('Desktop', 'Steam Big Picture') | Where-Object { -not ($names -contains $_) }
    if (@($need).Count -gt 0) { Add-Result 'apps.json' 'WARN' ('valid, ' + $names.Count + ' apps; missing ' + (@($need) -join ', ')) }
    else { Add-Result 'apps.json' 'OK' ('valid, ' + $names.Count + ' apps') }
} catch { Add-Result 'apps.json' 'FAIL' ($appsPath + ': ' + $_.Exception.Message) }

$wol = @(Get-CouchWolAdapter)
$on = @($wol | Where-Object { $_.Magic -eq '1' -and $_.PowerMgmt -eq 'Enabled' })
if ($on.Count -gt 0) { Add-Result 'wake-on-lan' 'OK' ('enabled on ' + (($on | ForEach-Object { $_.Name + ' ' + $_.Mac }) -join ', ')) }
else { Add-Result 'wake-on-lan' 'FAIL' 'magic packet not enabled (Set-WakeOnLan.ps1 -Apply)' }
$fs = Get-CouchFastStartup
if ($fs -eq 0) { Add-Result 'fast-startup' 'OK' 'off' } else { Add-Result 'fast-startup' 'FAIL' 'on - the PC may not wake (Set-WakeOnLan.ps1 -Apply)' }

$gpus = @(Get-CouchGpuName)
$enc = @($gpus | Where-Object { $_ -match 'NVIDIA|GeForce|RTX|Radeon|AMD|Intel\(R\) Arc|Arc\(TM\)|Iris Xe|UHD' })
if ($enc.Count -gt 0) { Add-Result 'gpu-encoder' 'OK' ($enc -join '; ') } else { Add-Result 'gpu-encoder' 'WARN' ('no known hardware encoder GPU: ' + ($gpus -join '; ')) }

foreach ($line in @(Get-LinkRecommendation -Links @(Get-CouchLinkInfo))) { Add-Result 'link' 'INFO' $line }

if ($null -ne (Get-CouchProbeTask)) { Add-Result 'link-probe' 'OK' 'iperf3 task present' } else { Add-Result 'link-probe' 'WARN' 'no iperf3 probe (Install-LinkProbe.ps1 -Apply) - the NUC cannot measure throughput' }

foreach ($r in $results) { Write-Host ('{0,-13} {1,-5} {2}' -f $r.Check, $r.Status, $r.Summary) }
$fail = @($results | Where-Object { $_.Status -eq 'FAIL' }).Count
$warn = @($results | Where-Object { $_.Status -eq 'WARN' }).Count
if ($fail -gt 0) { exit 2 } elseif ($warn -gt 0) { exit 1 } else { exit 0 }
