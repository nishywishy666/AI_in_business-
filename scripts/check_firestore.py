"""Read the Firestore trees this app writes and print what is actually there.

The Firebase console is a separate client with its own auth, its own network path and its own
extensions to be blocked by — "Error loading documents" there tells you nothing about whether your
data exists or whether the app can reach it. This asks the same question with the app's own
credentials and prints counts per collection.

    uv run python scripts/check_firestore.py
    uv run python scripts/check_firestore.py --business-id uncle_tony --limit 3

Needs FIREBASE_SA_JSON (or FIRESTORE_EMULATOR_HOST) in the environment, exactly as the app does.
Read-only: it never writes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from services.common.config import get_config  # noqa: E402
from services.common.firestore import BusinessPaths  # noqa: E402

# businesses/{id}/… — the voice tree (vr_plan.md §11)
VOICE_COLLECTIONS = ("calls", "bookings", "callbacks", "menuItems", "facts", "capacitySlots", "usageEvents")
CAP = 200  # enough to tell "empty" from "populated" without paging a whole call log


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--business-id", default=None, help="defaults to BUSINESS_ID from the environment")
    ap.add_argument("--limit", type=int, default=2, help="sample documents to print per collection")
    args = ap.parse_args()

    try:
        config = get_config()
    except Exception as exc:
        print(f"Could not read the environment: {type(exc).__name__}: {exc}")
        return 2
    business_id = args.business_id or config.business_id
    print(f"project   : {config.firebase_project_id or '(none in env)'}")
    print(f"business  : {business_id}")
    print(f"sink mode : {config.session_sink}")
    if config.session_sink != "firestore":
        print("\n! SESSION_SINK is not 'firestore', so this app instance writes call data to .testruns/,")
        print("  not to Firestore. Nothing new will appear in the console while it runs this way.")

    try:
        from services.common.firestore import make_client

        client = make_client(config)
    except Exception as exc:
        print(f"\nFAILED to build a Firestore client: {type(exc).__name__}: {exc}")
        print("That is a credentials problem (FIREBASE_SA_JSON), not a data problem.")
        return 2

    paths = BusinessPaths(business_id)
    print(f"\n{paths.root}")
    total = 0
    for name in VOICE_COLLECTIONS:
        path = f"{paths.root}/{name}"
        try:  # a plain read, capped, rather than an aggregation the emulator may not serve
            docs = list(client.collection(path).limit(CAP).stream())
        except Exception as exc:
            print(f"  {name:<14} ERROR  {type(exc).__name__}: {str(exc)[:120]}")
            continue
        count = len(docs)
        total += count
        more = "+" if count == CAP else ""
        print(f"  {name:<14} {count:>5}{more} document{'s' if count != 1 else ''}")
        for doc in docs[: args.limit]:
            keys = ", ".join(sorted(doc.to_dict() or {})[:6])
            print(f"    · {doc.id}  [{keys}]")

    print(f"\n{total} documents under {paths.root}.")
    if total == 0:
        print("Empty. Seed it with:  uv run python scripts/seed_business.py")
        print("and, for demo calls:  uv run python scripts/seed_demo_calls.py --sink firestore")
    else:
        print("The data is there and reachable with the app's credentials. If the Firebase console")
        print("still says 'Error loading documents', that is the console's own connection —")
        print("try a private window with extensions off, and check you are in the right project.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
