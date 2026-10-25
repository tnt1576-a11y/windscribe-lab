param([switch]$Demo)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonPath = (Get-Command pythonw.exe -ErrorAction Stop).Source
}
$arguments = @('"' + (Join-Path $PSScriptRoot 'main.py') + '"')
if ($Demo) { $arguments += '--demo' }
Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $PSScriptRoot -WindowStyle Normal
