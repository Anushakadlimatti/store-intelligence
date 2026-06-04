# Store Intelligence

Offline retail analytics: CCTV → YOLOv8 detection → PDF-schema events → REST API → live dashboard.

## Production readiness (Part C)

| Requirement | Implementation |
|-------------|----------------|
| **Containerised** | `docker compose up --build` — API + SQLite volume, no manual DB setup |
| **Structured logging** | JSON per request: `trace_id`, `store_id`, `endpoint`, `latency_ms`, `event_count` (ingest), `status_code` |
| **Idempotency** | `POST /events/ingest` dedupes by `event_id` — see `tests/test_metrics.py` |
| **Graceful degradation** | DB down → HTTP **503** `{"error":"service_unavailable",...}` — no stack traces in responses |
| **Tests** | `pytest` with **>70%** statement coverage on `app/` (enforced in `pytest.ini`) |
| **README** | This file — 5-command setup + detection → API pipeline |

---

## Quick start (5 commands)

After `git clone`:

```bash
cd store-intelligence2
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
docker compose up --build
```

API: **http://localhost:8080**  
Dashboard: **http://localhost:8080/dashboard?store=STORE_2**  
OpenAPI: **http://localhost:8080/docs**

No manual database migration — the container creates `data/store_intelligence.db` on first start and loads sample POS into `STORE_1`.

### Git Bash on Windows

```bash
bash start.sh
# or
docker compose up --build
```

---

## Run detection on CCTV clips → feed API

Detection runs **on the host** (needs GPU/CPU + video files under `store_1/` and `store_2/`). Output is PDF-schema JSONL; ingest via API.

### 1. Generate events from clips

```bash
# One command (detect STORE_2 + reload DB)
bash pipeline/run.sh store2 --fast

# Or step by step
python -m pipeline.detect --store STORE_2 --output output/store2_events.jsonl --fast
python -m pipeline.reload_store2
```

Or ingest directly while detecting:

```bash
python -m pipeline.detect --store STORE_2 --api-url http://127.0.0.1:8080
```

### 2. Bulk reload into API (SQLite)

```bash
bash pipeline/run.sh reload
```

### 3. Live pipeline (detection + API connected, STORE_2)

Runs YOLO on CCTV clips in simulated real time: each detected event is POSTed to the API immediately (not batch-only).

```bash
# Terminal 1: API
bash start.sh

# Terminal 2a: cameras in order (entry -> floor -> billing)
python -m pipeline.live_detect --store STORE_2 --reset --mode sequential --fast

# Terminal 2b: all cameras at once
python -m pipeline.live_detect --store STORE_2 --reset --mode simultaneous --fast
```

Dashboard: `?cameras=sequential` or `?cameras=simultaneous` for CCTV layout.

While the script runs, metrics update live. Use **View** on the dashboard to switch all cameras vs one at a time.

Options: omit `--fast` for full clips; add `--no-playback` for faster ingest.

### 4. Simulated live feed from JSONL (replay pre-generated events)

```bash
python -m pipeline.feed_live --input output/store2_events.jsonl --store STORE_2 --reset --interval 1.5
```

Use this when you already ran detection and only want to replay events into the API.

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/events/ingest` | Batch `{"events":[...]}` max 500, idempotent by `event_id` |
| GET | `/stores/{id}/metrics` | Visitors, conversion, dwell, queue, abandonment |
| GET | `/stores/{id}/funnel` | Entry → Zone Visit → Billing Queue → Purchase |
| GET | `/stores/{id}/heatmap` | Zone heatmap 0–100 + `data_confidence` |
| GET | `/stores/{id}/anomalies` | Queue spike, conversion drop, dead zones |
| GET | `/health` | On-call status + `STALE_FEED` if lag > 10 min |
| GET | `/dashboard` | Live web UI |

Full schema: **`docs/API.md`**

### Ingest example (Swagger / curl)

```bash
curl -X POST http://localhost:8080/events/ingest \
  -H "Content-Type: application/json" \
  -d '{"events":[{"event_id":"7afbac7c-8158-4b94-946b-4413cb5b653f","store_id":"STORE_1","camera_id":"CAM_ENTRY_01","visitor_id":"VIS_000011","event_type":"ENTRY","timestamp":"2026-04-10T12:00:30Z","zone_id":"ENTRANCE","dwell_ms":0,"is_staff":false,"confidence":0.9,"metadata":{"queue_depth":null,"sku_zone":"ENTRANCE","session_seq":1}}]}'
```

---

## Tests & coverage

```bash
export PYTHONPATH=.
pytest
```

Coverage threshold: **70%** on `app/` (see `pytest.ini`). Tests: `test_pipeline.py`, `test_metrics.py`, `test_anomalies.py`.

- Idempotent ingest (same payload twice)
- 503 when DB unavailable
- No stack traces in API responses
- Structured ingest logs
- All-staff clip → 0 visitors
- Zero purchases → 0% conversion
- REENTRY not double-counted in funnel
- Empty store

---

## Store IDs

| ID | Source |
|----|--------|
| `STORE_1` | `store_1/` footage; POS `ST1008` mapped on startup |
| `STORE_2` | `store_2/` footage |

---

## Local dev (without Docker)

```bash
export PYTHONPATH=.
export PORT=8080
bash start.sh
```

---

## Project layout

```
pipeline/
  detect.py            # Main detection + tracking
  tracker.py           # IoU tracker
  emit.py              # PDF event schema + emission
  run.sh               # One command: detect / live / feed / reload / store2
  live_detect.py       # Live detect + API ingest
  feed_live.py         # Replay JSONL to API
  reload_store2.py     # Load JSONL into DB
  layout.py, staff.py, stream.py, visitor_pool.py
app/
  main.py              # FastAPI entrypoint
  models.py, ingestion.py, metrics.py, funnel.py, anomalies.py, health.py
  (+ db, filters, occupancy, dashboard helpers)
tests/
  test_pipeline.py
  test_metrics.py
  test_anomalies.py
docs/
  DESIGN.md, CHOICES.md, API.md
dashboard/static/      # Live web UI
store_layout.json      # Zones, cameras, ROIs
store_1/, store_2/     # CCTV clips
docker-compose.yml
README.md
```

## Deploy (public demo URL)

Deploy to **[Render](https://render.com)** (free tier, Docker):

1. Push this repo to GitHub (include `seed/store2_events.jsonl` — committed for cloud demo data).
2. Render → **New** → **Blueprint** (or Web Service) → connect the repo.
3. Render detects `render.yaml` and builds the `Dockerfile`. First boot auto-loads **STORE_2** from `seed/`.
4. Open: `https://<your-service>.onrender.com/dashboard?store=STORE_2`  
   Health: `https://<your-service>.onrender.com/health`

Free tier may sleep after ~15 min idle; first visit can take 30–60s to wake. CCTV MJPEG streams need local `store_2/` video files; metrics/funnel/heatmap work on Render without videos.

---

## Docs

- `docs/Store_Intelligence_Run_Guide.docx` — setup, run steps, dashboard links (Word)  
- `docs/Store_Intelligence_Pitch.pptx` — audience pitch deck (27 slides, 16:9, branded)  
- `docs/Store_Intelligence_Presentation.pptx` — same deck (close file in PowerPoint, then run `python scripts/build_presentation.py` to refresh)  
- `docs/API.md` — request/response reference  
- `docs/DESIGN.md` — architecture  
- `docs/CHOICES.md` — design decisions  
