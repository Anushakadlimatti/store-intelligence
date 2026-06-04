from __future__ import annotations

import json
from pathlib import Path

_LAYOUT_PATH = Path(__file__).resolve().parent.parent / "store_layout.json"

_AREA_LABELS = {
    "ENTRANCE": "Entrance",
    "MAIN_FLOOR": "Main area",
    "BILLING": "Billing area",
}

_ENTRANCE = frozenset({"ENTRANCE"})
_BILLING = frozenset({"CASH_COUNTER", "BILLING_QUEUE", "BILLING_CHECKOUT"})


def _load_layout() -> dict:
    return json.loads(_LAYOUT_PATH.read_text(encoding="utf-8"))


def area_zone_members(store_id: str) -> dict[str, frozenset[str]]:
    """Map area id -> zone ids that belong to that area."""
    for store in _load_layout()["stores"]:
        if store["store_id"] != store_id:
            continue
        all_zones = {z["zone_id"] for z in store["zones"]}
        main_floor = frozenset(all_zones - _ENTRANCE - _BILLING)
        return {
            "ENTRANCE": _ENTRANCE,
            "MAIN_FLOOR": main_floor,
            "BILLING": _BILLING,
        }
    return {
        "ENTRANCE": _ENTRANCE,
        "MAIN_FLOOR": frozenset(),
        "BILLING": _BILLING,
    }


def rollup_zone_areas(
    store_id: str,
    zones: list[dict],
    area_presence: dict[str, set[str]] | None = None,
) -> list[dict]:
    """
    Aggregate per-zone counts into entrance / main floor / billing.
    ``current`` uses distinct visitors per area (never sum zones — avoids double counting).
    """
    by_id = {z["zone_id"]: z for z in zones}
    members = area_zone_members(store_id)
    areas: list[dict] = []
    for area_id in ("ENTRANCE", "MAIN_FLOOR", "BILLING"):
        member_ids = members.get(area_id, frozenset())
        if area_presence is not None:
            current = len(area_presence.get(area_id, set()))
        else:
            current = sum(by_id.get(zid, {}).get("current", 0) for zid in member_ids)
        entries = sum(by_id.get(zid, {}).get("entries", 0) for zid in member_ids)
        exits = sum(by_id.get(zid, {}).get("exits", 0) for zid in member_ids)
        areas.append(
            {
                "area_id": area_id,
                "area_name": _AREA_LABELS[area_id],
                "current": current,
                "entries": entries,
                "exits": exits,
                "zone_ids": sorted(member_ids),
            }
        )
    return areas
