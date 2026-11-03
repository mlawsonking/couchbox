# Runs the real scripts with every machine-changing wrapper mocked. Dry run (no -Apply) must write nothing.
BeforeAll {
    $script:Root = Split-Path -Parent $PSScriptRoot
    $script:Fix = Join-Path $Root 'fixtures'
    Import-Module (Join-Path $Root 'CouchKit.psm1') -Force
    function Get-TreeState([string]$Path) {
        return @(Get-ChildItem -Recurse -Force -LiteralPath $Path | ForEach-Object { $_.FullName + '|' + $_.Length + '|' + $_.LastWriteTimeUtc.Ticks })
    }
    function New-SunshineDir {
        $d = Join-Path $TestDrive ('sunshine-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory $d | Out-Null
        Copy-Item (Join-Path $Fix 'apps.json') (Join-Path $d 'apps.json')
        return $d
    }
}

Describe 'Setup-Sunshine.ps1' {
    BeforeEach {
        Mock Invoke-CouchWinget { return 0 } -ModuleName CouchKit
        Mock Invoke-CouchWinget { return 0 }
        Mock Get-CouchFirewallRule { return $null }
        Mock New-CouchFirewallRule { }
    }
    It 'dry run: prints the plan, writes nothing, calls nothing' {
        $d = New-SunshineDir
        $before = Get-TreeState $d
        $out = & (Join-Path $Root 'Setup-Sunshine.ps1') -SunshineConfigDir $d -LibraryJson (Join-Path $Fix 'steam-library.json') 6>&1 | Out-String
        $out | Should -Match 'DRY RUN'
        $out | Should -Match 'PLAN    add firewall rule "couchbox Sunshine TCP" TCP 47984,47989,48010'
        $out | Should -Match 'PLAN    add 3 app\(s\)'
        (Get-TreeState $d) | Should -Be $before
        Should -Invoke Invoke-CouchWinget -Times 0
        Should -Invoke New-CouchFirewallRule -Times 0
    }
    It '-Apply: backs up, merges, opens only the two Sunshine rules' {
        $d = New-SunshineDir
        & (Join-Path $Root 'Setup-Sunshine.ps1') -Apply -SunshineConfigDir $d -LibraryJson (Join-Path $Fix 'steam-library.json') 6>&1 | Out-Null
        @(Get-ChildItem $d -Filter 'apps.json.couchbox-*.bak').Count | Should -Be 1
        $doc = [System.IO.File]::ReadAllText((Join-Path $d 'apps.json')) | ConvertFrom-Json
        ($doc.apps | ForEach-Object { $_.name }) | Should -Be @('Desktop', "Michael's Emulator", 'Steam Big Picture', 'Celeste', 'Hollow Knight')
        Should -Invoke New-CouchFirewallRule -Times 2
        Should -Invoke New-CouchFirewallRule -Times 1 -ParameterFilter { $DisplayName -eq 'couchbox Sunshine UDP' -and $Protocol -eq 'UDP' }
    }
    It '-Apply twice: second run changes nothing' {
        $d = New-SunshineDir
        & (Join-Path $Root 'Setup-Sunshine.ps1') -Apply -SkipWinget -SkipFirewall -SunshineConfigDir $d 6>&1 | Out-Null
        $mid = Get-TreeState $d
        $out = & (Join-Path $Root 'Setup-Sunshine.ps1') -Apply -SkipWinget -SkipFirewall -SunshineConfigDir $d 6>&1 | Out-String
        $out | Should -Match 'already has every couchbox app'
        (Get-TreeState $d) | Should -Be $mid
    }
    It 'creates apps.json when Sunshine has none yet' {
        $d = Join-Path $TestDrive 'fresh'
        & (Join-Path $Root 'Setup-Sunshine.ps1') -Apply -SkipWinget -SkipFirewall -SunshineConfigDir $d 6>&1 | Out-Null
        $doc = [System.IO.File]::ReadAllText((Join-Path $d 'apps.json')) | ConvertFrom-Json
        @($doc.apps).Count | Should -Be 2
    }
}

Describe 'Export-SteamLibrary.ps1' {
    BeforeEach {
        $script:steam = Join-Path $TestDrive ('Steam' + [guid]::NewGuid().ToString('N'))
        $lib2 = Join-Path $TestDrive ('Lib2' + [guid]::NewGuid().ToString('N'))
        Copy-Item -Recurse (Join-Path $Fix 'steam') $steam
        Copy-Item -Recurse (Join-Path $Fix 'lib2') $lib2
        $vdf = Join-Path (Join-Path $steam 'steamapps') 'libraryfolders.vdf'
        [System.IO.File]::WriteAllText($vdf, [System.IO.File]::ReadAllText($vdf).Replace('@STEAM@', $steam).Replace('@LIB2@', $lib2))
        Mock Save-CouchUrl { [System.IO.File]::WriteAllBytes($Path, [byte[]](0xFF, 0xD8, 0xFF, 0xE0)) }
        Mock Convert-CouchJpegToPng { [System.IO.File]::WriteAllBytes($Destination, [byte[]](0x89, 0x50)) }
    }
    It 'dry run writes nothing anywhere' {
        $out = Join-Path $TestDrive 'export-dry'
        $sun = Join-Path $TestDrive 'sun-dry'
        $before = Get-TreeState $steam
        & (Join-Path $Root 'Export-SteamLibrary.ps1') -SteamPath $steam -OutDir $out -SunshineConfigDir $sun 6>&1 | Out-Null
        Test-Path $out | Should -BeFalse
        Test-Path $sun | Should -BeFalse
        (Get-TreeState $steam) | Should -Be $before
        Should -Invoke Save-CouchUrl -Times 0
    }
    It '-Apply writes steam-library.json + art for installed games only, Steam untouched' {
        $out = Join-Path $TestDrive 'export'
        $sun = Join-Path $TestDrive 'sun'
        $before = Get-TreeState $steam
        & (Join-Path $Root 'Export-SteamLibrary.ps1') -Apply -SteamPath $steam -OutDir $out -SunshineConfigDir $sun 6>&1 | Out-Null
        $lib = [System.IO.File]::ReadAllText((Join-Path $out 'steam-library.json')) | ConvertFrom-Json
        @($lib.games).Count | Should -Be 3
        Test-Path (Join-Path $out 'art/504230.jpg') | Should -BeTrue
        Test-Path (Join-Path $out 'art/1145360.jpg') | Should -BeFalse
        Test-Path (Join-Path $sun 'couchbox-art/367520.png') | Should -BeTrue
        Should -Invoke Save-CouchUrl -Times 2 -ParameterFilter { $Url -like 'https://cdn.cloudflare.steamstatic.com/steam/apps/*/library_600x900.jpg' }
        (Get-TreeState $steam) | Should -Be $before
    }
}

Describe 'Set-WakeOnLan.ps1' {
    BeforeEach {
        Mock Get-CouchWolAdapter { @([pscustomobject]@{ Name = 'Ethernet'; Mac = 'AA-BB-CC-DD-EE-FF'; Magic = '0'; PowerMgmt = 'Disabled'; Media = '802.3' }) }
        Mock Get-CouchFastStartup { 1 }
        Mock Set-CouchWol { }
        Mock Set-CouchFastStartup { }
    }
    It 'dry run: shows the MAC for couch.toml, changes nothing' {
        $bd = Join-Path $TestDrive 'wol-dry'
        New-Item -ItemType Directory $bd | Out-Null
        $out = & (Join-Path $Root 'Set-WakeOnLan.ps1') -BackupDir $bd 6>&1 | Out-String
        $out | Should -Match 'mac = "AA:BB:CC:DD:EE:FF"'
        $out | Should -Match 'PLAN    turn Fast Startup off'
        Should -Invoke Set-CouchWol -Times 0
        Should -Invoke Set-CouchFastStartup -Times 0
        @(Get-ChildItem $bd).Count | Should -Be 0
    }
    It '-Apply: saves previous values then changes WoL + Fast Startup only' {
        $bd = Join-Path $TestDrive 'wol'
        New-Item -ItemType Directory $bd | Out-Null
        & (Join-Path $Root 'Set-WakeOnLan.ps1') -Apply -BackupDir $bd 6>&1 | Out-Null
        $bak = @(Get-ChildItem $bd -Filter 'wol-backup-*.json')
        $bak.Count | Should -Be 1
        ([System.IO.File]::ReadAllText($bak[0].FullName) | ConvertFrom-Json).fastStartup | Should -Be 1
        Should -Invoke Set-CouchWol -Times 1 -ParameterFilter { $AdapterName -eq 'Ethernet' }
        Should -Invoke Set-CouchFastStartup -Times 1 -ParameterFilter { $Value -eq 0 }
    }
    It 'already configured: nothing to do even with -Apply' {
        Mock Get-CouchWolAdapter { @([pscustomobject]@{ Name = 'Ethernet'; Mac = 'AA-BB-CC-DD-EE-FF'; Magic = '1'; PowerMgmt = 'Enabled'; Media = '802.3' }) }
        Mock Get-CouchFastStartup { 0 }
        $bd = Join-Path $TestDrive 'wol2'
        New-Item -ItemType Directory $bd | Out-Null
        & (Join-Path $Root 'Set-WakeOnLan.ps1') -Apply -BackupDir $bd 6>&1 | Out-Null
        Should -Invoke Set-CouchWol -Times 0
        @(Get-ChildItem $bd).Count | Should -Be 0
    }
}

Describe 'Install-LinkProbe.ps1' {
    BeforeEach {
        Mock Invoke-CouchWinget { 0 }
        Mock Get-CouchIperfPath { 'C:\iperf3\iperf3.exe' }
        Mock Get-CouchProbeTask { $null }
        Mock Register-CouchProbeTask { }
        Mock Get-CouchFirewallRule { $null }
        Mock New-CouchFirewallRule { }
    }
    It 'dry run changes nothing' {
        $out = & (Join-Path $Root 'Install-LinkProbe.ps1') 6>&1 | Out-String
        $out | Should -Match 'PLAN    add firewall rule "couchbox iperf3" TCP 5201 Private/LocalSubnet'
        Should -Invoke Register-CouchProbeTask -Times 0
        Should -Invoke New-CouchFirewallRule -Times 0
        Should -Invoke Invoke-CouchWinget -Times 0
    }
    It '-Apply adds exactly one firewall rule (TCP 5201) and the task' {
        & (Join-Path $Root 'Install-LinkProbe.ps1') -Apply 6>&1 | Out-Null
        Should -Invoke New-CouchFirewallRule -Times 1 -ParameterFilter { $DisplayName -eq 'couchbox iperf3' -and $Protocol -eq 'TCP' -and $Ports[0] -eq 5201 -and $Ports.Count -eq 1 }
        Should -Invoke Register-CouchProbeTask -Times 1 -ParameterFilter { $Port -eq 5201 }
    }
}

Describe 'Check-Sunshine.ps1' {
    BeforeEach {
        Mock Get-CouchService { [pscustomobject]@{ Status = 'Running' } }
        Mock Get-CouchListeningPort { @(47984, 47989, 47990, 48010) }
        Mock Get-CouchFirewallRule { [pscustomobject]@{ DisplayName = $DisplayName } }
        Mock Get-CouchWolAdapter { @([pscustomobject]@{ Name = 'Ethernet'; Mac = 'AA-BB-CC-DD-EE-FF'; Magic = '1'; PowerMgmt = 'Enabled'; Media = '802.3' }) }
        Mock Get-CouchFastStartup { 0 }
        Mock Get-CouchGpuName { @('NVIDIA GeForce RTX 4070') }
        Mock Get-CouchProbeTask { [pscustomobject]@{ TaskName = 'couchbox iperf3' } }
    }
    It 'all green -> exit 0' {
        $d = New-SunshineDir
        $doc = [System.IO.File]::ReadAllText((Join-Path $d 'apps.json'))
        $m = Merge-SunshineApps -ExistingJson $doc -NewApps @(New-SunshineDefaultApps)
        Write-Utf8NoBom -Path (Join-Path $d 'apps.json') -Text $m.Json
        $out = & (Join-Path $Root 'Check-Sunshine.ps1') -SunshineConfigDir $d 6>&1 | Out-String
        $out | Should -Match 'service\s+OK'
        $LASTEXITCODE | Should -Be 0
    }
    It 'BOM in apps.json and Fast Startup on -> FAIL, exit 2' {
        Mock Get-CouchFastStartup { 1 }
        $d = New-SunshineDir
        $bytes = [byte[]](0xEF, 0xBB, 0xBF) + [System.IO.File]::ReadAllBytes((Join-Path $d 'apps.json'))
        [System.IO.File]::WriteAllBytes((Join-Path $d 'apps.json'), $bytes)
        $out = & (Join-Path $Root 'Check-Sunshine.ps1') -SunshineConfigDir $d 6>&1 | Out-String
        $out | Should -Match 'BOM'
        $out | Should -Match 'fast-startup\s+FAIL'
        $LASTEXITCODE | Should -Be 2
    }
}
