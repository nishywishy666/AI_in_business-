#!/usr/bin/env python3
"""Materialise capacitySlots for the next CAPACITY_SEED_DAYS from config/capacity.yaml (vr_plan.md §8.3).

    uv run python scripts/seed_business.py --dry-run
    uv run python scripts/seed_business.py            # writes to the configured Firestore project

Existing slot documents are never overwritten (seatsBooked is live data). Menu items and facts are
NOT seeded here — that data is owned by the dashboard (plan 0004).
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import load_yaml, threshold  # noqa: E402
from services.booking.capacity import materialise_slots, parse_windows  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days", type=int, default=int(threshold("CAPACITY_SEED_DAYS")))
    args = parser.parse_args()

    windows = parse_windows(load_yaml("capacity.yaml").get("windows"))
    if not windows:
        sys.exit("config/capacity.yaml has no windows — TODO(spec): fill in service windows and seat counts first")

    from services.common.config import get_config

    config = get_config()
    today = dt.datetime.now(ZoneInfo(config.tz_business)).date()
    docs = materialise_slots(windows, today, args.days, config.business_id)
    print(f"{len(docs)} slots over {args.days} days from {today} for {config.business_id}")
    if args.dry_run:
        for slot_id in list(docs)[:5]:
            print("  ", slot_id, docs[slot_id])
        return 0

    from google.api_core.exceptions import AlreadyExists

    from services.common.firestore import BusinessPaths, make_client

    client = make_client(config)
    paths = BusinessPaths(config.business_id)
    created = skipped = 0
    for slot_id, doc in docs.items():
        try:
            client.document(paths.capacity_slot(slot_id)).create(doc)
            created += 1
        except AlreadyExists:
            skipped += 1
    print(f"created {created}, kept {skipped} existing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
