#!/usr/bin/env bash
# Start API (Git Bash / Linux / macOS) — same as start.ps1
set -e
cd "$(dirname "$0")"
export PYTHONPATH=.
export PORT="${PORT:-8080}"
echo "Starting Store Intelligence API on http://127.0.0.1:${PORT}"
echo "Dashboard: http://127.0.0.1:${PORT}/dashboard?store=STORE_2"
exec python -m uvicorn app.main:app --reload --host 127.0.0.1 --port "$PORT"
