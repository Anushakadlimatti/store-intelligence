# PROMPT: Test API metrics, funnel, heatmap, ingest, filters, occupancy, POS ingestion, production readiness.
# CHANGES MADE: Merged test_api, test_filters, test_ingestion, test_occupancy, test_production (metrics/ingest parts).

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.db import EventRow, ZonePresenceRow, get_engine, get_session
from app.filters import (
    filter_customer_events,
    unique_customer_visitors,
    zone_entry_exit_from_events,
)
from app.ingestion import ingest_events, ingest_events_from_raw, load_pos_csv
from app.models import EventMetadata, EventType, StoreEvent
from app.occupancy import rebuild_occupancy_from_events
from conftest import event_fixture as _event
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
POS = ROOT / "POS - sample transactionsb1e826f.csv"


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert "status" in r.json()


def test_ingest_and_idempotent(client):
    ev = _event("ENTRY", zone_id="ENTRANCE")
    payload = {"events": [ev]}
    r1 = client.post("/events/ingest", json=payload)
    assert r1.status_code == 200
    assert r1.json()["accepted"] == 1
    r2 = client.post("/events/ingest", json=payload)
    assert r2.status_code == 200
    assert r2.json()["accepted"] == 1


def test_metrics_excludes_staff(client):
    client.post(
        "/events/ingest",
        json={
            "events": [
                _event("ENTRY", visitor_id="VIS_C1", zone_id="ENTRANCE"),
                _event("ENTRY", visitor_id="VIS_STAFF", zone_id="ENTRANCE", is_staff=True),
            ]
        },
    )
    r = client.get("/stores/STORE_1/metrics")
    assert r.status_code == 200
    assert r.json()["unique_visitors"] == 1


def test_zone_counts(client):
    client.post(
        "/events/ingest",
        json={
            "events": [
                _event("ENTRY", zone_id="ENTRANCE"),
                _event("ZONE_ENTER", zone_id="SALM"),
                _event("ZONE_EXIT", zone_id="SALM"),
            ]
        },
    )
    r = client.get("/stores/STORE_1/zones/counts")
    assert r.status_code == 200
    zones = {z["zone_id"]: z for z in r.json()["zones"]}
    assert zones["SALM"]["entries"] >= 1
    assert zones["SALM"]["exits"] >= 1


def test_funnel_ignores_reentry_and_duplicate_entry(client):
    client.post(
        "/events/ingest",
        json={
            "events": [
                _event("ENTRY", visitor_id="VIS_R1", zone_id="ENTRANCE"),
                _event("ENTRY", visitor_id="VIS_R1", zone_id="ENTRANCE"),
                _event("REENTRY", visitor_id="VIS_R1", zone_id="ENTRANCE"),
                _event("ZONE_ENTER", visitor_id="VIS_R1", zone_id="MAKEUP"),
            ]
        },
    )
    r = client.get("/stores/STORE_1/funnel")
    assert r.status_code == 200
    assert r.json()["stages"][0]["count"] == 1


def test_heatmap_low_confidence(client):
    client.post("/events/ingest", json={"events": [_event("ENTRY", zone_id="ENTRANCE")]})
    r = client.get("/stores/STORE_1/heatmap")
    assert r.status_code == 200
    assert r.json()["data_confidence"] == "LOW"


def test_empty_store_metrics(client):
    r = client.get("/stores/STORE_2/metrics")
    assert r.status_code == 200
    assert r.json()["unique_visitors"] == 0
    assert r.json()["conversion_rate"] == 0.0


def test_metrics_store_1_acceptance_gate(client):
    r = client.get("/stores/STORE_1/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["store_id"] == "STORE_1"
    assert "conversion_rate" in body


def test_layout_endpoint(client):
    r = client.get("/layout")
    assert r.status_code == 200
    assert len(r.json()["stores"]) == 2


def test_ingest_requires_events_wrapper(client):
    ev = _event("ENTRY", zone_id="ENTRANCE")
    r = client.post("/events/ingest", json=ev)
    assert r.status_code == 422


def test_ingest_partial_success_malformed():
    good = _event("ENTRY", zone_id="ENTRANCE")
    bad = {"event_id": "not-a-uuid", "store_id": "STORE_1"}
    result = ingest_events_from_raw([good, bad])
    assert result.accepted == 1
    assert result.rejected == 1
    assert result.errors[0].index == 1


def test_funnel_four_stages(client):
    client.post(
        "/events/ingest",
        json={
            "events": [
                _event("ENTRY", visitor_id="VIS_F1", zone_id="ENTRANCE"),
                _event("ZONE_ENTER", visitor_id="VIS_F1", zone_id="SALM"),
                _event("BILLING_QUEUE_JOIN", visitor_id="VIS_F1", zone_id="BILLING_QUEUE"),
            ]
        },
    )
    r = client.get("/stores/STORE_1/funnel")
    stages = {s["stage"]: s["count"] for s in r.json()["stages"]}
    assert stages["Entry"] == 1
    assert stages["Zone Visit"] == 1
    assert stages["Billing Queue"] == 1


def test_heatmap_normalized_range(client):
    client.post(
        "/events/ingest",
        json={
            "events": [
                _event("ENTRY", zone_id="ENTRANCE"),
                _event("ZONE_ENTER", zone_id="SALM"),
                _event("ZONE_DWELL", zone_id="SALM", dwell_ms=60000),
            ]
        },
    )
    r = client.get("/stores/STORE_1/heatmap")
    for z in r.json()["zones"]:
        assert 0 <= z["visit_frequency"] <= 100
        assert 0 <= z["avg_dwell_normalized"] <= 100


def test_excludes_staff_and_reentry():
    events = [
        {"event_type": "ENTRY", "visitor_id": "V1", "is_staff": False},
        {"event_type": "REENTRY", "visitor_id": "V1", "is_staff": False},
        {"event_type": "ENTRY", "visitor_id": "V2", "is_staff": True},
    ]
    out = filter_customer_events(events)
    assert len(out) == 1
    assert out[0]["visitor_id"] == "V1"


def test_unique_visitors_dedupe_entry():
    events = [
        {"event_type": "ENTRY", "visitor_id": "V1", "timestamp": "2026-04-10T12:00:00Z"},
        {"event_type": "ENTRY", "visitor_id": "V1", "timestamp": "2026-04-10T12:05:00Z"},
        {"event_type": "ENTRY", "visitor_id": "V2", "timestamp": "2026-04-10T12:01:00Z"},
    ]
    assert len(unique_customer_visitors(events)) == 2


def test_zone_counts_dedupe_per_visitor():
    events = [
        {"event_type": "ENTRY", "visitor_id": "V1", "zone_id": "ENTRANCE", "is_staff": False},
        {"event_type": "ZONE_ENTER", "visitor_id": "V1", "zone_id": "SALM", "is_staff": False},
        {"event_type": "ZONE_ENTER", "visitor_id": "V1", "zone_id": "SALM", "is_staff": False},
    ]
    z = zone_entry_exit_from_events(events)
    assert z["SALM"]["entries"] == 1


def test_pos_aggregation_pdf_schema():
    txns = load_pos_csv(POS, store_id_map={"ST1008": "STORE_1"})
    assert len(txns) > 0
    t0 = txns[0]
    assert t0.store_id == "STORE_1"
    assert t0.transaction_id
    assert t0.basket_value_inr >= 0
    order4 = [t for t in txns if t.transaction_id == "4"]
    assert len(order4) == 1
    assert order4[0].basket_value_inr >= 1


def _join_event(event_id: str | None = None, visitor_id: str = "VIS_000001") -> StoreEvent:
    return StoreEvent(
        event_id=event_id or str(uuid.uuid4()),
        store_id="STORE_1",
        camera_id="CAM_BILLING_01",
        visitor_id=visitor_id,
        event_type=EventType.BILLING_QUEUE_JOIN,
        timestamp=datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc),
        zone_id="BILLING_QUEUE",
        confidence=0.8,
        metadata=EventMetadata(),
    )


def test_duplicate_join_does_not_crash_ingest():
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    events = [
        _join_event(),
        StoreEvent(
            event_id=str(uuid.uuid4()),
            store_id="STORE_1",
            camera_id="CAM_ENTRY_01",
            visitor_id="VIS_000001",
            event_type=EventType.ENTRY,
            timestamp=datetime(2026, 4, 10, 11, 59, 0, tzinfo=timezone.utc),
            zone_id="ENTRANCE",
            confidence=0.9,
            metadata=EventMetadata(),
        ),
        _join_event(),
    ]
    events.sort(key=lambda e: e.timestamp)
    with Session() as session:
        session.query(ZonePresenceRow).filter_by(store_id="STORE_1").delete()
        session.commit()
    result = ingest_events(events, update_occupancy=True)
    assert result.accepted == 3
    with Session() as session:
        rebuild_occupancy_from_events(session, "STORE_1")
        session.commit()
        rows = (
            session.query(ZonePresenceRow)
            .filter_by(store_id="STORE_1", zone_id="BILLING_QUEUE")
            .all()
        )
        assert len(rows) == 1


def test_ingest_idempotent_same_payload_no_duplicate_rows(client: TestClient):
    ev = _event("ENTRY", zone_id="ENTRANCE")
    ev["event_id"] = str(uuid.uuid4())
    payload = {"events": [ev]}
    r1 = client.post("/events/ingest", json=payload)
    r2 = client.post("/events/ingest", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200
    with get_session() as session:
        count = session.query(EventRow).filter_by(event_id=ev["event_id"]).count()
    assert count == 1


def test_structured_request_log_on_ingest(client: TestClient, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger="store_intelligence")
    ev = _event("ENTRY", zone_id="ENTRANCE", store_id="STORE_2")
    client.post("/events/ingest", json={"events": [ev]})
    records = [r for r in caplog.records if r.name == "store_intelligence"]
    assert records
    log = json.loads(records[-1].message)
    assert log["endpoint"] == "/events/ingest"
    assert log["trace_id"]
    assert log["store_id"] == "STORE_2"
    assert log["event_count"] == 1
    assert log["status_code"] == 200


def test_all_staff_events_zero_customer_metrics(client: TestClient):
    client.post(
        "/events/ingest",
        json={
            "events": [
                _event("ENTRY", visitor_id="VIS_S1", zone_id="ENTRANCE", is_staff=True),
                _event("ENTRY", visitor_id="VIS_S2", zone_id="ENTRANCE", is_staff=True),
            ]
        },
    )
    r = client.get("/stores/STORE_1/metrics")
    assert r.json()["unique_visitors"] == 0
    assert r.json()["conversion_rate"] == 0.0


def test_zero_purchases_conversion_rate_zero(client: TestClient):
    client.post(
        "/events/ingest",
        json={
            "events": [
                _event("ENTRY", visitor_id="VIS_P1", zone_id="ENTRANCE", store_id="STORE_2"),
                _event("ENTRY", visitor_id="VIS_P2", zone_id="ENTRANCE", store_id="STORE_2"),
                _event("ZONE_ENTER", visitor_id="VIS_P1", zone_id="ZONE_01", store_id="STORE_2"),
            ]
        },
    )
    r = client.get("/stores/STORE_2/metrics")
    assert r.json()["unique_visitors"] == 2
    assert r.json()["conversion_rate"] == 0.0


def test_funnel_reentry_does_not_double_count(client: TestClient):
    client.post(
        "/events/ingest",
        json={
            "events": [
                _event("ENTRY", visitor_id="VIS_RE", zone_id="ENTRANCE"),
                _event("EXIT", visitor_id="VIS_RE", zone_id="ENTRANCE"),
                _event("REENTRY", visitor_id="VIS_RE", zone_id="ENTRANCE"),
                _event("ZONE_ENTER", visitor_id="VIS_RE", zone_id="MAKEUP"),
            ]
        },
    )
    r = client.get("/stores/STORE_1/funnel")
    assert r.json()["stages"][0]["count"] == 1


def test_ingest_batch_over_500_rejected():
    raw = [_event("ENTRY") for _ in range(501)]
    result = ingest_events_from_raw(raw)
    assert result.accepted == 0
    assert result.rejected == 501
    assert any("500" in e.message for e in result.errors)


def test_empty_store_all_endpoints(client: TestClient):
    for path in (
        "/stores/STORE_99/metrics",
        "/stores/STORE_99/funnel",
        "/stores/STORE_99/heatmap",
        "/stores/STORE_99/anomalies",
    ):
        r = client.get(path)
        assert r.status_code == 200


def test_health_stale_feed_warning(client):
    old_ts = "2020-01-01T12:00:00Z"
    ev = _event("ENTRY", zone_id="ENTRANCE")
    ev["timestamp"] = old_ts
    client.post("/events/ingest", json={"events": [ev]})
    r = client.get("/health")
    warnings = r.json().get("warnings", [])
    assert any("STALE_FEED" in w for w in warnings)
