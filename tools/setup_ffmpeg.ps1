# Download ffmpeg + ffprobe (Windows build) into .\bin
# Usage:  powershell -ExecutionPolicy Bypass -File tools\setup_ffmpeg.ps1
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$binDir = Join-Path $root 'bin'
New-Item -ItemType Directory -Force -Path $binDir | Out-Null

# 两个源：gyan.dev 官方构建（体积小），失败或过慢时退到 BtbN 的 GitHub 构建
$sources = @(
    'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip',
    'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
)
$zip = Join-Path $env:TEMP 'subremover-ffmpeg.zip'
$extract = Join-Path $env:TEMP ('subremover-ffmpeg-' + [guid]::NewGuid().ToString('N'))

$downloaded = $false
foreach ($url in $sources) {
    Write-Host "Downloading ffmpeg from $url ..."
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing -TimeoutSec 600
        $downloaded = $true
        break
    } catch {
        Write-Host "Failed: $($_.Exception.Message)"
        if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    }
}
if (-not $downloaded) { throw "All ffmpeg download sources failed. Install ffmpeg manually and set SUBTITLE_REMOVER_FFMPEG_DIR." }

Write-Host "Extracting ..."
Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force

foreach ($name in 'ffmpeg.exe', 'ffprobe.exe') {
    $found = Get-ChildItem -LiteralPath $extract -Recurse -Filter $name -File | Select-Object -First 1
    if (-not $found) { throw "$name not found in the downloaded archive." }
    Copy-Item -LiteralPath $found.FullName -Destination (Join-Path $binDir $name) -Force
    Write-Host "Installed $name -> $binDir"
}

Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue

& (Join-Path $binDir 'ffmpeg.exe') -version | Select-Object -First 1
Write-Host "Done. ffmpeg is ready in .\bin"
