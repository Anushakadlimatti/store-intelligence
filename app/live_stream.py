from __future__ import annotations

import asyncio
import json

from starlette.concurrency import run_in_threadpool

from app.funnel import get_store_funnel
from app.metrics import get_zone_entry_exit_counts
from app.zone_areas import rollup_zone_areas
from app.recent_events import get_recent_events


def build_live_payload(store_id: str) -> dict:
    """Sync snapshot for polling / SSE (run via thread pool from async routes)."""
    funnel = get_store_funnel(store_id)
    from app.db import get_session
    from app.occupancy import get_area_presence

    zones = get_zone_entry_exit_counts(store_id)
    with get_session() as session:
        area_presence = get_area_presence(session, store_id)
    return {
        "store_id": store_id,
        "zones": zones,
        "areas": rollup_zone_areas(store_id, zones, area_presence),
        "funnel": {
            "stages": [
                {
                    "stage": s.stage,
                    "count": s.count,
                    "drop_off_pct": s.drop_off_pct,
                }
                for s in funnel.stages
            ]
        },
        "recent_events": get_recent_events(store_id, 15),
    }


async def zone_counts_sse(store_id: str, interval_sec: float = 0.4):
    """SSE: zones only (legacy)."""
    while True:
        zones = await run_in_threadpool(get_zone_entry_exit_counts, store_id)
        payload = {"store_id": store_id, "zones": zones}
        yield f"data: {json.dumps(payload)}\n\n"
        await asyncio.sleep(interval_sec)


async def live_dashboard_sse(store_id: str, interval_sec: float = 0.4):
    """SSE: zones + funnel + recent events for full live UI."""
    while True:
        payload = await run_in_threadpool(build_live_payload, store_id)
        yield f"data: {json.dumps(payload)}\n\n"
        await asyncio.sleep(interval_sec)
