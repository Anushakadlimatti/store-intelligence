# PROMPT: Test detection pipeline — dress-code staff rule, entry-gated floor events, zone area rollups.
# CHANGES MADE: Merged test_staff, test_entered_visitors, test_zone_areas into one file per repo layout.

import numpy as np

from app.filters import filter_customer_events_with_entry, zone_entry_exit_from_events
from app.zone_areas import area_zone_members, rollup_zone_areas
from pipeline.staff import (
    STAFF_UNIFORM_MIN_COUNT,
    staff_indices_from_dress_code,
    upper_body_dress_signature,
)


def test_same_dress_code_two_or_more_are_staff():
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    frame[20:80, 30:90] = (0, 0, 200)
    frame[20:80, 110:170] = (0, 0, 200)
    bboxes = [(30.0, 20.0, 90.0, 120.0), (110.0, 20.0, 170.0, 120.0)]
    staff = staff_indices_from_dress_code(frame, bboxes, min_count=STAFF_UNIFORM_MIN_COUNT)
    assert staff == {0, 1}


def test_single_person_not_staff_by_dress_code():
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    frame[20:80, 30:90] = (0, 0, 200)
    bboxes = [(30.0, 20.0, 90.0, 120.0)]
    assert staff_indices_from_dress_code(frame, bboxes) == set()


def test_different_dress_codes_not_staff():
    frame = np.zeros((240, 240, 3), dtype=np.uint8)
    frame[10:110, 10:110] = (0, 0, 255)
    frame[10:110, 120:220] = (255, 255, 0)
    bboxes = [(10.0, 10.0, 110.0, 200.0), (120.0, 10.0, 220.0, 200.0)]
    assert staff_indices_from_dress_code(frame, bboxes) == set()


def test_signature_not_none_for_valid_crop():
    frame = np.full((100, 100, 3), 128, dtype=np.uint8)
    sig = upper_body_dress_signature(frame, (10.0, 10.0, 80.0, 90.0))
    assert sig is not None


def test_floor_events_require_entry():
    events = [
        {"event_type": "ENTRY", "visitor_id": "V1", "zone_id": "ENTRANCE", "is_staff": False},
        {"event_type": "ZONE_ENTER", "visitor_id": "V1", "zone_id": "MAKEUP", "is_staff": False},
        {"event_type": "ZONE_ENTER", "visitor_id": "GHOST", "zone_id": "MAKEUP", "is_staff": False},
    ]
    out = filter_customer_events_with_entry(events)
    assert len(out) == 2
    assert all(e["visitor_id"] != "GHOST" for e in out if e["event_type"] == "ZONE_ENTER")
    zmap = zone_entry_exit_from_events(events)
    assert "MAKEUP" in zmap
    assert zmap["MAKEUP"]["entries"] == 1


def test_store2_area_members():
    members = area_zone_members("STORE_2")
    assert "ENTRANCE" in members["ENTRANCE"]
    assert "BILLING_QUEUE" in members["BILLING"]
    assert "ZONE_01" in members["MAIN_FLOOR"]
    assert "ENTRANCE" not in members["MAIN_FLOOR"]


def test_rollup_distinct_visitors_per_area():
    zones = [
        {"zone_id": "ENTRANCE", "current": 2, "entries": 10, "exits": 7},
        {"zone_id": "ZONE_01", "current": 2, "entries": 5, "exits": 3},
        {"zone_id": "ZONE_02", "current": 1, "entries": 2, "exits": 1},
        {"zone_id": "CASH_COUNTER", "current": 1, "entries": 4, "exits": 3},
        {"zone_id": "BILLING_QUEUE", "current": 2, "entries": 6, "exits": 4},
    ]
    presence = {
        "ENTRANCE": {"V1", "V2"},
        "MAIN_FLOOR": {"V1", "V3"},
        "BILLING": {"V4"},
    }
    areas = {a["area_id"]: a for a in rollup_zone_areas("STORE_2", zones, presence)}
    assert areas["ENTRANCE"]["current"] == 2
    assert areas["MAIN_FLOOR"]["current"] == 2
    assert areas["BILLING"]["current"] == 1
