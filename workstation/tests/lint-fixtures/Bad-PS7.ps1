$a = $b ?? 1
$c = $x ? 1 : 2
$d = $e?.Name
Get-Item x && Get-Item y
Join-Path 'a' 'b' 'c'
$j = '{}' | ConvertFrom-Json -AsHashtable
$k = @{a = @{b = @{c = 1}}} | ConvertTo-Json
Set-Content -Path x -Value y -Encoding utf8NoBOM
if (@('a', 'b') -like 'a') { 'x' }
