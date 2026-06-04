from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/store_intelligence.db")


class Base(DeclarativeBase):
    pass


class EventRow(Base):
    __tablename__ = "events"

    event_id = Column(String(36), primary_key=True)
    store_id = Column(String(64), index=True, nullable=False)
    camera_id = Column(String(64), nullable=False)
    visitor_id = Column(String(64), index=True, nullable=False)
    event_type = Column(String(32), index=True, nullable=False)
    timestamp = Column(DateTime, index=True, nullable=False)
    zone_id = Column(String(64), nullable=True)
    dwell_ms = Column(Integer, default=0)
    is_staff = Column(Boolean, default=False)
    confidence = Column(Float, default=0.0)
    metadata_json = Column(Text, default="{}")


class PosRow(Base):
    __tablename__ = "pos_transactions"

    transaction_id = Column(String(64), primary_key=True)
    store_id = Column(String(64), index=True, nullable=False)
    timestamp = Column(DateTime, index=True, nullable=False)
    basket_value_inr = Column(Float, nullable=False)


class ZoneCountRow(Base):
    __tablename__ = "zone_counts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    store_id = Column(String(64), index=True, nullable=False)
    zone_id = Column(String(64), index=True, nullable=False)
    entries = Column(Integer, default=0)
    exits = Column(Integer, default=0)
    updated_at = Column(DateTime, nullable=False)


class ZonePresenceRow(Base):
    """Visitors currently inside a zone (live occupancy)."""

    __tablename__ = "zone_presence"

    store_id = Column(String(64), primary_key=True)
    zone_id = Column(String(64), primary_key=True)
    visitor_id = Column(String(64), primary_key=True)
    entered_at = Column(DateTime, nullable=False)


_engine = None
_SessionLocal: sessionmaker[Session] | None = None
_db_available = True


def probe_db() -> bool:
    """Lightweight check; resets availability if DB is reachable."""
    global _db_available
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        _db_available = True
        return True
    except Exception:
        _db_available = False
        return False


def _ensure_data_dir() -> None:
    if DATABASE_URL.startswith("sqlite"):
        path = DATABASE_URL.replace("sqlite:///", "")
        Path(path).parent.mkdir(parents=True, exist_ok=True)


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        _ensure_data_dir()
        connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
        _engine = create_engine(DATABASE_URL, connect_args=connect_args)
        Base.metadata.create_all(_engine)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


@contextmanager
def get_session():
    global _db_available
    try:
        engine = get_engine()
        assert _SessionLocal is not None
        session = _SessionLocal()
        _db_available = True
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    except Exception:
        _db_available = False
        raise


def is_db_available() -> bool:
    if _db_available:
        return True
    return probe_db()


def set_db_unavailable() -> None:
    global _db_available
    _db_available = False


def metadata_to_json(meta: dict[str, Any]) -> str:
    return json.dumps(meta)


def metadata_from_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    return json.loads(raw)


def row_to_event_dict(row: EventRow) -> dict[str, Any]:
    return {
        "event_id": row.event_id,
        "store_id": row.store_id,
        "camera_id": row.camera_id,
        "visitor_id": row.visitor_id,
        "event_type": row.event_type,
        "timestamp": row.timestamp,
        "zone_id": row.zone_id,
        "dwell_ms": row.dwell_ms,
        "is_staff": row.is_staff,
        "confidence": row.confidence,
        "metadata": metadata_from_json(row.metadata_json),
    }
