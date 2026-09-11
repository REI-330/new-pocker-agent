# Build the frontend (if needed) and start the local web app, then print the URL.
#
#   powershell -ExecutionPolicy Bypass -File scripts\run_web.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\run_web.ps1 -Port 8899
param(
    [int]$Port = 8000,
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $SkipFrontendBuild) {
    if (-not (Test-Path "frontend\node_modules")) {
        Write-Host "installing frontend dependencies (first run)..." -ForegroundColor Cyan
        npm ci --prefix frontend
    }
    Write-Host "building frontend..." -ForegroundColor Cyan
    npm run build --prefix frontend
}

$page = Join-Path $root "frontend\dist\index.html"
if (-not (Test-Path $page)) {
    Write-Warning "frontend/dist/index.html is missing; the API will run but the page will 404."
}

Write-Host ""
Write-Host "Pocker Agent web app:  http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "  library          -> pick a game, press start"
Write-Host "  new game         -> needs a model saved in Model Settings"
Write-Host "  Ctrl+C to stop"
Write-Host ""

uv run --frozen python -m uvicorn pocker_agent.app:app --host 127.0.0.1 --port $Port
