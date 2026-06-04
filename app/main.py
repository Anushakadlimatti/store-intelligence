from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from app.anomalies import get_store_anomalies
from app.funnel import get_store_funnel
from app.db import get_session, is_db_available, set_db_unavailable
from app.heatmap import get_store_heatmap
from app.health import get_health
from app.ingestion import ingest_events_from_raw, ingest_pos_transactions, load_pos_csv
from app.metrics import get_store_metrics, get_zone_entry_exit_counts
from app.models import IngestRequest, IngestResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
logger = logging.getLogger("store_intelligence")

ROOT = Path(__file__).resolve().parent.parent
LAYOUT_PATH = ROOT / "store_layout.json"
POS_PATH = ROOT / "POS - sample transactionsb1e826f.csv"
DASHBOARD_DIR = ROOT / "dashboard" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from app.db import get_engine

        get_engine()
        if POS_PATH.exists():
            txns = load_pos_csv(POS_PATH, store_id_map={"ST1008": "STORE_1"})
            ingest_pos_transactions(txns)
            logger.info(
                json.dumps(
                    {
                        "msg": "pos_loaded",
                        "count": len(txns),
                        "store_id": "STORE_1",
                    }
                )
            )
    except Exception as exc:
        logger.warning(json.dumps({"msg": "startup_warning", "error": str(exc)}))
    yield


app = FastAPI(title="Store Intelligence API", version="1.0.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "message": "Request body failed schema validation.",
            "details": exc.errors(),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, HTTPException):
        raise exc
    logger.exception(
        json.dumps(
            {
                "msg": "unhandled_exception",
                "endpoint": request.url.path,
                "error_type": type(exc).__name__,
            }
        )
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "An unexpected error occurred.",
        },
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if DASHBOARD_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id
    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    store_id = (
        request.path_params.get("store_id")
        or getattr(request.state, "store_id", None)
        or request.query_params.get("store_id")
        or None
    )
    log_entry = {
        "trace_id": trace_id,
        "store_id": store_id,
        "endpoint": request.url.path,
        "latency_ms": latency_ms,
        "status_code": response.status_code,
    }
    if request.url.path == "/events/ingest":
        log_entry["event_count"] = getattr(request.state, "event_count", None)
    logger.info(json.dumps(log_entry))
    response.headers["X-Trace-Id"] = trace_id
    return response


def _db_error() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "service_unavailable",
            "message": "Database is temporarily unavailable.",
        },
    )


@app.post(
    "/events/ingest",
    response_model=IngestResponse,
    summary="Ingest event batch (max 500)",
    description=(
        "Request body must be `{\"events\": [ ... ]}` with 1–500 event objects. "
        "Do not send a single event at the top level — put it inside `events`. "
        "Idempotent by `event_id`. Staff rows are stored but excluded from analytics."
    ),
)
async def events_ingest(body: IngestRequest, request: Request):
    if not is_db_available():
        return _db_error()
    try:
        raw_events = [e.model_dump(mode="json") for e in body.events]
        request.state.event_count = len(raw_events)
        request.state.store_id = body.events[0].store_id if body.events else None
        return await run_in_threadpool(ingest_events_from_raw, raw_events)
    except HTTPException:
        raise
    except Exception as exc:
        from sqlalchemy.exc import SQLAlchemyError

        if isinstance(exc, SQLAlchemyError):
            set_db_unavailable()
            return _db_error()
        raise


@app.get(
    "/stores/{store_id}/metrics",
    summary="Store metrics (today, real-time)",
)
async def store_metrics(store_id: str):
    if not is_db_available():
        return _db_error()
    return await run_in_threadpool(get_store_metrics, store_id)


@app.get(
    "/stores/{store_id}/funnel",
    summary="Conversion funnel (session-based)",
)
async def store_funnel(store_id: str):
    if not is_db_available():
        return _db_error()
    return await run_in_threadpool(get_store_funnel, store_id)


@app.get(
    "/stores/{store_id}/heatmap",
    summary="Zone heatmap (0–100 normalized)",
)
async def store_heatmap(store_id: str):
    if not is_db_available():
        return _db_error()
    return await run_in_threadpool(get_store_heatmap, store_id)


@app.get(
    "/stores/{store_id}/anomalies",
    summary="Active anomalies",
)
async def store_anomalies(store_id: str):
    if not is_db_available():
        return _db_error()
    return await run_in_threadpool(get_store_anomalies, store_id)


@app.get("/stores/{store_id}/zones/counts")
async def zone_counts(store_id: str):
    """Entry/exit counts per zone for dashboard."""
    if not is_db_available():
        return _db_error()
    from app.metrics import get_zone_counts_with_areas

    return await run_in_threadpool(get_zone_counts_with_areas, store_id)


@app.get("/stores/{store_id}/live")
async def live_snapshot(store_id: str):
    """Single JSON snapshot for dashboard polling (works in all browsers)."""
    if not is_db_available():
        return _db_error()
    from app.live_stream import build_live_payload

    return await run_in_threadpool(build_live_payload, store_id)


@app.get("/stores/{store_id}/zones/stream")
async def zone_counts_stream(store_id: str):
    """SSE stream — zones only (legacy)."""
    if not is_db_available():
        return _db_error()
    from app.live_stream import zone_counts_sse

    return StreamingResponse(
        zone_counts_sse(store_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/stores/{store_id}/live/stream")
async def live_dashboard_stream(store_id: str):
    """SSE stream — full live dashboard (zones + funnel + events)."""
    if not is_db_available():
        return _db_error()
    from app.live_stream import live_dashboard_sse

    return StreamingResponse(
        live_dashboard_sse(store_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get(
    "/health",
    summary="Service health (on-call)",
    description="Per-store last event time; STALE_FEED warning if lag > 10 minutes.",
)
async def health():
    if not is_db_available():
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "stores": [],
                "warnings": ["database_unavailable"],
            },
        )
    return await run_in_threadpool(get_health)


@app.get("/layout")
async def layout():
    return json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))


def _serve_dashboard() -> HTMLResponse | FileResponse:
    index = DASHBOARD_DIR / "index.html"
    if not index.exists():
        return HTMLResponse(
            content=(
                "<!DOCTYPE html><html><body style='font-family:sans-serif;padding:2rem'>"
                "<h1>Dashboard file missing</h1>"
                f"<p>Expected: <code>{index}</code></p>"
                "<p>Start API from the project folder with:<br>"
                "<code>.\\start.ps1</code> or <code>uvicorn app.main:app --reload --port 8080</code></p>"
                "</body></html>"
            ),
            status_code=500,
        )
    return HTMLResponse(content=index.read_text(encoding="utf-8"))


@app.get("/")
@app.get("/dashboard")
async def dashboard():
    """Main UI — use /dashboard?store=STORE_2 if / shows JSON in your browser."""
    return _serve_dashboard()


@app.get("/dashboard/stream/{store_id}/{camera_id}")
async def stream_camera(store_id: str, camera_id: str):
    """MJPEG stream from CCTV clip (looped). Requires opencv in the image + video files on disk."""
    try:
        from pipeline.stream import generate_mjpeg_stream
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "streams_unavailable",
                "message": "CCTV streaming is not enabled in this API-only image.",
            },
        )

    return StreamingResponse(
        iterate_in_threadpool(generate_mjpeg_stream(store_id, camera_id)),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.post("/admin/reset-live/{store_id}")
async def reset_live(store_id: str):
    """Clear events + occupancy for a store before a live demo."""
    if not is_db_available():
        return _db_error()
    from app.db import EventRow, ZoneCountRow
    from app.occupancy import reset_store_occupancy

    with get_session() as session:
        session.query(EventRow).filter_by(store_id=store_id).delete()
        session.query(ZoneCountRow).filter_by(store_id=store_id).delete()
        reset_store_occupancy(session, store_id)
    return {"store_id": store_id, "status": "reset"}


@app.post("/admin/reload-pos")
async def reload_pos():
    if POS_PATH.exists():
        txns = load_pos_csv(POS_PATH, store_id_map={"ST1008": "STORE_1"})
        n = ingest_pos_transactions(txns)
        return {"loaded": n}
    return {"loaded": 0}
