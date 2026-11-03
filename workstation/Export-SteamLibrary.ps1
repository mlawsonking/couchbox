<#
.SYNOPSIS
  couchbox: list installed Steam games (all library folders) into steam-library.json and fetch each game's box art
  from Steam's public CDN (library_600x900.jpg by appid; no key, no account).
.DESCRIPTION
  Dry run by default (prints what it found and what it would download). With -Apply it writes:
    <OutDir>\steam-library.json and <OutDir>\art\<appid>.jpg      -> copy to the NUC: couch-update --import <dir>
    <Sunshine config>\couchbox-art\<appid>.png (unless -NoSunshineArt) -> used by Setup-Sunshine.ps1 -LibraryJson
  It only READS Steam's files.
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$SteamPath = (Join-Path ${env:ProgramFiles(x86)} 'Steam'),
    [string]$OutDir = (Join-Path (Get-Location) 'couchbox-export'),
    [string]$SunshineConfigDir,
    [switch]$NoArt,
    [switch]$NoSunshineArt
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'CouchKit.psm1') -Force

if (-not (Test-Path -LiteralPath $SteamPath)) { throw ('Steam not found at ' + $SteamPath + ' (use -SteamPath)') }
if (-not $SunshineConfigDir) { $SunshineConfigDir = Get-CouchSunshineConfigDir }
$games = @(Get-SteamGame -SteamPath $SteamPath)
$installed = @($games | Where-Object { $_.installed })
Write-Host ('Found ' + $games.Count + ' Steam apps (' + $installed.Count + ' fully installed) in ' + (Get-SteamLibraryFolder -SteamPath $SteamPath).Count + ' library folder(s).')
foreach ($g in $installed) { Write-Host ('  ' + $g.appid + '  ' + $g.name) }

$json = (@{ generated = (Get-Date).ToString('s'); games = $games } | ConvertTo-Json -Depth 5)
$libPath = Join-Path $OutDir 'steam-library.json'
Write-CouchPlan -Apply:$Apply -Message ('write ' + $libPath)
if ($Apply) {
    New-Item -ItemType Directory -Force -Path (Join-Path $OutDir 'art') | Out-Null
    Write-Utf8NoBom -Path $libPath -Text $json
}
if ($NoArt) { return }
$sunArt = Join-Path $SunshineConfigDir 'couchbox-art'
foreach ($g in $installed) {
    $urls = @(
        ('https://cdn.cloudflare.steamstatic.com/steam/apps/' + $g.appid + '/library_600x900.jpg'),
        ('https://steamcdn-a.akamaihd.net/steam/apps/' + $g.appid + '/library_600x900.jpg')
    )
    $jpg = Join-Path (Join-Path $OutDir 'art') ([string]$g.appid + '.jpg')
    if (Test-Path -LiteralPath $jpg) { continue }
    Write-CouchPlan -Apply:$Apply -Message ('download ' + $urls[0])
    if (-not $Apply) { continue }
    $got = $false
    foreach ($url in $urls) {
        try { Save-CouchUrl -Url $url -Path $jpg; $got = $true; break } catch { $got = $false }
    }
    if (-not $got) {
        Write-Warning ('no box art for ' + $g.name + ' (' + $g.appid + '); the TV shows a generated card')
        continue
    }
    if (-not $NoSunshineArt) {
        if (-not (Test-Path -LiteralPath $sunArt)) { New-Item -ItemType Directory -Force -Path $sunArt | Out-Null }
        try { Convert-CouchJpegToPng -Source $jpg -Destination (Join-Path $sunArt ([string]$g.appid + '.png')) } catch { Write-Warning ('PNG conversion failed for ' + $g.appid) }
    }
}
Write-Host ('Next: .\Setup-Sunshine.ps1 -Apply -LibraryJson "' + $libPath + '"; then on the NUC: sudo /opt/couch/bin/couch-update --import <copy of ' + $OutDir + '>')
