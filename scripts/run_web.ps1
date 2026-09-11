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

$dist = Join-Path $root "frontend\dist\index.html"
if (-not (Test-Path $dist)) {
    Write-Warning "frontend/dist/index.html is missing; the API will still run but the page will 404."
}

Write-Host ""
Write-Host "Pocker Agent web app:  http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "  - 玩法库: pick a game and press 开始试玩"
Write-Host "  - 新建玩法: needs a model saved in 模型设置 (otherwise it says so)"
Write-Host "  - Ctrl+C to stop"
Write-Host ""

uv run --frozen python -m uvicorn pocker_agent.app:app --host 127.0.0.1 --port $Port
