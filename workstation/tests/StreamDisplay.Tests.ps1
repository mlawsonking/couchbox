# Pester: stream-display mode selection, do/undo/restore state handling, sunshine.conf merge. The
# ChangeDisplaySettingsEx call itself is hardware-only and mocked here.
BeforeAll {
    $script:Root = Split-Path -Parent $PSScriptRoot
    Import-Module (Join-Path $Root 'CouchKit.psm1') -Force
    function M([int]$w, [int]$h, [int]$r) { return [pscustomobject]@{ Width = $w; Height = $h; Refresh = $r } }
    function F($m) { if ($null -eq $m) { return '' }; return ('{0}x{1}@{2}' -f $m.Width, $m.Height, $m.Refresh) }
    # A typical 3440x1440 ultrawide's mode list (no 4K, no 16:9 above 2560x1440).
    $script:Ultrawide = @((M 3440 1440 144), (M 3440 1440 100), (M 3440 1440 60), (M 2560 1440 144), (M 2560 1440 60),
        (M 2560 1080 60), (M 1920 1080 144), (M 1920 1080 120), (M 1920 1080 60), (M 1280 720 60), (M 1024 768 60))
}

Describe 'Select-StreamMode' {
    It 'exact match' { F (Select-StreamMode -Modes $Ultrawide -Width 1920 -Height 1080 -Fps 60) | Should -Be '1920x1080@60' }
    It 'same size, highest refresh not above the request' {
        $r = Select-StreamMode -Modes $Ultrawide -Width 1920 -Height 1080 -Fps 90
        $r.Refresh | Should -Be 60
        (Select-StreamMode -Modes $Ultrawide -Width 1920 -Height 1080 -Fps 120).Refresh | Should -Be 120
    }
    It 'same size, only higher refreshes -> the lowest of them' {
        (Select-StreamMode -Modes @((M 1920 1080 144)) -Width 1920 -Height 1080 -Fps 60).Refresh | Should -Be 144
    }
    It '4K request on an ultrawide -> largest 16:9 mode that fits (2560x1440)' {
        $r = Select-StreamMode -Modes $Ultrawide -Width 3840 -Height 2160 -Fps 60
        "$($r.Width)x$($r.Height)@$($r.Refresh)" | Should -Be '2560x1440@60'
    }
    It 'no matching aspect or bad input -> leave the display alone' {
        Select-StreamMode -Modes @((M 3440 1440 60)) -Width 1920 -Height 1080 -Fps 60 | Should -BeNullOrEmpty
        Select-StreamMode -Modes $Ultrawide -Width 0 -Height 0 -Fps 60 | Should -BeNullOrEmpty
    }
}

Describe 'Get-StreamDisplayPlan' {
    BeforeAll { $script:Orig = M 3440 1440 144; $script:Tv = M 1920 1080 60 }
    It 'do: saves the original once, switches' {
        $p = Get-StreamDisplayPlan -Action do -State $null -Current $Orig -Selected $Tv
        F $p.SaveState | Should -Be (F $Orig)
        F $p.SetMode | Should -Be (F $Tv)
    }
    It 'do twice (or after a crash left the state): never overwrites the saved original' {
        $p = Get-StreamDisplayPlan -Action do -State $Orig -Current $Tv -Selected $Tv
        $p.SaveState | Should -BeNullOrEmpty
        $p.SetMode | Should -BeNullOrEmpty
    }
    It 'do when already in the stream mode with no state: nothing to save or set' {
        $p = Get-StreamDisplayPlan -Action do -State $null -Current $Tv -Selected $Tv
        $p.SaveState | Should -BeNullOrEmpty
        $p.SetMode | Should -BeNullOrEmpty
    }
    It 'do with no usable mode: nothing' {
        $p = Get-StreamDisplayPlan -Action do -State $null -Current $Orig -Selected $null
        $p.SetMode | Should -BeNullOrEmpty
        $p.Message | Should -Match 'no suitable mode'
    }
    It 'undo / restore: back to the saved mode and clear the state; no state = no-op' {
        foreach ($a in 'undo', 'restore') {
            $p = Get-StreamDisplayPlan -Action $a -State $Orig -Current $Tv -Selected $null
            F $p.SetMode | Should -Be (F $Orig)
            $p.ClearState | Should -BeTrue
            $n = Get-StreamDisplayPlan -Action $a -State $null -Current $Orig -Selected $null
            $n.SetMode | Should -BeNullOrEmpty
            $n.ClearState | Should -BeFalse
        }
    }
}

Describe 'Set-StreamDisplay.ps1 (P/Invoke mocked)' {
    BeforeEach {
        $global:CouchTestDisplay = M 3440 1440 144
        Mock Get-CouchDisplayModes { $Ultrawide }
        Mock Get-CouchCurrentMode { $global:CouchTestDisplay }
        Mock Set-CouchDisplayMode { $global:CouchTestDisplay = $Mode; 0 }
        $script:State = Join-Path $TestDrive ('state-' + [guid]::NewGuid().ToString('N') + '.json')
    }
    It 'do -> undo round trip with Sunshine env vars, idempotent' {
        $env:SUNSHINE_CLIENT_WIDTH = '1920'; $env:SUNSHINE_CLIENT_HEIGHT = '1080'; $env:SUNSHINE_CLIENT_FPS = '60'
        try {
            & (Join-Path $Root 'Set-StreamDisplay.ps1') do -StatePath $State 6>&1 | Out-Null
            "$($global:CouchTestDisplay.Width)x$($global:CouchTestDisplay.Height)" | Should -Be '1920x1080'
            (Read-StreamDisplayState -Path $State).Width | Should -Be 3440
            & (Join-Path $Root 'Set-StreamDisplay.ps1') do -StatePath $State 6>&1 | Out-Null       # second do
            (Read-StreamDisplayState -Path $State).Width | Should -Be 3440                       # original kept
            & (Join-Path $Root 'Set-StreamDisplay.ps1') undo -StatePath $State 6>&1 | Out-Null
            "$($global:CouchTestDisplay.Width)x$($global:CouchTestDisplay.Height)@$($global:CouchTestDisplay.Refresh)" | Should -Be '3440x1440@144'
            Test-Path $State | Should -BeFalse
            & (Join-Path $Root 'Set-StreamDisplay.ps1') restore -StatePath $State 6>&1 | Out-Null  # nothing saved
            Should -Invoke Set-CouchDisplayMode -Times 2
        } finally {
            Remove-Item Env:SUNSHINE_CLIENT_WIDTH, Env:SUNSHINE_CLIENT_HEIGHT, Env:SUNSHINE_CLIENT_FPS -ErrorAction SilentlyContinue
        }
    }
    It 'a failed mode change keeps the state and never fails the stream' {
        Mock Set-CouchDisplayMode { -2 }
        & (Join-Path $Root 'Set-StreamDisplay.ps1') do -StatePath $State -Width 1920 -Height 1080 -Fps 60 3>&1 6>&1 | Out-Null
        $LASTEXITCODE | Should -Be 0
        Test-Path $State | Should -BeTrue
    }
}

Describe 'Merge-SunshineConf' {
    It 'empty sunshine.conf (the host today) gets one global_prep_cmd line' {
        $m = Merge-SunshineConf -Text '' -Do 'X Set-StreamDisplay.ps1 do' -Undo 'X Set-StreamDisplay.ps1 undo'
        $m.Changed | Should -BeTrue
        $line = ($m.Text -split "`r?`n")[0]
        $line | Should -Match '^global_prep_cmd = \['
        $arr = @(($line -replace '^global_prep_cmd = ', '') | ConvertFrom-Json)
        $arr.Count | Should -Be 1
        $arr[0].undo | Should -Be 'X Set-StreamDisplay.ps1 undo'
        $arr[0].elevated | Should -BeFalse
    }
    It 'keeps other settings and existing prep commands; second merge is a no-op' {
        $conf = "sunshine_name = BOX`r`nglobal_prep_cmd = [{`"do`":`"nircmd a`",`"undo`":`"nircmd b`",`"elevated`":true}]`r`nport = 47989`r`n"
        $m = Merge-SunshineConf -Text $conf -Do 'p Set-StreamDisplay.ps1 do' -Undo 'p Set-StreamDisplay.ps1 undo'
        $m.Text | Should -Match 'sunshine_name = BOX'
        $m.Text | Should -Match 'port = 47989'
        $arr = @((($m.Text -split "`r?`n") | Where-Object { $_ -like 'global_prep_cmd*' }) -replace '^global_prep_cmd = ', '' | ConvertFrom-Json)
        $arr.Count | Should -Be 2
        $arr[0].do | Should -Be 'nircmd a'
        $arr[0].elevated | Should -BeTrue
        (Merge-SunshineConf -Text $m.Text -Do 'p Set-StreamDisplay.ps1 do' -Undo 'q').Changed | Should -BeFalse
    }
}
