$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
New-Item -ItemType Directory -Path '.qa' -Force | Out-Null
$testDirectory = Join-Path '.qa' ('build-tests-' + [guid]::NewGuid().ToString('N'))
& $pythonPath -m pytest -q --basetemp=$testDirectory
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
$previousBuildPath = $env:PATH
try {
    # Foreign tools on PATH can supply an incompatible ICU DLL. Qt uses Windows' ICU.
    $env:PATH = (Split-Path -Parent $pythonPath) + ';' + $env:SystemRoot + '\System32;' + $env:SystemRoot
    & $pythonPath -m PyInstaller --noconfirm --clean --windowed --onedir --name WindscribeLab --icon assets/app.ico --version-file assets/version_info.txt --add-data 'assets;assets' --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick main.py
    if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
}
finally { $env:PATH = $previousBuildPath }
Copy-Item -LiteralPath README.md,LICENSE,THIRD_PARTY.md -Destination dist/WindscribeLab
Copy-Item -LiteralPath licenses -Destination dist/WindscribeLab -Recurse -Force
New-Item -ItemType Directory -Path dist/WindscribeLab/docs -Force | Out-Null
Copy-Item -LiteralPath docs/EXPORTS.md,docs/preview-simulation.png,docs/preview-anti-censorship.png -Destination dist/WindscribeLab/docs
& $pythonPath build_release.py
if ($LASTEXITCODE -ne 0) { throw 'Release packaging failed.' }
Write-Output (Join-Path $PSScriptRoot 'dist\WindscribeLab\WindscribeLab.exe')
