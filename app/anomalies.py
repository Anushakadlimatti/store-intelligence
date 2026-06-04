from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from app.db import EventRow, PosRow, get_session
from app.filters import filter_customer_events, unique_customer_visitors
from app.models import Anomaly, AnomalySeverity, StoreAnomalies


def _load_events(store_id: str, since: datetime) -> list[dict]:
    with get_session() as session:
        rows = (
            session.query(EventRow)
            .filter(
                EventRow.store_id == store_id,
                EventRow.timestamp >= since,
                EventRow.is_staff == False,  # noqa: E712
            )
            .all()
        )
        return [
            {
                "visitor_id": e.visitor_id,
                "event_type": e.event_type,
                "zone_id": e.zone_id,
                "timestamp": e.timestamp,
            }
            for e in rows
        ]


def get_store_anomalies(store_id: str) -> StoreAnomalies:
    now = datetime.utcnow()
    anomalies: list[Anomaly] = []
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    events = filter_customer_events(_load_events(store_id, today_start))
    week_events = filter_customer_events(_load_events(store_id, now - timedelta(days=7)))

    with get_session() as session:
        txns_today = (
            session.query(PosRow)
            .filter(PosRow.store_id == store_id, PosRow.timestamp >= today_start)
            .all()
        )
        txns_week = (
            session.query(PosRow)
            .filter(PosRow.store_id == store_id, PosRow.timestamp >= now - timedelta(days=7))
            .all()
        )
        txn_today_data = [
            {"transaction_id": t.transaction_id, "timestamp": t.timestamp}
            for t in txns_today
        ]
        txn_week_data = [
            {"transaction_id": t.transaction_id, "timestamp": t.timestamp}
            for t in txns_week
        ]

    queue_joins = [e for e in events if e["event_type"] == "BILLING_QUEUE_JOIN"]
    if len(queue_joins) >= 5:
        anomalies.append(
            Anomaly(
                anomaly_type="BILLING_QUEUE_SPIKE",
                severity=AnomalySeverity.WARN,
                message=f"Billing queue spike: {len(queue_joins)} joins in the last hour window.",
                suggested_action="Deploy additional cashier or open express lane.",
                detected_at=now,
            )
        )

    visitors_today = len(unique_customer_visitors(events))
    purchases_today = len({t["transaction_id"] for t in txn_today_data})
    conv_today = purchases_today / visitors_today if visitors_today else 0.0

    daily_visitors: dict[str, set] = defaultdict(set)
    daily_purchases: dict[str, int] = defaultdict(int)

    week_customers = unique_customer_visitors(week_events)
    for e in week_events:
        if e["event_type"] == "ENTRY" and e["visitor_id"] in week_customers:
            day = e["timestamp"].date().isoformat()
            daily_visitors[day].add(e["visitor_id"])
    for t in txn_week_data:
        day = t["timestamp"].date().isoformat()
        daily_purchases[day] += 1

    daily_conv: dict[str, float] = {}
    for day, vset in daily_visitors.items():
        if vset:
            daily_conv[day] = daily_purchases[day] / len(vset)

    if daily_conv:
        avg_conv = sum(daily_conv.values()) / len(daily_conv)
        if avg_conv > 0 and conv_today < avg_conv * 0.7 and visitors_today >= 3:
            anomalies.append(
                Anomaly(
                    anomaly_type="CONVERSION_DROP",
                    severity=AnomalySeverity.CRITICAL,
                    message=f"Conversion {conv_today:.1%} vs 7-day avg {avg_conv:.1%}.",
                    suggested_action="Review staffing, promotions, and queue wait times.",
                    detected_at=now,
                )
            )

    zone_last_visit: dict[str, datetime] = {}
    for e in events:
        if e["event_type"] in ("ZONE_ENTER", "ZONE_DWELL") and e["zone_id"]:
            zone_last_visit[e["zone_id"]] = max(
                zone_last_visit.get(e["zone_id"], e["timestamp"]), e["timestamp"]
            )

    for zone_id, last_ts in zone_last_visit.items():
        if (now - last_ts).total_seconds() > 30 * 60:
            anomalies.append(
                Anomaly(
                    anomaly_type="DEAD_ZONE",
                    severity=AnomalySeverity.INFO,
                    message=f"Zone {zone_id} has had no visits for 30+ minutes.",
                    suggested_action="Check camera coverage or run a zone promotion.",
                    detected_at=now,
                )
            )

    all_zones = {e["zone_id"] for e in events if e["zone_id"]}
    layout = json.loads(
        (Path(__file__).resolve().parent.parent / "store_layout.json").read_text()
    )
    layout_zones = set()
    for s in layout["stores"]:
        if s["store_id"] == store_id:
            layout_zones = {z["zone_id"] for z in s["zones"] if z["zone_id"] != "ENTRANCE"}

    for zone_id in layout_zones - all_zones:
        anomalies.append(
            Anomaly(
                anomaly_type="DEAD_ZONE",
                severity=AnomalySeverity.WARN,
                message=f"Zone {zone_id} has zero visits today.",
                suggested_action="Verify detection ROI calibration for this zone.",
                detected_at=now,
            )
        )

    return StoreAnomalies(store_id=store_id, anomalies=anomalies)
