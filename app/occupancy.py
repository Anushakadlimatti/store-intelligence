from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.db import ZonePresenceRow
from app.entered_visitors import entered_visitor_ids, event_counts_for_customer
from app.filters import event_type_str
from app.models import StoreEvent

_ENTER = frozenset({"ENTRY", "ZONE_ENTER", "BILLING_QUEUE_JOIN"})
_LEAVE = frozenset({"EXIT", "ZONE_EXIT", "BILLING_QUEUE_ABANDON"})


def _find_presence_row(
    session: Session,
    store_id: str,
    zone_id: str,
    visitor_id: str,
) -> ZonePresenceRow | None:
    """Find presence in DB or pending inserts in this session (avoids duplicate PK on flush)."""
    pk = {
        "store_id": store_id,
        "zone_id": zone_id,
        "visitor_id": visitor_id,
    }
    row = session.get(ZonePresenceRow, pk)
    if row is not None:
        return row
    for obj in session.new:
        if not isinstance(obj, ZonePresenceRow):
            continue
        if (
            obj.store_id == store_id
            and obj.zone_id == zone_id
            and obj.visitor_id == visitor_id
        ):
            return obj
    return None


def _zone_for_event(event: StoreEvent) -> str | None:
    if event.zone_id:
        return event.zone_id
    et = event_type_str(event.event_type)
    if et in _ENTER | _LEAVE:
        return "ENTRANCE"
    return None


def apply_occupancy(
    session: Session,
    event: StoreEvent,
    entered: set[str] | None = None,
) -> None:
    """Update who is in each zone now (+1 enter, -1 leave)."""
    if event.is_staff:
        return
    et = event_type_str(event.event_type)
    zone_id = _zone_for_event(event)
    if not zone_id:
        return

    if zone_id != "ENTRANCE" and et != "ENTRY":
        if entered is None:
            entered = entered_visitor_ids(_load_store_events(session, event.store_id))
        if event.visitor_id not in entered:
            return

    key = (event.store_id, zone_id, event.visitor_id)

    if et in _ENTER:
        existing = _find_presence_row(
            session, event.store_id, zone_id, event.visitor_id
        )
        if not existing:
            session.add(
                ZonePresenceRow(
                    store_id=event.store_id,
                    zone_id=zone_id,
                    visitor_id=event.visitor_id,
                    entered_at=event.timestamp.replace(tzinfo=None)
                    if event.timestamp.tzinfo
                    else event.timestamp,
                )
            )
    elif et in _LEAVE:
        row = _find_presence_row(
            session, event.store_id, zone_id, event.visitor_id
        )
        if row:
            session.delete(row)


def _load_store_events(session: Session, store_id: str) -> list[dict]:
    from app.db import EventRow

    rows = session.query(EventRow).filter_by(store_id=store_id).all()
    return [
        {
            "visitor_id": r.visitor_id,
            "event_type": r.event_type,
            "zone_id": r.zone_id,
            "is_staff": r.is_staff,
        }
        for r in rows
    ]


def get_zone_occupancy(session: Session, store_id: str) -> dict[str, int]:
    entered = entered_visitor_ids(_load_store_events(session, store_id))
    rows = (
        session.query(ZonePresenceRow.zone_id, ZonePresenceRow.visitor_id)
        .filter(ZonePresenceRow.store_id == store_id)
        .all()
    )
    counts: dict[str, int] = {}
    for zone_id, visitor_id in rows:
        if zone_id != "ENTRANCE" and visitor_id not in entered:
            continue
        counts[zone_id] = counts.get(zone_id, 0) + 1
    return counts


def get_area_presence(session: Session, store_id: str) -> dict[str, set[str]]:
    """Distinct visitors per area (entrance / main floor / billing)."""
    from app.zone_areas import area_zone_members

    entered = entered_visitor_ids(_load_store_events(session, store_id))
    members = area_zone_members(store_id)
    rows = (
        session.query(ZonePresenceRow.zone_id, ZonePresenceRow.visitor_id)
        .filter(ZonePresenceRow.store_id == store_id)
        .all()
    )
    by_area: dict[str, set[str]] = {aid: set() for aid in members}
    for zone_id, visitor_id in rows:
        if zone_id != "ENTRANCE" and visitor_id not in entered:
            continue
        for area_id, zone_ids in members.items():
            if zone_id in zone_ids:
                by_area[area_id].add(visitor_id)
    return by_area


def reset_store_occupancy(session: Session, store_id: str) -> None:
    session.query(ZonePresenceRow).filter_by(store_id=store_id).delete()


def rebuild_occupancy_from_events(session: Session, store_id: str) -> None:
    """Replay stored events in time order to rebuild live occupancy."""
    from app.db import EventRow
    from app.filters import is_customer_event
    from app.models import EventMetadata, EventType, StoreEvent

    reset_store_occupancy(session, store_id)
    rows = (
        session.query(EventRow)
        .filter_by(store_id=store_id)
        .order_by(EventRow.timestamp)
        .all()
    )
    entered: set[str] = set()
    for row in rows:
        if not is_customer_event(row.event_type, row.is_staff):
            continue
        if row.event_type == "ENTRY":
            entered.add(row.visitor_id)
    for row in rows:
        if not is_customer_event(row.event_type, row.is_staff):
            continue
        if row.zone_id and row.zone_id != "ENTRANCE" and row.visitor_id not in entered:
            continue
        ev = StoreEvent(
            event_id=row.event_id,
            store_id=row.store_id,
            camera_id=row.camera_id,
            visitor_id=row.visitor_id,
            event_type=EventType(row.event_type),
            timestamp=row.timestamp,
            zone_id=row.zone_id,
            dwell_ms=row.dwell_ms,
            is_staff=row.is_staff,
            confidence=row.confidence,
            metadata=EventMetadata(),
        )
        apply_occupancy(session, ev)
