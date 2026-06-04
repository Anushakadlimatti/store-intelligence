# PROMPT: Shared pytest fixtures - temp SQLite DB, FastAPI TestClient, PDF-schema event builder.
# CHANGES MADE: Centralized fresh_db + client + event_fixture for test_api and test_production.

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_store_intelligence.db")

from app.db import Base, get_engine  # noqa: E402
from app.main import app  # noqa: E402


def event_fixture(
    event_type: str,
    visitor_id: str = "VIS_000001",
    store_id: str = "STORE_1",
    zone_id: str | None = None,
    is_staff: bool = False,
    dwell_ms: int = 0,
) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": 0.9,
        "metadata": {"queue_depth": None, "sku_zone": zone_id, "session_seq": 1},
    }


@pytest.fixture(autouse=True)
def fresh_db():
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)
