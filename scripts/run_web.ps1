# Build the frontend (if needed) and start the local web app.
#
#   powershell -ExecutionPolicy Bypass -File scripts\run_web.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\run_web.ps1 -Port 8899
#
# Prefer the Python launcher (no execution policy, no encoding quirks):
#   uv run python scripts\run_web.py
param(
    [int]$Port = 8000,
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$args = @('run', '--frozen', 'python', 'scripts\run_web.py', '--port', "$Port")
if ($SkipFrontendBuild) { $args += '--skip-build' }

Write-Host "starting Pocker Agent web app (build + serve)..." -ForegroundColor Cyan
uv @args
