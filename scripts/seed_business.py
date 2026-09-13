#!/usr/bin/env python3
"""Seed the business tree in Firestore (vr_plan.md §8.3, §11; plan 0005).

    uv run python scripts/seed_business.py --dry-run
    uv run python scripts/seed_business.py            # writes to the configured Firestore project

Writes, in order:
  1. capacitySlots for the next CAPACITY_SEED_DAYS from config/capacity.yaml — existing slot
     documents are never overwritten (seatsBooked is live data);
  2. menuItems + facts from data/business/<BUSINESS_DATASET>/ (set(merge=True), so owner edits made
     in Firestore survive; `_readme` keys are skipped);
  3. the marketing questionnaire at users/{MARKETING_USER_ID or BUSINESS_ID}/context/questionnaire
     (lesson 0001: the context path is a collection) — only when no document exists there yet.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import load_yaml, threshold  # noqa: E402
from services.booking.capacity import materialise_slots, parse_windows  # noqa: E402


def _dataset(name: str) -> tuple[dict, dict, dict | None]:
    folder = Path(__file__).resolve().parents[1] / "data" / "business" / name
    if not folder.exists():
        sys.exit(f"no dataset at {folder}")

    def load(file: str) -> dict:
        path = folder / file
        if not path.exists():
            return {}
        return {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if not str(k).startswith("_")}

    context = folder / "marketing_context.json"
    return load("menu_items.json"), load("facts.json"), (json.loads(context.read_text(encoding="utf-8")) if context.exists() else None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days", type=int, default=int(threshold("CAPACITY_SEED_DAYS")))
    parser.add_argument("--dataset", default=None, help="folder under data/business (default: BUSINESS_DATASET or uncle_tony)")
    args = parser.parse_args()

    windows = parse_windows(load_yaml("capacity.yaml").get("windows"))
    if not windows:
        sys.exit("config/capacity.yaml has no windows — TODO(spec): fill in service windows and seat counts first")

    from dotenv import load_dotenv

    load_dotenv()
    from dashboard.settings import get_settings
    from services.common.config import get_config

    config = get_config()
    dataset_name = args.dataset or get_settings().business_dataset
    menu, facts, context = _dataset(dataset_name)
    today = dt.datetime.now(ZoneInfo(config.tz_business)).date()
    docs = materialise_slots(windows, today, args.days, config.business_id)
    print(f"{len(docs)} slots over {args.days} days from {today}, {len(menu)} menu items, {len(facts)} facts "
          f"(dataset {dataset_name}) for {config.business_id}")
    if args.dry_run:
        for slot_id in list(docs)[:5]:
            print("  ", slot_id, docs[slot_id])
        for item_id, doc in list(menu.items())[:3]:
            print("  menuItems/", item_id, doc.get("name"), doc.get("priceCents"))
        return 0
    if config.session_sink == "local":
        sys.exit("SESSION_SINK=local: nothing to seed — the local reader loads data/business/<dataset> directly")

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
    print(f"capacitySlots: created {created}, kept {skipped} existing")
    for item_id, doc in menu.items():
        client.document(paths.menu_item(item_id)).set(doc, merge=True)
    for key, doc in facts.items():
        client.document(paths.fact(key)).set(doc, merge=True)
    print(f"menuItems: {len(menu)} merged · facts: {len(facts)} merged")
    if context is not None:
        import os

        user_id = os.environ.get("MARKETING_USER_ID") or config.business_id
        template = os.environ.get("CONTEXT_PATH") or "users/{userId}/context"
        path = template.replace("{userId}", user_id).replace("{uid}", user_id)
        if len(path.split("/")) % 2 == 1:
            path = f"{path}/questionnaire"
        existing = list(client.collection(path.rsplit("/", 1)[0]).limit(1).stream())
        if existing:
            print(f"marketing context: kept existing document under {path.rsplit('/', 1)[0]}")
        else:
            client.document(path).set(context)
            print(f"marketing context: written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
