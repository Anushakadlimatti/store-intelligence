"""
Reload Store 2 events into the API database (customer-only, no re-entry/staff).
Run with API stopped or running — then refresh the dashboard.

  python -m pipeline.reload_store2
"""
from __future__ import annotations

from app.bootstrap import reload_store2_events


def main() -> None:
    summary = reload_store2_events()
    print(f"Cleared old {summary['store_id']} rows, ingested {summary['accepted']} customer events")
    print(f"Skipped (staff/re-entry/no-entry): {summary['skipped']}")
    print(f"Unique visitors: {summary['unique_visitors']}")
    print(f"Zones with data: {summary['zones_with_data']}")


if __name__ == "__main__":
    main()
