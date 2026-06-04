# Store Intelligence API

Base URL: `http://127.0.0.1:8080` (default; set `PORT` env var)

Interactive docs: `/docs`

## Event schema (pipeline output)

All events must match this JSON shape (see `app/models.py`):

| Field | Type | Notes |
|-------|------|--------|
| `event_id` | UUID v4 | Globally unique; idempotent ingest key |
| `store_id` | string | e.g. `STORE_1`, `STORE_2` from `store_layout.json` |
| `camera_id` | string | e.g. `CAM_ENTRY_01` |
| `visitor_id` | string | Re-ID token per visit session |
| `event_type` | enum | See catalogue below |
| `timestamp` | ISO-8601 UTC | Clip start + frame offset |
| `zone_id` | string \| null | From `store_layout.json` |
| `dwell_ms` | int | 0 for instantaneous events |
| `is_staff` | bool | Excluded from analytics when `true` |
| `confidence` | float 0–1 | Do not drop low-confidence events |
| `metadata` | object | `queue_depth`, `sku_zone`, `session_seq` |

### Event type catalogue

| Type | When to emit |
|------|----------------|
| `ENTRY` | Visitor crosses entry line inbound |
| `EXIT` | Visitor crosses entry line outbound |
| `ZONE_ENTER` | Enters named zone |
| `ZONE_EXIT` | Leaves named zone |
| `ZONE_DWELL` | 30+ seconds continuous dwell (repeat every 30s) |
| `BILLING_QUEUE_JOIN` | Enters billing zone; set `metadata.queue_depth` |
| `BILLING_QUEUE_ABANDON` | Leaves billing without POS purchase |
| `REENTRY` | Same `visitor_id` after prior `EXIT` |

---

## POST `/events/ingest`

- **Body:** `{ "events": [ { ...event }, ... ] }` — max **500** events  
  (a **single event object** without the wrapper also works for testing)
- **Validates** each event individually (Pydantic / PDF schema)
- **Idempotent** on `event_id` (re-post counts as accepted, no duplicate row)
- **Partial success:** malformed events → `errors[]` with `index`, `event_id`, `message`
- **Stores** staff and REENTRY rows; analytics endpoints exclude staff / REENTRY rules

**Response:**

```json
{
  "accepted": 42,
  "rejected": 2,
  "errors": [
    { "index": 3, "event_id": "bad-id", "message": "..." }
  ]
}
```

---

## GET `/stores/{store_id}/metrics`

Today’s metrics (real-time from DB, not yesterday’s cache):

| Field | Description |
|-------|-------------|
| `unique_visitors` | Distinct customers with `ENTRY` (no staff, no REENTRY double-count) |
| `conversion_rate` | Visitors with POS-correlated purchase / unique visitors |
| `avg_dwell_by_zone` | Per-zone entries, exits, avg dwell ms |
| `queue_depth` | Max `metadata.queue_depth` from joins + live billing queue occupancy |
| `abandonment_rate` | Abandons / queue joins |
| `as_of` | Server timestamp |

Zero-purchase stores: `conversion_rate` = 0.

---

## GET `/stores/{store_id}/funnel`

Session-based funnel (not raw event counts):

1. **Entry** — unique visitors with first `ENTRY`
2. **Zone Visit** — session reached any zone
3. **Billing Queue** — session reached billing
4. **Purchase** — POS correlation within window

Each stage includes `count` and `drop_off_pct` vs previous stage. REENTRY does not add a second session.

---

## GET `/stores/{store_id}/heatmap`

- `zones[]`: `visit_frequency` and `avg_dwell_normalized` (0–100) per layout zone
- `data_confidence`: `"HIGH"` if ≥ 20 sessions today, else `"LOW"`

---

## GET `/stores/{store_id}/anomalies`

Active anomalies with `severity` (`INFO` | `WARN` | `CRITICAL`) and `suggested_action`:

- `BILLING_QUEUE_SPIKE`
- `CONVERSION_DROP` (vs 7-day average)
- `DEAD_ZONE` (no visits 30+ min or zero visits today)

---

## GET `/health`

On-call endpoint:

- `status`: `ok` | `degraded` | `unavailable`
- `stores[]`: `store_id`, `last_event_at`, `status` (`ok` | `stale` | `no_data`)
- `warnings[]`: includes `STALE_FEED:{store_id}` if last event **> 10 minutes** ago

---

## Additional endpoints (dashboard)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/stores/{store_id}/zones/counts` | Per-zone + area rollups |
| GET | `/stores/{store_id}/live` | Live snapshot for polling |
| GET | `/layout` | `store_layout.json` |
| GET | `/dashboard` | Web UI |
