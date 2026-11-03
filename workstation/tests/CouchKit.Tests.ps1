# Pester 5 tests for CouchKit.psm1 (run under pwsh in CI; the code itself targets Windows PowerShell 5.1).
BeforeAll {
    $script:Root = Split-Path -Parent $PSScriptRoot
    Import-Module (Join-Path $Root 'CouchKit.psm1') -Force
    $script:Fix = Join-Path $Root 'fixtures'
    function New-SteamFixture {
        $id = [guid]::NewGuid().ToString('N')
        $steam = Join-Path $TestDrive ('Steam' + $id)
        $lib2 = Join-Path $TestDrive ('Lib2' + $id)
        Copy-Item -Recurse (Join-Path $Fix 'steam') $steam
        Copy-Item -Recurse (Join-Path $Fix 'lib2') $lib2
        $vdf = Join-Path (Join-Path $steam 'steamapps') 'libraryfolders.vdf'
        $t = [System.IO.File]::ReadAllText($vdf)
        $t = $t.Replace('@STEAM@', $steam.Replace('\', '\\')).Replace('@LIB2@', $lib2.Replace('\', '\\'))
        [System.IO.File]::WriteAllText($vdf, $t)
        $script:LastLib2 = $lib2
        return $steam
    }
}

Describe 'ConvertFrom-Vdf' {
    It 'parses nested blocks, comments and escaped quotes' {
        $d = ConvertFrom-Vdf -Text ('"A" { "k" "v" // c' + "`n" + ' "n" { "x" "1" } "q" "say \"hi\"" }')
        $d['A']['k'] | Should -Be 'v'
        $d['A']['n']['x'] | Should -Be '1'
        $d['A']['q'] | Should -Be 'say "hi"'
    }
    It 'rejects unbalanced input' {
        { ConvertFrom-Vdf -Text '"A" { "k" "v"' } | Should -Throw
        { ConvertFrom-Vdf -Text '"A" "v" }' } | Should -Throw
    }
}

Describe 'Steam library' {
    It 'finds every library folder from libraryfolders.vdf' {
        $steam = New-SteamFixture
        $libs = Get-SteamLibraryFolder -SteamPath $steam
        $libs.Count | Should -Be 2
        $libs[1] | Should -Be $script:LastLib2
    }
    It 'lists games across libraries, skips redistributables, flags installed by StateFlags bit 4' {
        $steam = New-SteamFixture
        $games = @(Get-SteamGame -SteamPath $steam)
        ($games | ForEach-Object { $_.name }) | Should -Be @('Celeste', 'Hades "Deluxe"', 'Hollow Knight')
        ($games | Where-Object { $_.appid -eq 1145360 }).installed | Should -BeFalse
        ($games | Where-Object { $_.appid -eq 367520 }).installed | Should -BeTrue
    }
    It 'never writes into the Steam folder' {
        $steam = New-SteamFixture
        $before = Get-ChildItem -Recurse $steam | ForEach-Object { $_.FullName + $_.LastWriteTimeUtc.Ticks }
        [void](Get-SteamGame -SteamPath $steam)
        (Get-ChildItem -Recurse $steam | ForEach-Object { $_.FullName + $_.LastWriteTimeUtc.Ticks }) | Should -Be $before
    }
}

Describe 'Merge-SunshineApps' {
    BeforeAll { $script:Existing = [System.IO.File]::ReadAllText((Join-Path $Fix 'apps.json')) }
    It 'keeps every existing entry, unknown keys and top-level keys; adds only missing names' {
        $new = @(New-SunshineDefaultApps) + @(New-SunshineGameApp -AppId 504230 -Name 'Celeste')
        $r = Merge-SunshineApps -ExistingJson $Existing -NewApps $new
        $r.Added | Should -Be @('Steam Big Picture', 'Celeste')
        $doc = $r.Json | ConvertFrom-Json
        @($doc.apps).Count | Should -Be 4
        $doc.'custom-top-level'.keep | Should -Be 'me'
        $doc.env.PATH | Should -Be '$(PATH);$(ProgramFiles(x86))\Steam'
        $desk = $doc.apps | Where-Object { $_.name -eq 'Desktop' }
        $desk.'my-own-key' | Should -Be 42
        $emu = $doc.apps | Where-Object { $_.name -eq "Michael's Emulator" }
        $emu.'prep-cmd'[0].elevated | Should -BeTrue
        $emu.'exclude-global-prep-cmd' | Should -Be 'false'
    }
    It 'writes the documented Sunshine forms' {
        $r = Merge-SunshineApps -ExistingJson '' -NewApps (@(New-SunshineDefaultApps) + @(New-SunshineGameApp -AppId 504230 -Name 'Celeste' -ImagePath 'C:\x\504230.png'))
        $doc = $r.Json | ConvertFrom-Json
        $bp = $doc.apps | Where-Object { $_.name -eq 'Steam Big Picture' }
        $bp.cmd | Should -Be 'steam://open/bigpicture'
        $bp.'prep-cmd'[0].undo | Should -Be 'steam://close/bigpicture'
        $bp.'auto-detach' | Should -BeTrue
        $g = $doc.apps | Where-Object { $_.name -eq 'Celeste' }
        @($g.detached) | Should -Be @('steam://rungameid/504230')
        $g.'image-path' | Should -Be 'C:\x\504230.png'
    }
    It 'is idempotent and case-insensitive on names' {
        $r1 = Merge-SunshineApps -ExistingJson $Existing -NewApps @(New-SunshineDefaultApps)
        $r2 = Merge-SunshineApps -ExistingJson $r1.Json -NewApps @(New-SunshineDefaultApps)
        $r2.Added.Count | Should -Be 0
        $r3 = Merge-SunshineApps -ExistingJson $r1.Json -NewApps @(New-SunshineGameApp -AppId 1 -Name 'DESKTOP')
        $r3.Added.Count | Should -Be 0
    }
    It 'refuses a non-object document' {
        { Merge-SunshineApps -ExistingJson '[1,2]' -NewApps @() } | Should -Throw
    }
}

Describe 'files' {
    It 'writes UTF-8 without a BOM' {
        $p = Join-Path $TestDrive 'x.json'
        Write-Utf8NoBom -Path $p -Text '{"a":1}'
        $b = [System.IO.File]::ReadAllBytes($p)
        $b[0] | Should -Be ([byte][char]'{')
    }
    It 'backs up beside the file with a timestamp' {
        $p = Join-Path $TestDrive 'apps.json'
        Set-Content -LiteralPath $p -Value 'x'
        $bak = Backup-CouchFile -Path $p -Now ([datetime]'2026-11-03T10:20:30')
        $bak | Should -Be ($p + '.couchbox-20261103-102030.bak')
        Test-Path $bak | Should -BeTrue
        Backup-CouchFile -Path (Join-Path $TestDrive 'missing.json') | Should -BeNullOrEmpty
    }
}
