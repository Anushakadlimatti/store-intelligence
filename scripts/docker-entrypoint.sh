#!/bin/sh
set -e
mkdir -p /app/data /app/output
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
