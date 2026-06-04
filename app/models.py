from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class EventMetadata(BaseModel):
    queue_depth: int | None = None
    sku_zone: str | None = None
    session_seq: int | None = None


class StoreEvent(BaseModel):
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: EventType
    timestamp: datetime
    zone_id: str | None = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("event_id")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        UUID(v, version=4)
        return v


_INGEST_EXAMPLE_EVENT = {
    "event_id": "7afbac7c-8158-4b94-946b-4413cb5b653f",
    "store_id": "STORE_1",
    "camera_id": "CAM_ENTRY_01",
    "visitor_id": "VIS_000011",
    "event_type": "ENTRY",
    "timestamp": "2026-04-10T12:00:30.363667Z",
    "zone_id": "ENTRANCE",
    "dwell_ms": 0,
    "is_staff": False,
    "confidence": 0.9,
    "metadata": {
        "queue_depth": None,
        "sku_zone": "ENTRANCE",
        "session_seq": 1,
    },
}


class IngestRequest(BaseModel):
    """POST /events/ingest — always wrap events in this array (max 500)."""

    events: list[StoreEvent] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Batch of events matching the PDF schema",
    )

    model_config = ConfigDict(
        json_schema_extra={"example": {"events": [_INGEST_EXAMPLE_EVENT]}}
    )


class IngestError(BaseModel):
    index: int
    event_id: str | None
    message: str


class IngestResponse(BaseModel):
    accepted: int
    rejected: int
    errors: list[IngestError]


class ZoneMetric(BaseModel):
    zone_id: str
    zone_name: str
    entries: int
    exits: int
    avg_dwell_ms: float


class StoreMetrics(BaseModel):
    store_id: str
    unique_visitors: int
    conversion_rate: float
    avg_dwell_by_zone: list[ZoneMetric]
    queue_depth: int
    abandonment_rate: float
    as_of: datetime


class FunnelStage(BaseModel):
    stage: str
    count: int
    drop_off_pct: float | None = None


class StoreFunnel(BaseModel):
    store_id: str
    stages: list[FunnelStage]


class HeatmapZone(BaseModel):
    zone_id: str
    visit_frequency: float
    avg_dwell_normalized: float


class StoreHeatmap(BaseModel):
    store_id: str
    zones: list[HeatmapZone]
    data_confidence: str


class AnomalySeverity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class Anomaly(BaseModel):
    anomaly_type: str
    severity: AnomalySeverity
    message: str
    suggested_action: str
    detected_at: datetime


class StoreAnomalies(BaseModel):
    store_id: str
    anomalies: list[Anomaly]


class StoreHealth(BaseModel):
    store_id: str
    last_event_at: datetime | None
    status: str


class HealthResponse(BaseModel):
    status: str
    stores: list[StoreHealth]
    warnings: list[str]


class PosTransaction(BaseModel):
    store_id: str
    transaction_id: str
    timestamp: datetime
    basket_value_inr: float
