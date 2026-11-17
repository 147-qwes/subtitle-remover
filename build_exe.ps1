# Build a standalone Windows exe with PyInstaller.
# Usage:  pip install pyinstaller;  powershell -ExecutionPolicy Bypass -File build_exe.ps1
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
Set-Location -LiteralPath $root

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "python not found in PATH" }

python -m PyInstaller --version > $null 2>&1
if ($LASTEXITCODE -ne 0) { throw "PyInstaller is not installed. Run: pip install pyinstaller" }

python -m PyInstaller --noconfirm --clean --windowed --onefile --name SubtitleRemover main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

$distDir = Join-Path $root 'dist'
$binDir = Join-Path $root 'bin'
if (Test-Path -LiteralPath $binDir) {
    $targetBin = Join-Path $distDir 'bin'
    New-Item -ItemType Directory -Force -Path $targetBin | Out-Null
    Get-ChildItem -LiteralPath $binDir -File | Copy-Item -Destination $targetBin -Force
    Write-Host "Copied ffmpeg to dist\bin"
} else {
    Write-Host "WARNING: .\bin not found. Run tools\setup_ffmpeg.ps1 first, or ship ffmpeg separately."
}

Write-Host ""
Write-Host "Build finished:"
Get-ChildItem -LiteralPath $distDir | Select-Object Name, Length
