from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from app.db import EventRow, get_session, is_db_available
from app.models import HealthResponse, StoreHealth


def get_health() -> HealthResponse:
    warnings: list[str] = []
    stores: list[StoreHealth] = []

    if not is_db_available():
        return HealthResponse(status="degraded", stores=[], warnings=["database_unavailable"])

    layout = json.loads(
        (Path(__file__).resolve().parent.parent / "store_layout.json").read_text()
    )
    now = datetime.utcnow()

    with get_session() as session:
        for store in layout["stores"]:
            store_id = store["store_id"]
            last = (
                session.query(EventRow)
                .filter_by(store_id=store_id)
                .order_by(EventRow.timestamp.desc())
                .first()
            )
            last_ts = last.timestamp if last else None
            status = "ok"
            if last_ts and (now - last_ts) > timedelta(minutes=10):
                status = "stale"
                warnings.append(f"STALE_FEED:{store_id}")
            elif not last_ts:
                status = "no_data"
            stores.append(StoreHealth(store_id=store_id, last_event_at=last_ts, status=status))

    overall = "ok"
    if warnings:
        overall = "degraded"
    if not is_db_available():
        overall = "unavailable"

    return HealthResponse(status=overall, stores=stores, warnings=warnings)
