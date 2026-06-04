from __future__ import annotations

from typing import Any

from app.entered_visitors import entered_visitor_ids, event_counts_for_customer

# Event types excluded from all customer analytics
EXCLUDED_EVENT_TYPES = frozenset({"REENTRY"})


def event_type_str(event_type) -> str:
    return event_type.value if hasattr(event_type, "value") else str(event_type)


def is_customer_event(
    event_type: str,
    is_staff: bool = False,
) -> bool:
    """Customer-only: not staff, not re-entry."""
    if is_staff:
        return False
    return event_type not in EXCLUDED_EVENT_TYPES


def filter_customer_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        e
        for e in events
        if is_customer_event(e.get("event_type", ""), e.get("is_staff", False))
    ]


def unique_customer_visitors(events: list[dict[str, Any]]) -> set[str]:
    """
    One visitor per visitor_id — first ENTRY only (ignores duplicate ENTRY rows).
    """
    seen: set[str] = set()
    visitors: set[str] = set()
    ordered = sorted(events, key=lambda e: e.get("timestamp", ""))
    for e in ordered:
        if e.get("event_type") != "ENTRY":
            continue
        vid = e.get("visitor_id")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        visitors.add(vid)
    return visitors


def filter_customer_events_with_entry(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Customer events; floor/billing only if visitor has an ENTRY (drops ghost tracks)."""
    entered = entered_visitor_ids(events)
    return [
        e
        for e in filter_customer_events(events)
        if event_counts_for_customer(e, entered)
    ]


def zone_entry_exit_from_events(events: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """
    Per-zone entry/exit counts for customers only.
    Duplicate zone visits: one enter + one exit count per (visitor_id, zone_id) pair.
    Floor/billing: only visitors who have ENTRY (same person, not duplicate track IDs).
    """
    zones: dict[str, dict[str, set[str]]] = {}
    enter_seen: set[tuple[str, str]] = set()
    exit_seen: set[tuple[str, str]] = set()

    for e in filter_customer_events_with_entry(events):
        zid = e.get("zone_id")
        vid = e.get("visitor_id")
        if not zid or not vid:
            continue
        if zid not in zones:
            zones[zid] = {"entries": set(), "exits": set()}
        et = e.get("event_type")
        key = (vid, zid)
        if et in ("ZONE_ENTER", "ENTRY", "BILLING_QUEUE_JOIN"):
            if key not in enter_seen:
                enter_seen.add(key)
                zones[zid]["entries"].add(vid)
        elif et in ("ZONE_EXIT", "EXIT", "BILLING_QUEUE_ABANDON"):
            if key not in exit_seen:
                exit_seen.add(key)
                zones[zid]["exits"].add(vid)

    return {
        zid: {"entries": len(v["entries"]), "exits": len(v["exits"])}
        for zid, v in zones.items()
    }
