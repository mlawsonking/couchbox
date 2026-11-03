# Lints the workstation kit for Windows PowerShell 5.1 compatibility (runs under pwsh in CI).
# Fails on: non-ASCII bytes, a BOM, parse errors, and PowerShell 6/7-only syntax or parameters.
param([string]$Path = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$problems = New-Object System.Collections.Generic.List[string]
$files = @(Get-ChildItem -LiteralPath $Path -File | Where-Object { $_.Extension -in '.ps1', '.psm1', '.psd1' })
$testDir = Join-Path $Path 'tests'
if (Test-Path -LiteralPath $testDir) { $files += @(Get-ChildItem -LiteralPath $testDir -File -Filter '*.ps1') }
$bannedParams = @('AsHashtable', 'AdditionalChildPath', 'Parallel', 'SkipCertificateCheck', 'NoProxy', 'AsByteStream')
$bannedValues = @('utf8NoBOM', 'utf8BOM')
$bannedVars = @('PSStyle', 'IsWindows', 'IsLinux', 'IsMacOS', 'IsCoreCLR')
foreach ($f in $files) {
    $bytes = [System.IO.File]::ReadAllBytes($f.FullName)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) { $problems.Add("$($f.Name): has a BOM") }
    for ($i = 0; $i -lt $bytes.Length; $i++) {
        if ($bytes[$i] -gt 127) { $problems.Add(("{0}: non-ASCII byte 0x{1:X2} at offset {2}" -f $f.Name, $bytes[$i], $i)); break }
    }
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$tokens, [ref]$errors)
    foreach ($e in $errors) { $problems.Add("$($f.Name):$($e.Extent.StartLineNumber): parse error: $($e.Message)") }
    $isTest = $f.DirectoryName -eq $testDir
    foreach ($t in $tokens) {
        $k = [string]$t.Kind
        if ($k -in 'QuestionQuestion', 'QuestionQuestionEquals', 'QuestionDot', 'QuestionLBracket', 'AndAnd', 'OrOr') {
            $problems.Add("$($f.Name):$($t.Extent.StartLineNumber): PS7-only operator '$($t.Text)'")
        }
    }
    foreach ($n in $ast.FindAll({ $true }, $true)) {
        $line = $n.Extent.StartLineNumber
        $type = $n.GetType().Name
        if ($type -in 'TernaryExpressionAst', 'PipelineChainAst') { $problems.Add("$($f.Name):${line}: PS7-only $type") }
        if ($isTest) { continue }
        if ($type -eq 'CommandParameterAst' -and $n.ParameterName -in $bannedParams) { $problems.Add("$($f.Name):${line}: -$($n.ParameterName) does not exist in 5.1") }
        if ($type -eq 'StringConstantExpressionAst' -and $n.Value -in $bannedValues) { $problems.Add("$($f.Name):${line}: '$($n.Value)' encoding does not exist in 5.1") }
        if ($type -eq 'VariableExpressionAst' -and $n.VariablePath.UserPath -in $bannedVars) { $problems.Add("$($f.Name):${line}: variable $($n.VariablePath.UserPath) does not exist in 5.1") }
        if ($type -eq 'BinaryExpressionAst' -and ([string]$n.Operator) -in 'Ilike', 'Inotlike', 'Clike', 'Cnotlike', 'Imatch', 'Inotmatch') {
            if ($n.Left.GetType().Name -in 'ArrayExpressionAst', 'ArrayLiteralAst') { $problems.Add("$($f.Name):${line}: -like/-match on an array filters instead of testing") }
        }
        if ($type -eq 'CommandAst') {
            $cmd = $n.GetCommandName()
            if ($cmd -eq 'Join-Path') {
                $positional = @($n.CommandElements | Select-Object -Skip 1 | Where-Object { $_.GetType().Name -ne 'CommandParameterAst' })
                if ($positional.Count -gt 2) { $problems.Add("$($f.Name):${line}: Join-Path with more than 2 paths is PS6+") }
            }
            if ($cmd -eq 'ConvertFrom-Json' -or $cmd -eq 'ConvertTo-Json') {
                $hasDepth = @($n.CommandElements | Where-Object { $_.GetType().Name -eq 'CommandParameterAst' -and $_.ParameterName -eq 'Depth' }).Count -gt 0
                if ($cmd -eq 'ConvertFrom-Json' -and $hasDepth) { $problems.Add("$($f.Name):${line}: ConvertFrom-Json -Depth is PS6+") }
                if ($cmd -eq 'ConvertTo-Json' -and -not $hasDepth) { $problems.Add("$($f.Name):${line}: ConvertTo-Json without -Depth truncates at 2 levels in 5.1") }
            }
        }
    }
}
if ($problems.Count -gt 0) {
    foreach ($p in $problems) { Write-Host "PS51-LINT: $p" }
    exit 1
}
Write-Host "PS 5.1 lint: clean ($($files.Count) files)"
exit 0
