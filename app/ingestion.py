from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from app.db import EventRow, PosRow, ZoneCountRow, get_session, metadata_to_json
from app.filters import is_customer_event
from app.models import IngestError, IngestResponse, PosTransaction, StoreEvent
from app.entered_visitors import entered_visitor_ids
from app.occupancy import apply_occupancy, _load_store_events

MAX_INGEST_BATCH = 500


def ingest_events_from_raw(
    raw_events: list[dict],
    *,
    update_occupancy: bool = True,
) -> IngestResponse:
    """Validate each event; store all valid rows; idempotent on event_id."""
    if len(raw_events) > MAX_INGEST_BATCH:
        return IngestResponse(
            accepted=0,
            rejected=len(raw_events),
            errors=[
                IngestError(
                    index=-1,
                    event_id=None,
                    message=f"Batch exceeds maximum of {MAX_INGEST_BATCH} events",
                )
            ],
        )

    parsed: list[tuple[int, StoreEvent]] = []
    errors: list[IngestError] = []

    for idx, raw in enumerate(raw_events):
        if not isinstance(raw, dict):
            errors.append(
                IngestError(
                    index=idx,
                    event_id=None,
                    message="Event must be a JSON object",
                )
            )
            continue
        try:
            event = StoreEvent.model_validate(raw)
            parsed.append((idx, event))
        except ValidationError as exc:
            errors.append(
                IngestError(
                    index=idx,
                    event_id=raw.get("event_id") if isinstance(raw.get("event_id"), str) else None,
                    message=str(exc.errors()[0]["msg"]) if exc.errors() else str(exc),
                )
            )
        except Exception as exc:
            errors.append(
                IngestError(
                    index=idx,
                    event_id=raw.get("event_id") if isinstance(raw.get("event_id"), str) else None,
                    message=str(exc),
                )
            )

    accepted = 0
    with get_session() as session:
        store_entered: dict[str, set[str]] = {}
        for idx, event in parsed:
            try:
                existing = session.get(EventRow, event.event_id)
                if existing:
                    accepted += 1
                    continue

                row = EventRow(
                    event_id=event.event_id,
                    store_id=event.store_id,
                    camera_id=event.camera_id,
                    visitor_id=event.visitor_id,
                    event_type=event.event_type.value,
                    timestamp=event.timestamp.replace(tzinfo=None)
                    if event.timestamp.tzinfo
                    else event.timestamp,
                    zone_id=event.zone_id,
                    dwell_ms=event.dwell_ms,
                    is_staff=event.is_staff,
                    confidence=event.confidence,
                    metadata_json=metadata_to_json(event.metadata.model_dump()),
                )
                session.add(row)
                sid = event.store_id
                if sid not in store_entered:
                    store_entered[sid] = entered_visitor_ids(
                        _load_store_events(session, sid)
                    )
                if event.event_type.value == "ENTRY" and not event.is_staff:
                    store_entered[sid].add(event.visitor_id)

                if _affects_zone_counters(event):
                    _update_zone_counts(session, event)
                if update_occupancy and _affects_occupancy(event):
                    apply_occupancy(session, event, store_entered[sid])
                accepted += 1
            except Exception as exc:
                errors.append(
                    IngestError(
                        index=idx,
                        event_id=event.event_id,
                        message=str(exc),
                    )
                )

    return IngestResponse(
        accepted=accepted,
        rejected=len(errors),
        errors=errors,
    )


def ingest_events(
    events: list[StoreEvent],
    *,
    update_occupancy: bool = True,
) -> IngestResponse:
    """Ingest pre-validated events (e.g. reload script)."""
    return ingest_events_from_raw(
        [e.model_dump(mode="json") for e in events],
        update_occupancy=update_occupancy,
    )


def _affects_zone_counters(event: StoreEvent) -> bool:
    et = event.event_type.value
    if et == "REENTRY":
        return False
    return is_customer_event(et, event.is_staff) and et in (
        "ZONE_ENTER",
        "ZONE_EXIT",
        "ENTRY",
        "EXIT",
    )


def _affects_occupancy(event: StoreEvent) -> bool:
    et = event.event_type.value
    if et == "REENTRY":
        return False
    return is_customer_event(et, event.is_staff)


def _event_type_str(event: StoreEvent) -> str:
    et = event.event_type
    return et.value if hasattr(et, "value") else str(et)


def _update_zone_counts(session, event: StoreEvent) -> None:
    if not event.zone_id:
        return
    etype = _event_type_str(event)
    if etype not in ("ZONE_ENTER", "ZONE_EXIT", "ENTRY", "EXIT"):
        return

    session.flush()
    row = (
        session.query(ZoneCountRow)
        .filter_by(store_id=event.store_id, zone_id=event.zone_id)
        .first()
    )
    now = datetime.utcnow()
    if not row:
        row = ZoneCountRow(
            store_id=event.store_id,
            zone_id=event.zone_id,
            entries=0,
            exits=0,
            updated_at=now,
        )
        session.add(row)

    if etype in ("ZONE_ENTER", "ENTRY"):
        row.entries += 1
    elif etype in ("ZONE_EXIT", "EXIT"):
        row.exits += 1
    row.updated_at = now


def load_pos_csv(
    csv_path: Path,
    store_id_map: dict[str, str] | None = None,
) -> list[PosTransaction]:
    """Aggregate line-item POS CSV into PDF transaction schema."""
    store_id_map = store_id_map or {"ST1008": "STORE_1"}
    orders: dict[str, dict] = {}

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            order_id = row["order_id"]
            raw_store = row["store_id"]
            store_id = store_id_map.get(raw_store, raw_store)
            date_str = row["order_date"]
            time_str = row["order_time"]
            dt = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M:%S")
            amount = float(row["total_amount"])

            if order_id not in orders:
                orders[order_id] = {
                    "store_id": store_id,
                    "timestamp": dt,
                    "basket_value_inr": 0.0,
                }
            orders[order_id]["basket_value_inr"] += amount

    return [
        PosTransaction(
            store_id=v["store_id"],
            transaction_id=oid,
            timestamp=v["timestamp"].replace(tzinfo=timezone.utc),
            basket_value_inr=round(v["basket_value_inr"], 2),
        )
        for oid, v in orders.items()
    ]


def ingest_pos_transactions(transactions: list[PosTransaction]) -> int:
    count = 0
    with get_session() as session:
        for txn in transactions:
            if session.get(PosRow, txn.transaction_id):
                continue
            session.add(
                PosRow(
                    transaction_id=txn.transaction_id,
                    store_id=txn.store_id,
                    timestamp=txn.timestamp.replace(tzinfo=None)
                    if txn.timestamp.tzinfo
                    else txn.timestamp,
                    basket_value_inr=txn.basket_value_inr,
                )
            )
            count += 1
    return count


def correlate_conversions(store_id: str, window_minutes: int = 5) -> set[str]:
    """Visitors in billing zone within window before POS txn count as converted."""
    converted: set[str] = set()
    with get_session() as session:
        txns = session.query(PosRow).filter_by(store_id=store_id).all()
        for txn in txns:
            window_start = txn.timestamp - timedelta(minutes=window_minutes)
            billing_events = (
                session.query(EventRow)
                .filter(
                    EventRow.store_id == store_id,
                    EventRow.event_type.in_(
                        ["BILLING_QUEUE_JOIN", "ZONE_ENTER"]
                    ),
                    EventRow.zone_id.in_(["CASH_COUNTER", "BILLING_QUEUE"]),
                    EventRow.timestamp >= window_start,
                    EventRow.timestamp <= txn.timestamp,
                    EventRow.is_staff == False,  # noqa: E712
                )
                .all()
            )
            for ev in billing_events:
                converted.add(ev.visitor_id)
    return converted
