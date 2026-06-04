CHOICES.md

Four design decisions for the Store Intelligence take-home: what was considered, what AI suggested, and what was implemented.


1. Detection model: YOLOv8n (not a VLM)

Options considered

  YOLOv8n - small weights, Ultralytics API, person class, runs on CPU. Weak on crowds and occlusion, no semantics.
  YOLOv8s/m - better accuracy, slower on laptop CPU.
  RT-DETR / YOLO11 - strong accuracy, heavier setup.
  VLM per frame (GPT-4V, Gemini) - can describe who entered a zone. Cost, latency, non-deterministic, bad for batch CCTV.
  VLM for zones only - auto-label ROIs from layout photo. Needs prompts and validation per store.

What AI suggested

  Default YOLOv8n at 2 FPS with ByteTrack for IDs.
  Optional VLM to classify zone from crop or flag staff in uniform.

Example VLM prompt AI proposed (not implemented):

  You are reviewing a retail CCTV frame. List each visible person as JSON with role customer or staff, zone name, and action entering browsing or queuing. Use only zone names from the provided list.

What I chose

  YOLOv8n plus OpenCV geometry plus IoU tracker. No VLM anywhere.

  Zones: fixed ROIs from store_layout.json, not VLM classification.
  Staff: HSV dress-code clustering in pipeline/staff.py - if 2 or more detections share similar upper-body colour in one frame, treat as staff.
  Sampling: about 2 FPS (--sample-fps) to keep batch runtime acceptable on CPU.

Why

  The brief needs deterministic PDF-schema events at scale. VLMs are a poor fit for batch replay on many CCTV files.
  No API keys or GPU required for an offline docker-friendly submission.
  YOLO gives boxes; ROI rules turn boxes into ZONE_ENTER and similar events with auditable logic.

VLM evaluation (not used)

  I did not run a VLM in the final pipeline, so there is no production accuracy number.

  In exploration, frame-level VLMs could help zone naming from layout PNGs but would not give stable visitor_id or 30s dwell without heavy post-processing.
  For staff, a VLM prompt works on clear uniform photos but fails when customers wear similar colours or staff wear plain clothes - similar to our HSV rule.

  Conclusion: VLM is useful for one-off ROI setup. Detection plus tracking plus rules is the right core for this deliverable.


2. Event schema design

Options considered

  PDF schema only - matches scoring and brief. Ignores legacy sample JSONL shape.
  Normalise sample_eventsbe42122.jsonl - familiar fields but out of scope.
  Extra fields (track_id, age, gender) - good for debugging but out of contract.

What AI suggested

  Add track_id, frame_number, optional demographics.
  Lower confidence threshold and drop events below 0.5.

What I chose

  Strict PDF schema in app/models.py and pipeline/emit.py:

  event_id: UUID v4 for idempotent ingest
  visitor_id: session token from tracker, not global Re-ID across days
  event_type: catalogue enum (ENTRY, ZONE_DWELL, BILLING_QUEUE_JOIN, etc.)
  metadata.queue_depth on billing joins; sku_zone from layout; session_seq ordinal
  Do not suppress low-confidence events in the emitter; API stores them and analytics can filter later

Why

  Ingest validation and acceptance tests target one contract.
  Sample JSONL was reference only for domain language, not the wire format.


3. API architecture: SQLite, session analytics, graceful ops

Options considered

  SQLite plus SQLAlchemy - docker compose up with no extra services. Write contention at many stores.
  Postgres plus Redis streams - scales better, more moving parts for take-home.
  In-memory store - fast but no idempotency across restarts.
  Pre-aggregated metrics only - fast reads but stale and hard to debug.

What AI suggested

  Postgres plus Redis or Kafka for ingest.
  Cache metrics in Redis with TTL.
  Separate ingest and query microservices.

What I chose

  Single FastAPI app plus SQLite file under ./data/
  Idempotent ingest on event_id; partial success with errors array per bad row
  Session-based funnel (unique visitors with ENTRY, not raw event counts)
  zone_presence for live in-zone-now counts; area rollups count distinct visitors
  Structured JSON logging per request; 503 when DB down; over 70% test coverage on app/
  Thread pool for sync DB and MJPEG so the event loop is not blocked

Why

  Matches clone, docker compose up, works with no Redis or Postgres setup.
  Clear path to Postgres: change DATABASE_URL, keep same SQLAlchemy models.
  Operators can grep JSON logs by trace_id without distributed tracing in v1.


4. Production Docker image: API-only split

Options considered

  Single requirements.txt in Docker - includes ultralytics/PyTorch/OpenCV; image can exceed 9 GB with ML stack and CCTV copies.
  Multi-stage copy of app/ only - smaller but still pulls ML if requirements unchanged.
  Split requirements-api.txt vs requirements-ml.txt - API image slim; ML on host for detection.

What AI suggested

  One Dockerfile with full pip install -r requirements.txt for simplicity.

What I chose

  requirements-api.txt for the production Dockerfile (FastAPI, SQLAlchemy, uvicorn only).
  requirements-ml.txt for local pip install when running pipeline/detect.py.
  requirements-dev.txt for pytest; not installed in the cloud image.
  .dockerignore for store_1/, store_2/, and *.mp4 so footage is never COPY'd into the image.
  docker-compose build arg WITH_OPENCV=true for local MJPEG only; cloud deploy leaves it false.
  POST /dashboard/stream/... returns 503 in API-only images (streams_unavailable).

Why

  Cloud hosts (Render free tier) need a small pull and fast cold start; detection does not run in the API container.
  docker compose on a laptop still mounts CCTV and can enable OpenCV for MJPEG without shipping YOLO to production.
  Demo data is loaded explicitly via feed_live against seed/store2_events.jsonl (or detect on host), not auto-baked into SQLite on boot.


Store ID mapping

  STORE_1 - store_1 videos
  STORE_2 - store_2 videos
  POS ST1008 maps to STORE_1 in load_pos_csv store_id_map

Change mapping in app/ingestion.py if your POS uses different store codes.
