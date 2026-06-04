from __future__ import annotations

from typing import Any


def entered_visitor_ids(events: list[dict[str, Any]]) -> set[str]:
    """Visitors who crossed the entrance line (real customers)."""
    return {
        e["visitor_id"]
        for e in events
        if e.get("event_type") == "ENTRY" and e.get("visitor_id") and not e.get("is_staff")
    }


def event_counts_for_customer(
    event: dict[str, Any],
    entered: set[str],
) -> bool:
    """Whether this event should affect floor/billing metrics and occupancy."""
    if event.get("is_staff"):
        return False
    zid = event.get("zone_id")
    et = event.get("event_type", "")
    vid = event.get("visitor_id")
    if not vid:
        return False
    if et == "ENTRY" or zid == "ENTRANCE":
        return True
    if et in ("EXIT",) and zid in (None, "ENTRANCE"):
        return True
    return vid in entered
