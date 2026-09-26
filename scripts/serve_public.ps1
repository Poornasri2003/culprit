# scripts/serve_public.ps1
#
# One command to (a) start Compass's Streamlit UI on localhost:8501 and
# (b) expose it to the internet through Cloudflare's Quick Tunnel, printing
# a public https:// URL you can paste into your lablab submission.
#
# Requirements:
#   * `bob` CLI on PATH (Bob Shell — authenticates from BOB_API_KEY in .env)
#   * `cloudflared` on PATH — install once:
#        winget install --id Cloudflare.cloudflared
#     (or download from https://github.com/cloudflare/cloudflared/releases)
#   * .env filled in with BOB_API_KEY.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\serve_public.ps1
#
# When you're done, close this window (or Ctrl+C). Both processes stop cleanly.

$ErrorActionPreference = "Stop"

$repoRoot   = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "No virtualenv at $venvPython. Create it first:" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv"
    Write-Host "  .\.venv\Scripts\Activate.ps1"
    Write-Host "  pip install -e .[ui]"
    exit 1
}

if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "cloudflared not on PATH." -ForegroundColor Yellow
    Write-Host "Install with:  winget install --id Cloudflare.cloudflared"
    Write-Host "Or download:   https://github.com/cloudflare/cloudflared/releases"
    exit 1
}

Write-Host "Starting Streamlit on http://localhost:8501 ..." -ForegroundColor Cyan
$streamlit = Start-Process -FilePath $venvPython `
    -ArgumentList "-m","streamlit","run","streamlit_app.py",
                  "--server.port","8501","--server.headless","true",
                  "--browser.gatherUsageStats","false" `
    -WorkingDirectory $repoRoot -PassThru -WindowStyle Hidden

Start-Sleep -Seconds 3
Write-Host "Opening Cloudflare Quick Tunnel ..." -ForegroundColor Cyan
Write-Host "Watch below for the trycloudflare.com URL — that is your submission link.`n"

try {
    # cloudflared prints the assigned URL to stderr; --loglevel info keeps it visible.
    cloudflared tunnel --url http://localhost:8501 --loglevel info
}
finally {
    if ($streamlit -and -not $streamlit.HasExited) {
        Write-Host "`nStopping Streamlit ..." -ForegroundColor Yellow
        Stop-Process -Id $streamlit.Id -Force -ErrorAction SilentlyContinue
    }
}
