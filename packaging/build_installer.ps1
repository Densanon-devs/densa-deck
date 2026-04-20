# Build the Windows installer end-to-end.
#
# Prereqs (one-time):
#   1. `pip install -e '.[desktop]'` to get pyinstaller + pywebview
#   2. Install Inno Setup from https://jrsoftware.org/isinfo.php
#      (ISCC.exe ends up at "C:\Program Files (x86)\Inno Setup 6\ISCC.exe")
#
# Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
#
# Outputs:
#   dist\mtg-engine\              -> PyInstaller bundle (folder mode)
#   dist\MTG-Deck-Engine-Setup-<ver>.exe -> Inno Setup installer
#
# Code signing: add `& signtool sign ...` calls after the build steps if a
# cert is available. Unsigned installers still work — users see a Smart
# Screen warning they can click through.

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot

try {
    # Step 1: PyInstaller — folder mode (faster startup than single-file)
    Write-Host "[1/2] Running PyInstaller..." -ForegroundColor Cyan
    pyinstaller mtg-engine.spec --clean --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }

    # Step 2: Inno Setup
    $ISCC = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    if (-not (Test-Path $ISCC)) {
        # Try the default on 64-bit-native systems
        $ISCC = "C:\Program Files\Inno Setup 6\ISCC.exe"
    }
    if (-not (Test-Path $ISCC)) {
        throw "ISCC.exe not found. Install Inno Setup 6 from https://jrsoftware.org/isinfo.php"
    }
    Write-Host "[2/2] Running Inno Setup..." -ForegroundColor Cyan
    & $ISCC "packaging\installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit $LASTEXITCODE)" }

    Write-Host "`nBuild complete. Installer in dist\." -ForegroundColor Green
    Get-ChildItem dist\*.exe | ForEach-Object { Write-Host "  $($_.FullName)" }
}
finally {
    Pop-Location
}
