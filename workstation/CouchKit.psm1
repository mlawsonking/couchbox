# CouchKit.psm1 - shared, testable functions for the couchbox workstation kit.
# PowerShell 5.1 compatible, pure ASCII. Every function that changes the machine is a small wrapper here so the
# scripts stay dry-run by default and Pester can mock each side effect.
Set-StrictMode -Version 2.0

$script:SunshineTargetVersion = 'v2026.1103.23515'
$script:FirewallGroup = 'couchbox'
$script:SunshineTcpPorts = @(47984, 47989, 48010)
$script:SunshineUdpPorts = @(47998, 47999, 48000, 48002, 48010)
$script:ProbePort = 5201

function Get-CouchConstant {
    param([Parameter(Mandatory = $true)][string]$Name)
    switch ($Name) {
        'SunshineTargetVersion' { return $script:SunshineTargetVersion }
        'FirewallGroup' { return $script:FirewallGroup }
        'SunshineTcpPorts' { return $script:SunshineTcpPorts }
        'SunshineUdpPorts' { return $script:SunshineUdpPorts }
        'ProbePort' { return $script:ProbePort }
        default { throw "unknown constant $Name" }
    }
}

function Write-CouchPlan {
    # One line per intended change. In dry-run mode this IS the output; with -Apply it is the log.
    param([Parameter(Mandatory = $true)][string]$Message, [switch]$Apply)
    if ($Apply) { Write-Host ('  change  ' + $Message) } else { Write-Host ('  PLAN    ' + $Message) }
}

# ------------------------------------------------------------------ files
function Write-Utf8NoBom {
    # Sunshine's JSON reader must not see a BOM; Set-Content -Encoding UTF8 in 5.1 writes one.
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Text)
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $enc)
}

function Backup-CouchFile {
    # Copies Path to "<Path>.couchbox-<yyyyMMdd-HHmmss>.bak" beside it. Returns the backup path.
    param([Parameter(Mandatory = $true)][string]$Path, [datetime]$Now = (Get-Date))
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $bak = $Path + '.couchbox-' + $Now.ToString('yyyyMMdd-HHmmss') + '.bak'
    Copy-Item -LiteralPath $Path -Destination $bak -Force
    return $bak
}

function Test-AsciiText {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)
    foreach ($ch in $Text.ToCharArray()) { if ([int]$ch -gt 127) { return $false } }
    return $true
}

# ------------------------------------------------------------------ Valve KeyValues (.vdf / .acf)
function ConvertFrom-Vdf {
    # Minimal KeyValues text parser: quoted keys/values, nested { } blocks, // comments. Returns nested hashtables.
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)
    $tokens = New-Object System.Collections.Generic.List[string]
    $i = 0
    $n = $Text.Length
    while ($i -lt $n) {
        $c = $Text[$i]
        if ([char]::IsWhiteSpace($c)) { $i++; continue }
        if ($c -eq '/' -and ($i + 1) -lt $n -and $Text[$i + 1] -eq '/') {
            while ($i -lt $n -and $Text[$i] -ne "`n") { $i++ }
            continue
        }
        if ($c -eq '{' -or $c -eq '}') { $tokens.Add([string]$c); $i++; continue }
        if ($c -eq '"') {
            $sb = New-Object System.Text.StringBuilder
            $i++
            while ($i -lt $n -and $Text[$i] -ne '"') {
                if ($Text[$i] -eq '\' -and ($i + 1) -lt $n) {
                    $nx = $Text[$i + 1]
                    if ($nx -eq 'n') { [void]$sb.Append("`n") }
                    elseif ($nx -eq 't') { [void]$sb.Append("`t") }
                    else { [void]$sb.Append($nx) }
                    $i += 2
                    continue
                }
                [void]$sb.Append($Text[$i])
                $i++
            }
            $i++
            $tokens.Add('"' + $sb.ToString())
            continue
        }
        $start = $i
        while ($i -lt $n -and -not [char]::IsWhiteSpace($Text[$i]) -and $Text[$i] -ne '{' -and $Text[$i] -ne '}') { $i++ }
        $tokens.Add('"' + $Text.Substring($start, $i - $start))
    }
    $pos = 0
    $stack = New-Object System.Collections.Stack
    $root = @{}
    $cur = $root
    while ($pos -lt $tokens.Count) {
        $t = $tokens[$pos]
        if ($t -eq '}') {
            if ($stack.Count -eq 0) { throw 'VDF: unbalanced }' }
            $cur = $stack.Pop()
            $pos++
            continue
        }
        if ($t -eq '{') { throw 'VDF: unexpected {' }
        $key = $t.Substring(1)
        if (($pos + 1) -ge $tokens.Count) { throw "VDF: key '$key' without value" }
        $v = $tokens[$pos + 1]
        if ($v -eq '{') {
            $child = @{}
            $cur[$key] = $child
            $stack.Push($cur)
            $cur = $child
            $pos += 2
        } elseif ($v -eq '}') {
            throw "VDF: key '$key' without value"
        } else {
            $cur[$key] = $v.Substring(1)
            $pos += 2
        }
    }
    if ($stack.Count -ne 0) { throw 'VDF: missing }' }
    return $root
}

function Get-SteamLibraryFolder {
    # All library paths from steamapps\libraryfolders.vdf (new and old formats). The Steam folder itself first.
    param([Parameter(Mandatory = $true)][string]$SteamPath)
    $paths = New-Object System.Collections.Generic.List[string]
    $paths.Add($SteamPath)
    $vdf = Join-Path (Join-Path $SteamPath 'steamapps') 'libraryfolders.vdf'
    if (Test-Path -LiteralPath $vdf) {
        $data = ConvertFrom-Vdf -Text ([System.IO.File]::ReadAllText($vdf))
        $lf = $null
        foreach ($k in @('libraryfolders', 'LibraryFolders')) { if ($data.ContainsKey($k)) { $lf = $data[$k] } }
        if ($null -ne $lf) {
            foreach ($key in ($lf.Keys | Sort-Object)) {
                if ($key -notmatch '^\d+$') { continue }
                $entry = $lf[$key]
                $p = $null
                if ($entry -is [hashtable]) { if ($entry.ContainsKey('path')) { $p = $entry['path'] } } else { $p = [string]$entry }
                if ($p) {
                    $p = $p -replace '\\\\', '\'
                    $already = $false
                    foreach ($x in $paths) { if ($x.TrimEnd('\', '/') -ieq $p.TrimEnd('\', '/')) { $already = $true } }
                    if (-not $already) { $paths.Add($p) }
                }
            }
        }
    }
    return , $paths.ToArray()
}

function Read-SteamAppManifest {
    # Parses steamapps\appmanifest_<id>.acf -> [pscustomobject]@{ appid; name; installed }. Tools/redists skipped.
    param([Parameter(Mandatory = $true)][string]$Path)
    $data = ConvertFrom-Vdf -Text ([System.IO.File]::ReadAllText($Path))
    if (-not $data.ContainsKey('AppState')) { return $null }
    $s = $data['AppState']
    if (-not ($s.ContainsKey('appid') -and $s.ContainsKey('name'))) { return $null }
    $name = [string]$s['name']
    if ($name -match '^(Steamworks Common Redistributables|Steam Linux Runtime|Proton )') { return $null }
    $installed = $false
    if ($s.ContainsKey('StateFlags')) { $installed = (([int]$s['StateFlags']) -band 4) -eq 4 }
    return [pscustomobject]@{ appid = [int]$s['appid']; name = $name; installed = $installed }
}

function Get-SteamGame {
    param([Parameter(Mandatory = $true)][string]$SteamPath)
    $games = @{}
    foreach ($lib in (Get-SteamLibraryFolder -SteamPath $SteamPath)) {
        $apps = Join-Path $lib 'steamapps'
        if (-not (Test-Path -LiteralPath $apps)) { continue }
        foreach ($f in (Get-ChildItem -LiteralPath $apps -Filter 'appmanifest_*.acf' -File)) {
            try { $g = Read-SteamAppManifest -Path $f.FullName } catch { Write-Warning ("skipping " + $f.Name + ": " + $_); continue }
            if ($null -ne $g -and -not $games.ContainsKey($g.appid)) { $games[$g.appid] = $g }
        }
    }
    return @($games.Values | Sort-Object -Property name)
}

# ------------------------------------------------------------------ Sunshine apps.json
function New-SunshineDefaultApps {
    # The two base entries, in the form documented by Sunshine (src_assets/windows/assets/apps.json, docs/app_examples.md).
    $desktop = [ordered]@{ 'name' = 'Desktop'; 'image-path' = 'desktop.png' }
    $bigPicture = [ordered]@{
        'name'        = 'Steam Big Picture'
        'cmd'         = 'steam://open/bigpicture'
        'prep-cmd'    = @([ordered]@{ 'do' = ''; 'undo' = 'steam://close/bigpicture' })
        'auto-detach' = $true
        'wait-all'    = $true
        'image-path'  = 'steam.png'
    }
    return @($desktop, $bigPicture)
}

function New-SunshineGameApp {
    param([Parameter(Mandatory = $true)][int]$AppId, [Parameter(Mandatory = $true)][string]$Name, [string]$ImagePath)
    $app = [ordered]@{ 'name' = $Name; 'detached' = @('steam://rungameid/' + $AppId) }
    if ($ImagePath) { $app['image-path'] = $ImagePath }
    return $app
}

function Merge-SunshineApps {
    # Returns @{ Json = <new text>; Added = <names> }. Never removes or edits an existing entry; keeps every unknown
    # top-level key and every unknown per-app key; adds only names that are not present (case-insensitive).
    param([AllowEmptyString()][string]$ExistingJson, [object[]]$NewApps)
    if ([string]::IsNullOrWhiteSpace($ExistingJson)) {
        $doc = [pscustomobject]@{ env = [pscustomobject]@{}; apps = @() }
    } else {
        $doc = $ExistingJson | ConvertFrom-Json
        if ($null -eq $doc -or $doc -is [array]) { throw 'apps.json: top level must be an object' }
    }
    if (-not ($doc.PSObject.Properties.Name -contains 'apps')) {
        $doc | Add-Member -NotePropertyName 'apps' -NotePropertyValue @()
    }
    $list = New-Object System.Collections.ArrayList
    foreach ($a in @($doc.apps)) { if ($null -ne $a) { [void]$list.Add($a) } }
    $have = @{}
    foreach ($a in $list) { if ($a.PSObject.Properties.Name -contains 'name') { $have[([string]$a.name).ToLowerInvariant()] = $true } }
    $added = New-Object System.Collections.Generic.List[string]
    foreach ($n in $NewApps) {
        $key = ([string]$n['name']).ToLowerInvariant()
        if ($have.ContainsKey($key)) { continue }
        [void]$list.Add([pscustomobject]$n)
        $have[$key] = $true
        $added.Add([string]$n['name'])
    }
    $doc.apps = $list.ToArray()
    $json = $doc | ConvertTo-Json -Depth 20
    return @{ Json = $json; Added = $added.ToArray() }
}

# ------------------------------------------------------------------ side effects (mocked in tests)
function Invoke-CouchWinget {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & winget @Arguments
    return $LASTEXITCODE
}

function Get-CouchFirewallRule {
    param([Parameter(Mandatory = $true)][string]$DisplayName)
    return Get-NetFirewallRule -DisplayName $DisplayName -ErrorAction SilentlyContinue
}

function New-CouchFirewallRule {
    # Inbound allow, Private profile, LocalSubnet only, grouped "couchbox" so the kit never touches other rules.
    param([Parameter(Mandatory = $true)][string]$DisplayName, [Parameter(Mandatory = $true)][string]$Protocol,
          [Parameter(Mandatory = $true)][int[]]$Ports)
    New-NetFirewallRule -DisplayName $DisplayName -Group $script:FirewallGroup -Direction Inbound -Action Allow `
        -Protocol $Protocol -LocalPort $Ports -Profile Private -RemoteAddress LocalSubnet | Out-Null
}

function Get-CouchSunshineConfigDir {
    $pf = [Environment]::GetFolderPath('ProgramFiles')
    return Join-Path (Join-Path $pf 'Sunshine') 'config'
}

function Get-CouchFastStartup {
    $v = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled -ErrorAction SilentlyContinue
    if ($null -eq $v) { return $null }
    return [int]$v.HiberbootEnabled
}

function Set-CouchFastStartup {
    param([Parameter(Mandatory = $true)][int]$Value)
    Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled -Value $Value -Type DWord
}

function Get-CouchWolAdapter {
    # Physical, connected adapters with their Wake-on-Magic-Packet state.
    $out = @()
    foreach ($a in (Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' })) {
        $prop = Get-NetAdapterAdvancedProperty -Name $a.Name -RegistryKeyword '*WakeOnMagicPacket' -ErrorAction SilentlyContinue
        $pm = Get-NetAdapterPowerManagement -Name $a.Name -ErrorAction SilentlyContinue
        $val = $null
        if ($null -ne $prop) { $val = [string]$prop.RegistryValue }
        $pmv = $null
        if ($null -ne $pm) { $pmv = [string]$pm.WakeOnMagicPacket }
        $out += [pscustomobject]@{ Name = $a.Name; Mac = $a.MacAddress; Magic = $val; PowerMgmt = $pmv; Media = [string]$a.MediaType }
    }
    return $out
}

function Set-CouchWol {
    param([Parameter(Mandatory = $true)][string]$AdapterName)
    Set-NetAdapterAdvancedProperty -Name $AdapterName -RegistryKeyword '*WakeOnMagicPacket' -RegistryValue 1 -ErrorAction SilentlyContinue
    Set-NetAdapterPowerManagement -Name $AdapterName -WakeOnMagicPacket Enabled -ErrorAction SilentlyContinue
}

function Save-CouchUrl {
    param([Parameter(Mandatory = $true)][string]$Url, [Parameter(Mandatory = $true)][string]$Path)
    Invoke-WebRequest -Uri $Url -OutFile $Path -UseBasicParsing -TimeoutSec 30
}

function Convert-CouchJpegToPng {
    # Sunshine expects PNG box art; the Steam CDN serves JPEG. System.Drawing ships with Windows PowerShell 5.1.
    param([Parameter(Mandatory = $true)][string]$Source, [Parameter(Mandatory = $true)][string]$Destination)
    Add-Type -AssemblyName System.Drawing
    $img = [System.Drawing.Image]::FromFile($Source)
    try { $img.Save($Destination, [System.Drawing.Imaging.ImageFormat]::Png) } finally { $img.Dispose() }
}


function Get-CouchService {
    param([Parameter(Mandatory = $true)][string]$Name)
    return Get-Service -Name $Name -ErrorAction SilentlyContinue
}

function Get-CouchListeningPort {
    return @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ForEach-Object { [int]$_.LocalPort } | Sort-Object -Unique)
}

function Get-CouchGpuName {
    return @(Get-CimInstance -ClassName Win32_VideoController -ErrorAction SilentlyContinue | ForEach-Object { [string]$_.Name })
}

function Get-CouchIperfPath {
    $c = Get-Command iperf3.exe -ErrorAction SilentlyContinue
    if ($null -ne $c) { return $c.Source }
    $hit = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages') -Filter iperf3.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $hit) { return $hit.FullName }
    return $null
}

function Get-CouchProbeTask {
    return Get-ScheduledTask -TaskName 'couchbox iperf3' -ErrorAction SilentlyContinue
}

function Register-CouchProbeTask {
    param([Parameter(Mandatory = $true)][string]$Exe, [Parameter(Mandatory = $true)][int]$Port)
    $action = New-ScheduledTaskAction -Execute $Exe -Argument ('-s -p ' + $Port)
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\LOCAL SERVICE' -LogonType ServiceAccount
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName 'couchbox iperf3' -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
    Start-ScheduledTask -TaskName 'couchbox iperf3'
}

Export-ModuleMember -Function *
