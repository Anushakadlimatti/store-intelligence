from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from app.db import EventRow, get_session, metadata_from_json
from app.occupancy import get_area_presence, get_zone_occupancy
from app.filters import (
    filter_customer_events,
    unique_customer_visitors,
    zone_entry_exit_from_events,
)
from app.ingestion import correlate_conversions
from app.models import StoreMetrics, ZoneMetric
from app.zone_areas import rollup_zone_areas

_LAYOUT_PATH = Path(__file__).resolve().parent.parent / "store_layout.json"


def _load_zone_names() -> dict[str, dict[str, str]]:
    data = json.loads(_LAYOUT_PATH.read_text(encoding="utf-8"))
    result: dict[str, dict[str, str]] = {}
    for store in data["stores"]:
        result[store["store_id"]] = {
            z["zone_id"]: z["zone_name"] for z in store["zones"]
        }
    return result


def _metrics_window_start(session, store_id: str) -> datetime:
    """Use today; if clips have no events today, include all ingested events (demo CCTV)."""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    has_today = (
        session.query(EventRow)
        .filter(EventRow.store_id == store_id, EventRow.timestamp >= today_start)
        .first()
    )
    if has_today:
        return today_start
    return datetime(1970, 1, 1)


def get_store_metrics(store_id: str) -> StoreMetrics:
    zone_names = _load_zone_names().get(store_id, {})
    with get_session() as session:
        window_start = _metrics_window_start(session, store_id)
        event_rows = (
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
                "metadata": metadata_from_json(e.metadata_json),
            }
            for e in event_rows
        ]
    from app.filters import filter_customer_events_with_entry

    customer_events = filter_customer_events_with_entry(events)

    dwell_by_zone: dict[str, list[int]] = defaultdict(list)
    for ev in customer_events:
        if ev["event_type"] == "ZONE_DWELL" and ev["zone_id"]:
            dwell_by_zone[ev["zone_id"]].append(ev["dwell_ms"])

    queue_joins = [
        e for e in customer_events if e["event_type"] == "BILLING_QUEUE_JOIN"
    ]
    abandons = [
        e for e in customer_events if e["event_type"] == "BILLING_QUEUE_ABANDON"
    ]
    queue_depths = [
        ev["metadata"].get("queue_depth")
        for ev in queue_joins
        if ev.get("metadata", {}).get("queue_depth") is not None
    ]
    queue_depth = max(0, max(queue_depths)) if queue_depths else 0
    with get_session() as session:
        live_billing = get_zone_occupancy(session, store_id)
        queue_depth = max(queue_depth, live_billing.get("BILLING_QUEUE", 0))

    abandonment_rate = 0.0
    if queue_joins:
        abandonment_rate = len(abandons) / len(queue_joins)

    converted = correlate_conversions(store_id)
    visitors = unique_customer_visitors(customer_events)
    unique_visitors = len(visitors)
    conversion_rate = 0.0
    if unique_visitors > 0:
        conversion_rate = len(converted & visitors) / unique_visitors

    zmap = zone_entry_exit_from_events(customer_events)
    zone_metrics: list[ZoneMetric] = []
    all_zones = set(zmap.keys()) | set(dwell_by_zone.keys())
    for zone_id in sorted(all_zones):
        zc = zmap.get(zone_id)
        avg_dwell = 0.0
        if zone_id in dwell_by_zone and dwell_by_zone[zone_id]:
            avg_dwell = sum(dwell_by_zone[zone_id]) / len(dwell_by_zone[zone_id])
        zone_metrics.append(
            ZoneMetric(
                zone_id=zone_id,
                zone_name=zone_names.get(zone_id, zone_id),
                entries=zc.get("entries", 0) if zc else 0,
                exits=zc.get("exits", 0) if zc else 0,
                avg_dwell_ms=avg_dwell,
            )
        )

    return StoreMetrics(
        store_id=store_id,
        unique_visitors=unique_visitors,
        conversion_rate=round(conversion_rate, 4),
        avg_dwell_by_zone=zone_metrics,
        queue_depth=max(0, queue_depth),
        abandonment_rate=round(abandonment_rate, 4),
        as_of=datetime.utcnow(),
    )


def get_zone_entry_exit_counts(store_id: str) -> list[dict]:
    with get_session() as session:
        window_start = _metrics_window_start(session, store_id)
        rows = (
            session.query(EventRow)
            .filter(EventRow.store_id == store_id, EventRow.timestamp >= window_start)
            .all()
        )
        events = [
            {
                "visitor_id": e.visitor_id,
                "event_type": e.event_type,
                "zone_id": e.zone_id,
                "is_staff": e.is_staff,
                "timestamp": e.timestamp,
            }
            for e in rows
        ]
    from app.filters import filter_customer_events_with_entry

    customer = filter_customer_events_with_entry(events)
    zmap = zone_entry_exit_from_events(events)
    zone_names = _load_zone_names().get(store_id, {})
    with get_session() as session:
        occupancy = get_zone_occupancy(session, store_id)

    all_zone_ids = set(zmap.keys()) | set(occupancy.keys())
    if "ENTRANCE" in zone_names and "ENTRANCE" not in all_zone_ids:
        all_zone_ids.add("ENTRANCE")

    result = []
    for zid in sorted(all_zone_ids):
        counts = zmap.get(zid, {"entries": 0, "exits": 0})
        result.append(
            {
                "zone_id": zid,
                "zone_name": zone_names.get(zid, zid),
                "entries": counts["entries"],
                "exits": counts["exits"],
                "current": occupancy.get(zid, 0),
            }
        )
    return result


def get_zone_counts_with_areas(store_id: str) -> dict:
    zones = get_zone_entry_exit_counts(store_id)
    with get_session() as session:
        area_presence = get_area_presence(session, store_id)
    return {
        "store_id": store_id,
        "zones": zones,
        "areas": rollup_zone_areas(store_id, zones, area_presence),
    }
