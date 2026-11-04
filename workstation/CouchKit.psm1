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
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)
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


# ------------------------------------------------------------------ stream display (Sunshine global prep-cmd)
function Select-StreamMode {
    # Picks the primary-display mode for a client asking for Width x Height @ Fps. Pure (Pester-tested).
    # 1) exact WxH at the requested refresh; 2) exact WxH at the highest refresh <= Fps (else the lowest above);
    # 3) the largest mode with the client's aspect ratio (+-1%) that fits inside WxH; 4) $null = leave the display.
    param([Parameter(Mandatory = $true)][object[]]$Modes, [Parameter(Mandatory = $true)][int]$Width,
          [Parameter(Mandatory = $true)][int]$Height, [int]$Fps = 60)
    if ($Width -le 0 -or $Height -le 0) { return $null }
    $same = @($Modes | Where-Object { $_.Width -eq $Width -and $_.Height -eq $Height })
    if ($same.Count -gt 0) {
        $exact = @($same | Where-Object { $_.Refresh -eq $Fps })
        if ($exact.Count -gt 0) { return $exact[0] }
        $below = @($same | Where-Object { $_.Refresh -le $Fps } | Sort-Object -Property Refresh -Descending)
        if ($below.Count -gt 0) { return $below[0] }
        return @($same | Sort-Object -Property Refresh)[0]
    }
    $aspect = $Width / [double]$Height
    $fit = @($Modes | Where-Object {
            $_.Width -le $Width -and $_.Height -le $Height -and [math]::Abs(($_.Width / [double]$_.Height) - $aspect) -le ($aspect * 0.01)
        })
    if ($fit.Count -eq 0) { return $null }
    $bestArea = ($fit | ForEach-Object { $_.Width * $_.Height } | Measure-Object -Maximum).Maximum
    $biggest = @($fit | Where-Object { ($_.Width * $_.Height) -eq $bestArea })
    $atFps = @($biggest | Where-Object { $_.Refresh -le $Fps } | Sort-Object -Property Refresh -Descending)
    if ($atFps.Count -gt 0) { return $atFps[0] }
    return @($biggest | Sort-Object -Property Refresh)[0]
}

function Test-SameMode {
    param($A, $B)
    if ($null -eq $A -or $null -eq $B) { return $false }
    return ($A.Width -eq $B.Width -and $A.Height -eq $B.Height -and $A.Refresh -eq $B.Refresh)
}

function Get-StreamDisplayPlan {
    # Decides what do/undo/restore must do from the saved state and the current mode. Pure (Pester-tested).
    # Returns @{ SetMode = <mode or $null>; SaveState = <mode or $null>; ClearState = <bool>; Message = <text> }.
    # Invariants: the saved state always holds the user's ORIGINAL mode - a second "do" (or a "do" after a crash
    # left the state behind) never overwrites it; "undo" with no state is a no-op.
    param([Parameter(Mandatory = $true)][ValidateSet('do', 'undo', 'restore')][string]$Action,
          $State, $Current, $Selected)
    $plan = @{ SetMode = $null; SaveState = $null; ClearState = $false; Message = '' }
    if ($Action -eq 'do') {
        if ($null -eq $Selected) { $plan.Message = 'no suitable mode for the client; display left as is'; return $plan }
        if ($null -eq $State) {
            if (Test-SameMode $Current $Selected) { $plan.Message = 'display already in the stream mode'; return $plan }
            $plan.SaveState = $Current
        }
        if (-not (Test-SameMode $Current $Selected)) { $plan.SetMode = $Selected }
        $plan.Message = ('stream mode {0}x{1}@{2}' -f $Selected.Width, $Selected.Height, $Selected.Refresh)
        return $plan
    }
    if ($null -eq $State) { $plan.Message = 'nothing to restore'; return $plan }
    if (-not (Test-SameMode $Current $State)) { $plan.SetMode = $State }
    $plan.ClearState = $true
    $plan.Message = ('restore {0}x{1}@{2}' -f $State.Width, $State.Height, $State.Refresh)
    return $plan
}

function Read-StreamDisplayState {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $o = [System.IO.File]::ReadAllText($Path) | ConvertFrom-Json
        return [pscustomobject]@{ Width = [int]$o.Width; Height = [int]$o.Height; Refresh = [int]$o.Refresh }
    } catch { return $null }
}

function Write-StreamDisplayState {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)]$Mode)
    $o = [ordered]@{ Width = [int]$Mode.Width; Height = [int]$Mode.Height; Refresh = [int]$Mode.Refresh; saved = (Get-Date).ToString('s') }
    Write-Utf8NoBom -Path $Path -Text ($o | ConvertTo-Json -Depth 3)
}

function Initialize-CouchDisplayApi {
    # P/Invoke for EnumDisplaySettings / ChangeDisplaySettingsEx on the PRIMARY display (hardware-only code path).
    if ('CouchDisplay' -as [type]) { return }
    Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class CouchDisplay {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi)]
    public struct DEVMODE {
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName;
        public short dmSpecVersion; public short dmDriverVersion; public short dmSize; public short dmDriverExtra;
        public int dmFields; public int dmPositionX; public int dmPositionY; public int dmDisplayOrientation;
        public int dmDisplayFixedOutput; public short dmColor; public short dmDuplex; public short dmYResolution;
        public short dmTTOption; public short dmCollate;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmFormName;
        public short dmLogPixels; public int dmBitsPerPel; public int dmPelsWidth; public int dmPelsHeight;
        public int dmDisplayFlags; public int dmDisplayFrequency; public int dmICMMethod; public int dmICMIntent;
        public int dmMediaType; public int dmDitherType; public int dmReserved1; public int dmReserved2;
        public int dmPanningWidth; public int dmPanningHeight;
    }
    [DllImport("user32.dll", CharSet = CharSet.Ansi)]
    public static extern bool EnumDisplaySettings(string deviceName, int modeNum, ref DEVMODE devMode);
    [DllImport("user32.dll", CharSet = CharSet.Ansi)]
    public static extern int ChangeDisplaySettingsEx(string deviceName, ref DEVMODE devMode, IntPtr hwnd, int flags, IntPtr lParam);
    public const int ENUM_CURRENT_SETTINGS = -1;
    public const int DM_PELSWIDTH = 0x80000, DM_PELSHEIGHT = 0x100000, DM_DISPLAYFREQUENCY = 0x400000;
    public static DEVMODE New() { DEVMODE d = new DEVMODE(); d.dmSize = (short)Marshal.SizeOf(typeof(DEVMODE)); return d; }
    public static List<int[]> Modes() {
        List<int[]> r = new List<int[]>(); DEVMODE d = New(); int i = 0;
        while (EnumDisplaySettings(null, i, ref d)) { r.Add(new int[] { d.dmPelsWidth, d.dmPelsHeight, d.dmDisplayFrequency }); i++; }
        return r;
    }
    public static int[] Current() { DEVMODE d = New(); EnumDisplaySettings(null, ENUM_CURRENT_SETTINGS, ref d); return new int[] { d.dmPelsWidth, d.dmPelsHeight, d.dmDisplayFrequency }; }
    public static int Set(int w, int h, int hz) {
        DEVMODE d = New(); EnumDisplaySettings(null, ENUM_CURRENT_SETTINGS, ref d);
        d.dmPelsWidth = w; d.dmPelsHeight = h; d.dmDisplayFrequency = hz;
        d.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY;
        return ChangeDisplaySettingsEx(null, ref d, IntPtr.Zero, 0, IntPtr.Zero);
    }
}
"@
}

function Get-CouchDisplayModes {
    Initialize-CouchDisplayApi
    $seen = @{}
    $out = @()
    foreach ($m in [CouchDisplay]::Modes()) {
        $k = '{0}x{1}@{2}' -f $m[0], $m[1], $m[2]
        if (-not $seen.ContainsKey($k)) { $seen[$k] = $true; $out += [pscustomobject]@{ Width = $m[0]; Height = $m[1]; Refresh = $m[2] } }
    }
    return $out
}

function Get-CouchCurrentMode {
    Initialize-CouchDisplayApi
    $m = [CouchDisplay]::Current()
    return [pscustomobject]@{ Width = $m[0]; Height = $m[1]; Refresh = $m[2] }
}

function Set-CouchDisplayMode {
    # Non-persistent change (flags 0 = this session); returns DISP_CHANGE_* (0 = success).
    param([Parameter(Mandatory = $true)]$Mode)
    Initialize-CouchDisplayApi
    return [CouchDisplay]::Set([int]$Mode.Width, [int]$Mode.Height, [int]$Mode.Refresh)
}

# ------------------------------------------------------------------ sunshine.conf (global_prep_cmd)
function Merge-SunshineConf {
    # Adds one global_prep_cmd entry to sunshine.conf text, keeping every other line and any existing prep
    # commands. Returns @{ Text = <new text>; Changed = <bool> }. Pure (Pester-tested).
    param([AllowEmptyString()][string]$Text, [Parameter(Mandatory = $true)][string]$Do,
          [Parameter(Mandatory = $true)][string]$Undo, [string]$Marker = 'Set-StreamDisplay.ps1')
    $entry = [ordered]@{ 'do' = $Do; 'undo' = $Undo; 'elevated' = $false }
    $lines = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrEmpty($Text)) { foreach ($l in ($Text -split "`r?`n")) { $lines.Add($l) } }
    while ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -eq '') { $lines.RemoveAt($lines.Count - 1) }
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^\s*global_prep_cmd\s*=\s*(.*)$') {
            $raw = $Matches[1].Trim()
            $arr = @()
            if ($raw) { $arr = @($raw | ConvertFrom-Json) }
            foreach ($e in $arr) { if ([string]$e.do -like ('*' + $Marker + '*')) { return @{ Text = $Text; Changed = $false } } }
            $all = @($arr) + @([pscustomobject]$entry)
            $lines[$i] = 'global_prep_cmd = ' + (ConvertTo-Json -InputObject $all -Depth 5 -Compress)
            return @{ Text = (($lines -join "`r`n") + "`r`n"); Changed = $true }
        }
    }
    $lines.Add('global_prep_cmd = ' + (ConvertTo-Json -InputObject @([pscustomobject]$entry) -Depth 5 -Compress))
    return @{ Text = (($lines -join "`r`n") + "`r`n"); Changed = $true }
}

function Get-CouchLinkInfo {
    # Link speed + the Realtek power-saving properties that can downshift a gigabit link (report only).
    $names = @('Gigabit Lite', 'Energy-Efficient Ethernet', 'Power Saving Mode', 'Green Ethernet', 'Advanced EEE')
    $out = @()
    foreach ($a in (Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' })) {
        $props = @{}
        foreach ($p in (Get-NetAdapterAdvancedProperty -Name $a.Name -ErrorAction SilentlyContinue)) {
            if ($names -contains [string]$p.DisplayName) { $props[[string]$p.DisplayName] = [string]$p.DisplayValue }
        }
        $out += [pscustomobject]@{ Name = $a.Name; LinkSpeed = [string]$a.LinkSpeed; Media = [string]$a.MediaType; Props = $props }
    }
    return $out
}

function Get-LinkRecommendation {
    # Pure: turns Get-CouchLinkInfo output into advice lines. Never a failure - the owner decides (facts: the
    # Realtek NIC once linked at 100 Mbps with Gigabit Lite / EEE / Power Saving on).
    param([object[]]$Links)
    $lines = @()
    foreach ($l in $Links) {
        $line = $l.Name + ': link ' + $l.LinkSpeed
        $on = @()
        foreach ($k in ($l.Props.Keys | Sort-Object)) { if ($l.Props[$k] -match '^(Enabled|On)$') { $on += $k } }
        if ($l.LinkSpeed -match '^(10|100) Mbps$') { $line += ' - below gigabit: check the cable/port' }
        if ($on.Count -gt 0) { $line += '; consider turning off ' + ($on -join ', ') + ' (can downshift the link; your call, the kit never changes it)' }
        $lines += $line
    }
    return $lines
}


function Get-CouchRestoreTask {
    return Get-ScheduledTask -TaskName 'couchbox display restore' -ErrorAction SilentlyContinue
}

function Register-CouchRestoreTask {
    # At logon of the current user: put the display back if a stream ended without Sunshine's undo (crash, reboot).
    param([Parameter(Mandatory = $true)][string]$ScriptPath)
    $arg = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $ScriptPath + '" restore'
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User ([Environment]::UserDomainName + '\' + [Environment]::UserName)
    Register-ScheduledTask -TaskName 'couchbox display restore' -Action $action -Trigger $trigger | Out-Null
}

function Get-StreamDisplayCommand {
    param([Parameter(Mandatory = $true)][string]$ScriptPath, [Parameter(Mandatory = $true)][string]$Verb)
    return 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $ScriptPath + '" ' + $Verb
}

Export-ModuleMember -Function *
