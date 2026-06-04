from __future__ import annotations

from datetime import datetime

from app.db import EventRow, get_session
from app.filters import is_customer_event


def get_recent_events(store_id: str, limit: int = 20) -> list[dict]:
    out: list[dict] = []
    with get_session() as session:
        rows = (
            session.query(EventRow)
            .filter_by(store_id=store_id)
            .order_by(EventRow.timestamp.desc())
            .limit(limit * 3)
            .all()
        )
        for row in rows:
            if not is_customer_event(row.event_type, row.is_staff):
                continue
            out.append(
                {
                    "event_type": row.event_type,
                    "visitor_id": row.visitor_id,
                    "zone_id": row.zone_id,
                    "timestamp": row.timestamp.isoformat()
                    if isinstance(row.timestamp, datetime)
                    else str(row.timestamp),
                }
            )
            if len(out) >= limit:
                break
    return out
