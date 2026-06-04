#!/usr/bin/env bash
# One entrypoint for the detection pipeline (run from repo root or pipeline/).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH=.

CMD="${1:-help}"
shift || true

case "$CMD" in
  detect)
    exec python -m pipeline.detect "$@"
    ;;
  live)
    exec python -m pipeline.live_detect "$@"
    ;;
  feed)
    exec python -m pipeline.feed_live "$@"
    ;;
  reload)
    exec python -m pipeline.reload_store2 "$@"
    ;;
  store2)
    python -m pipeline.detect --store STORE_2 --output output/store2_events.jsonl "$@"
    python -m pipeline.reload_store2
    ;;
  all)
    python -m pipeline.detect --output output/events.jsonl "$@"
    ;;
  help|*)
    echo "Usage: bash pipeline/run.sh <command> [args...]"
    echo "  detect   Process CCTV clips -> JSONL (python -m pipeline.detect)"
    echo "  live     Live detect + API ingest (python -m pipeline.live_detect)"
    echo "  feed     Replay JSONL to API (python -m pipeline.feed_live)"
    echo "  reload   Load store2_events.jsonl into DB"
    echo "  store2   detect STORE_2 + reload DB"
    echo "  all      detect all stores -> output/events.jsonl"
    exit 0
    ;;
esac
