from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from app.db import EventRow, get_session
from app.filters import filter_customer_events, unique_customer_visitors
from app.models import HeatmapZone, StoreHeatmap


def _heatmap_window_start(session, store_id: str) -> datetime:
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    has_today = (
        session.query(EventRow)
        .filter(EventRow.store_id == store_id, EventRow.timestamp >= today_start)
        .first()
    )
    return today_start if has_today else datetime(1970, 1, 1)


def get_store_heatmap(store_id: str) -> StoreHeatmap:
    with get_session() as session:
        window_start = _heatmap_window_start(session, store_id)
        rows = (
            session.query(EventRow)
            .filter(
                EventRow.store_id == store_id,
                EventRow.timestamp >= window_start,
                EventRow.is_staff == False,  # noqa: E712
            )
            .all()
        )
        events = [
            {
                "visitor_id": e.visitor_id,
                "event_type": e.event_type,
                "zone_id": e.zone_id,
                "dwell_ms": e.dwell_ms,
                "is_staff": e.is_staff,
                "timestamp": e.timestamp,
            }
            for e in rows
        ]

    customer_events = filter_customer_events(events)
    session_count = len(unique_customer_visitors(customer_events))

    visits: dict[str, int] = defaultdict(int)
    dwells: dict[str, list[int]] = defaultdict(list)

    for ev in customer_events:
        if ev["zone_id"] and ev["event_type"] in ("ZONE_ENTER", "ZONE_DWELL"):
            visits[ev["zone_id"]] += 1
        if ev["event_type"] == "ZONE_DWELL" and ev["zone_id"]:
            dwells[ev["zone_id"]].append(ev["dwell_ms"])

    max_visits = max(visits.values()) if visits else 1
    max_dwell = max((sum(d) / len(d) for d in dwells.values() if d), default=1.0)

    layout = json.loads(
        (Path(__file__).resolve().parent.parent / "store_layout.json").read_text()
    )
    zone_ids = []
    for s in layout["stores"]:
        if s["store_id"] == store_id:
            zone_ids = [z["zone_id"] for z in s["zones"]]

    zones: list[HeatmapZone] = []
    for zone_id in zone_ids:
        v = visits.get(zone_id, 0)
        dlist = dwells.get(zone_id, [])
        avg_dwell = sum(dlist) / len(dlist) if dlist else 0.0
        zones.append(
            HeatmapZone(
                zone_id=zone_id,
                visit_frequency=round((v / max_visits) * 100, 2) if max_visits else 0.0,
                avg_dwell_normalized=round((avg_dwell / max_dwell) * 100, 2)
                if max_dwell
                else 0.0,
            )
        )

    confidence = "HIGH" if session_count >= 20 else "LOW"

    return StoreHeatmap(store_id=store_id, zones=zones, data_confidence=confidence)
