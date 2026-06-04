Store Intelligence - Design

Overview

This system turns anonymised in-store CCTV into structured behavioural events (PDF schema), stores them in SQLite, and serves real-time intelligence through a REST API and a live dashboard. POS transactions are loaded separately and correlated to visitors for conversion metrics. We do not use customer IDs from the POS file.

Flow:

  CCTV clips in store_1/ and store_2/
  -> Detection pipeline (pipeline/detect.py): YOLOv8n, staff filter, IoU tracker, ROI rules per camera
  -> JSONL events in PDF schema
  -> POST /events/ingest (idempotent) into SQLite (events, zone_presence, POS)
  -> GET metrics, funnel, heatmap, anomalies, health
  -> Dashboard polls /live; optional MJPEG streams

Store layout and IDs

  store_1 footage -> STORE_1 (acceptance gate; POS ST1008 maps to STORE_1)
  store_2 footage -> STORE_2 (primary demo for live dashboard)

Zones and camera ROIs are in store_layout.json (normalized x1,y1,x2,y2 per camera). STORE_2 uses simple names (Entrance, Zone 01-06, Billing queue/checkout). Three areas roll up for the UI: Entrance, Main area (all floor zones), Billing area.

Detection layer

  Person detector: YOLOv8n (COCO class 0), about 2 FPS sample rate
  Tracking: SimpleTracker with IoU match; visitor_id like VIS_000001
  Entry cameras: crossing line -> ENTRY / EXIT
  Floor cameras: centroid in ROI -> ZONE_ENTER, ZONE_EXIT, ZONE_DWELL (30s)
  Billing cameras: queue ROI -> BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON
  Staff: dress-code rule - if 2 or more people share similar upper-body HSV in one frame, treat as staff (no events)
  Cross-camera IDs: visitor pool - floor and billing only reuse IDs that already have ENTRY
  Re-entry: not emitted by detector; REENTRY supported in API but excluded from funnel

No VLM in the pipeline. Zone names come from layout PNGs and manual ROI mapping (see CHOICES.md).

Intelligence API

  Stack: FastAPI, SQLAlchemy, SQLite (DATABASE_URL can point to Postgres later)
  Ingest: POST /events/ingest with events array (max 500); per-event validation; partial success; idempotent on event_id
  Live occupancy: zone_presence table; area counts use distinct visitors, not summed zones
  Analytics: staff and REENTRY filtered out; floor/billing need prior ENTRY for same visitor_id
  Logging: JSON per request with trace_id, store_id, endpoint, latency_ms, event_count, status_code
  Degradation: DB unavailable returns HTTP 503 JSON; no stack traces in responses

Dashboard

  /dashboard?store=STORE_2 polls GET /stores/{id}/live about every 800ms
  Three area cards (entrance, main floor, billing) plus zone detail table
  CCTV MJPEG on demand so API stays responsive
  pipeline/feed_live.py replays JSONL for demos; pipeline/reload_store2.py bulk-loads after detect

Docker

  docker compose up --build starts the API on port 8080, mounts data/, read-only video folders, and sample POS CSV. No manual DB migration.

AI-Assisted Decisions

1. Zone ROIs without camera calibration

What the LLM suggested: Draw ROIs from layout PNGs using normalized coordinates per camera, or use a VLM to label zones from a single frame per store.

What I did: Manual ROIs in store_layout.json from the floor-plan PNGs, one rectangle per brand zone. No homography and no VLM calls.

Agree or override: Agreed for a take-home timeline. Would override in production with homography from floor plans plus a one-time calibration tool. Overlapping ROIs caused duplicate zone enters until we added one zone at a time per track.

2. Staff detection

What the LLM suggested: A small CNN staff classifier, or a VLM prompt to list employees in uniform. Later: upper-body colour variance only.

What I chose: Rule-based dress code - quantize upper-body HSV into a signature; if 2 or more people share a similar signature in the same frame, treat as staff and drop before tracking. No VLM.

Agree or override: Overrode the pure variance heuristic (too many false staff/customer swaps). The dress-code rule is still fragile on busy days but is explainable and testable. Production would use a fine-tuned staff detector or staff badges on Re-ID.

3. POS conversion without customer_id

What the LLM suggested: Link visitor_id to order_id when possible.

What I did: Rejected - PDF POS sample has no customer_id. Conversion means visitor had a billing-zone event within 5 minutes before a POS timestamp.

Agree or override: Agreed with the brief. Window-based correlation is honest about data limits and keeps zero-purchase stores at 0% conversion correctly.
