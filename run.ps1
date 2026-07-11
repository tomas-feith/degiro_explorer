<#
.SYNOPSIS
    Activate the venv, sync with DEGIRO, then launch the dashboard.

.EXAMPLE
    .\run.ps1              # full sync (logs in to DEGIRO - needs phone approval), then dashboard
    .\run.ps1 --offline    # re-run derivations from stored data (no login), then dashboard
    .\run.ps1 --no-sync    # skip the sync entirely, just open the dashboard
#>
$ErrorActionPreference = "Stop"

# Always run from the project root (the dir this script lives in).
Set-Location -LiteralPath $PSScriptRoot

$python = ".\.venv\Scripts\python.exe"
$streamlit = ".\.venv\Scripts\streamlit.exe"
if (-not (Test-Path $python)) {
    Write-Error "virtualenv not found at $python`ncreate it first, e.g.:  python -m venv .venv ; .\.venv\Scripts\pip install -r requirements.txt"
    exit 1
}

# Pass through any sync flags (e.g. --offline). --no-sync skips syncing.
if ($args.Count -ge 1 -and $args[0] -eq "--no-sync") {
    Write-Host ">> skipping sync (--no-sync)"
} else {
    Write-Host ">> syncing with DEGIRO..."
    & $python scripts\sync.py @args
    if ($LASTEXITCODE -ne 0) {
        Write-Error "sync failed (exit $LASTEXITCODE) - not launching the dashboard"
        exit $LASTEXITCODE
    }
}

Write-Host ">> launching dashboard..."
& $streamlit run dashboard\app.py
