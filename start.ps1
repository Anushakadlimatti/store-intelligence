# Start API on configurable port (default 8080 — avoids conflict with other apps on 8000)
param(
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($Port -gt 0) {
    $env:PORT = "$Port"
} elseif (-not $env:PORT) {
    $env:PORT = "8080"
}

$env:PYTHONPATH = "."
Write-Host "Starting Store Intelligence API on http://127.0.0.1:$($env:PORT)"
Write-Host "Dashboard: http://127.0.0.1:$($env:PORT)/dashboard?store=STORE_2"
uvicorn app.main:app --reload --host 127.0.0.1 --port $env:PORT
