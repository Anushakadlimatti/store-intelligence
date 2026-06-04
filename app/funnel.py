from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from app.db import EventRow, get_session
from app.filters import filter_customer_events_with_entry, unique_customer_visitors
from app.ingestion import correlate_conversions
from app.models import FunnelStage, StoreFunnel


def _load_events(store_id: str, since: datetime) -> list[dict]:
    with get_session() as session:
        rows = (
            session.query(EventRow)
            .filter(
                EventRow.store_id == store_id,
                EventRow.timestamp >= since,
            )
            .order_by(EventRow.timestamp)
            .all()
        )
        return [
            {
                "visitor_id": e.visitor_id,
                "event_type": e.event_type,
                "zone_id": e.zone_id,
                "is_staff": e.is_staff,
                "timestamp": e.timestamp,
            }
            for e in rows
        ]


def _funnel_window_start(store_id: str) -> datetime:
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    with get_session() as session:
        has_today = (
            session.query(EventRow)
            .filter(EventRow.store_id == store_id, EventRow.timestamp >= today_start)
            .first()
        )
    return today_start if has_today else datetime(1970, 1, 1)


def get_store_funnel(store_id: str) -> StoreFunnel:
    events = filter_customer_events_with_entry(
        _load_events(store_id, _funnel_window_start(store_id))
    )
    customer_ids = unique_customer_visitors(events)

    sessions: dict[str, dict] = defaultdict(
        lambda: {
            "zone_visit": False,
            "billing": False,
            "purchase": False,
        }
    )

    for ev in events:
        vid = ev["visitor_id"]
        if vid not in customer_ids:
            continue
        s = sessions[vid]
        if ev["event_type"] in ("ZONE_ENTER", "ZONE_DWELL"):
            s["zone_visit"] = True
        elif ev["event_type"] == "BILLING_QUEUE_JOIN" or (
            ev["event_type"] == "ZONE_ENTER"
            and ev["zone_id"] in ("CASH_COUNTER", "BILLING_QUEUE")
        ):
            s["billing"] = True

    converted = correlate_conversions(store_id)
    for vid in converted:
        if vid in sessions:
            sessions[vid]["purchase"] = True

    entry_count = len(customer_ids)
    zone_count = sum(1 for s in sessions.values() if s["zone_visit"])
    billing_count = sum(1 for s in sessions.values() if s["billing"])
    purchase_count = sum(1 for s in sessions.values() if s["purchase"])

    def drop_off(current: int, previous: int) -> float | None:
        if previous == 0:
            return None
        return round((1 - current / previous) * 100, 2)

    stages = [
        FunnelStage(stage="Entry", count=entry_count, drop_off_pct=None),
        FunnelStage(
            stage="Zone Visit",
            count=zone_count,
            drop_off_pct=drop_off(zone_count, entry_count),
        ),
        FunnelStage(
            stage="Billing Queue",
            count=billing_count,
            drop_off_pct=drop_off(billing_count, zone_count),
        ),
        FunnelStage(
            stage="Purchase",
            count=purchase_count,
            drop_off_pct=drop_off(purchase_count, billing_count),
        ),
    ]

    return StoreFunnel(store_id=store_id, stages=stages)
