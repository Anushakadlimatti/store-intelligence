# PROMPT: Test anomaly detection API and graceful degradation (503, no stack traces).
# CHANGES MADE: Merged test_api anomalies/health checks and test_production error-handling cases.

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from conftest import event_fixture as _event


def test_anomalies_endpoint(client):
    r = client.get("/stores/STORE_1/anomalies")
    assert r.status_code == 200
    assert "anomalies" in r.json()


def test_db_unavailable_returns_structured_503(client: TestClient):
    with patch("app.main.is_db_available", return_value=False):
        r = client.get("/stores/STORE_1/metrics")
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "service_unavailable"
    assert "message" in body
    assert "Traceback" not in r.text


def test_health_unavailable_structured_503(client: TestClient):
    with patch("app.main.is_db_available", return_value=False):
        r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["status"] == "unavailable"
    assert "database_unavailable" in r.json()["warnings"]


def test_no_stack_trace_on_internal_error():
    from app.main import app

    async def failing_pool(fn, *args, **kwargs):
        raise RuntimeError("secret db password leaked")

    client = TestClient(app, raise_server_exceptions=False)
    with patch("app.main.run_in_threadpool", failing_pool):
        r = client.get("/stores/STORE_1/metrics")
    assert r.status_code == 500
    assert r.json()["error"] == "internal_error"
    assert "secret" not in r.text
    assert "Traceback" not in r.text
