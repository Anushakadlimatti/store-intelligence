"""
Feed events one-by-one in timestamp order so zone occupancy updates live on the dashboard.

  python feed_live.py --input output/store2_events.jsonl --store STORE_2 --reset
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

from app.config import api_base_url


def main():
    parser = argparse.ArgumentParser(description="Live event feeder for dashboard")
    parser.add_argument("--input", default="output/store2_events.jsonl")
    parser.add_argument("--api-url", default=None, help="Default: http://127.0.0.1:$PORT (PORT=8080)")
    parser.add_argument("--store", default="STORE_2")
    parser.add_argument("--interval", type=float, default=0.8, help="Seconds between events")
    parser.add_argument("--reset", action="store_true", help="Reset store before feeding")
    parser.add_argument(
        "--entry-only",
        action="store_true",
        help="Only ENTRY/EXIT at ENTRANCE (skip floor/billing events)",
    )
    args = parser.parse_args()
    base = (args.api_url or api_base_url()).rstrip("/")

    path = Path(args.input)
    if not path.exists():
        print(f"Missing {path}. Run: python -m pipeline.detect --store {args.store} --output {path}")
        return

    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))

    if args.entry_only:
        events = [
            e
            for e in events
            if e.get("event_type") in ("ENTRY", "EXIT")
            and e.get("zone_id") == "ENTRANCE"
            and not e.get("is_staff")
        ]

    events.sort(key=lambda e: e.get("timestamp", ""))

    with httpx.Client(timeout=30.0) as client:
        if args.reset:
            r = client.post(f"{base}/admin/reset-live/{args.store}")
            print("Reset:", r.json())

        print(f"Feeding {len(events)} events every {args.interval}s -> {base}")
        print("Open dashboard:", f"{base}/dashboard?store={args.store}")
        for i, ev in enumerate(events, 1):
            r = client.post(f"{base}/events/ingest", json={"events": [ev]})
            body = r.json()
            live = client.get(f"{base}/stores/{args.store}/live").json()
            areas = {a["area_id"]: a["current"] for a in live.get("areas", [])}
            print(
                f"[{i}/{len(events)}] {ev.get('event_type')} "
                f"{ev.get('visitor_id')} @ {ev.get('zone_id')} | "
                f"entrance={areas.get('ENTRANCE', '?')} "
                f"floor={areas.get('MAIN_FLOOR', '?')} "
                f"billing={areas.get('BILLING', '?')} | {body}"
            )
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
