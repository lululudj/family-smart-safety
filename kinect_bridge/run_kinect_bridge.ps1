$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root "tower_ui\kinect_bridge.exe"

if (-not (Test-Path -LiteralPath $exe)) {
    & (Join-Path $PSScriptRoot "build_kinect_bridge.ps1")
}

Write-Host "Starting Kinect v2 bridge..."
& $exe
