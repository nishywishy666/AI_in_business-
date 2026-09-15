#!/usr/bin/env python3
"""Copy businesses/{id}/facts OUT of Firestore into data/business/<dataset>/facts.json.

    uv run python scripts/pull_facts.py --dry-run
    uv run python scripts/pull_facts.py

`seed_business.py` only ever pushes the local dataset INTO Firestore, so facts added or edited in
the Firestore console are invisible to everything that reads the local dataset — which is the
offline dashboard, the /sim simulator and the pytest suite. That drift is silent: the console looks
full while the simulator answers "I'll have the owner call you back".

Existing local entries are preserved unless --overwrite is passed; provenance keys written by the
public-data reconciliation (source, sourceUrl, sourceNote, confirmedByOwner) are carried across.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

KEEP = ("value", "source", "sourceUrl", "sourceNote", "confirmedByOwner",
        "publiclyVerifiedAt", "publicSourceUrl")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true", help="let Firestore win for keys that already exist locally")
    parser.add_argument("--dataset", default=None, help="folder under data/business (default: BUSINESS_DATASET)")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()
    from dashboard.settings import get_settings
    from services.common.config import get_config
    from services.common.firestore import make_client

    config = get_config()
    folder = (Path(__file__).resolve().parents[1] / "data" / "business" / args.dataset) if args.dataset \
        else get_settings().dataset_dir
    target = folder / "facts.json"
    if not target.exists():
        sys.exit(f"no dataset facts file at {target}")
    local = json.loads(target.read_text(encoding="utf-8"))

    client = make_client(config)
    remote = {d.id: (d.to_dict() or {}) for d in
              client.collection("businesses").document(config.business_id).collection("facts").stream()}
    if not remote:
        sys.exit("Firestore has no facts for this business — nothing to pull")

    added, updated, skipped = [], [], []
    for key, doc in sorted(remote.items()):
        if not doc.get("value"):
            continue
        entry = {k: doc[k] for k in KEEP if doc.get(k) is not None}
        if key not in local:
            local[key] = entry
            added.append(key)
        elif args.overwrite and local[key] != entry:
            local[key] = entry
            updated.append(key)
        else:
            skipped.append(key)

    print(f"added   ({len(added)}): {', '.join(added) or '-'}")
    print(f"updated ({len(updated)}): {', '.join(updated) or '-'}")
    print(f"kept    ({len(skipped)}): {', '.join(skipped) or '-'}")
    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0
    target.write_text(json.dumps(local, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {target} ({len([k for k in local if not k.startswith('_')])} facts)")
    print("Remember: a fact still needs a synonym in config/fact_synonyms.yaml to be answerable (lesson 0011).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
