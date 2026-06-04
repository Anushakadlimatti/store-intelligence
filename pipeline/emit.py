from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.models import EventMetadata, EventType, StoreEvent


def clip_timestamp(clip_start: datetime, frame_idx: int, fps: float) -> datetime:
    offset = timedelta(seconds=frame_idx / fps)
    return (clip_start + offset).replace(tzinfo=timezone.utc)


def make_event(
    store_id: str,
    camera_id: str,
    visitor_id: str,
    event_type: EventType,
    timestamp: datetime,
    zone_id: str | None = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 0.85,
    metadata: dict[str, Any] | None = None,
    session_seq: int = 1,
) -> StoreEvent:
    meta = metadata or {}
    meta.setdefault("session_seq", session_seq)
    sku = meta.get("sku_zone")
    return StoreEvent(
        event_id=str(uuid.uuid4()),
        store_id=store_id,
        camera_id=camera_id,
        visitor_id=visitor_id,
        event_type=event_type,
        timestamp=timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc),
        zone_id=zone_id,
        dwell_ms=dwell_ms,
        is_staff=is_staff,
        confidence=confidence,
        metadata=EventMetadata(
            queue_depth=meta.get("queue_depth"),
            sku_zone=sku or zone_id,
            session_seq=meta.get("session_seq", session_seq),
        ),
    )


def events_to_jsonl(events: list[StoreEvent]) -> str:
    lines = []
    for e in events:
        d = e.model_dump(mode="json")
        lines.append(
            __import__("json").dumps(d, default=str)
        )
    return "\n".join(lines)
