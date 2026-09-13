#!/usr/bin/env python3
"""Replay a recorded simulator call's caller turns through Mode A (vr_plan.md §12.4).

    uv run python scripts/replay.py .testruns/CAsim0123.jsonl
    uv run python scripts/replay.py .testruns/CAsim0123.jsonl --base-url http://localhost:8000

Without --base-url the production TurnEngine is driven in-process (SESSION_SINK is forced to
local, so the replay itself is recorded under a new CAsim id).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def caller_turns(path: Path) -> list[str]:
    turns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") == "turn" and (record.get("doc") or {}).get("speaker") == "caller":
            turns.append(record["doc"]["textFinal"])
    return turns


async def replay_in_process(turns: list[str]) -> None:
    os.environ["SESSION_SINK"] = "local"
    from api.index import build_deps
    from api.sim.app import SimSessions
    from services.common.config import get_config

    config = get_config()
    deps = build_deps(config)
    sessions = SimSessions(deps, config.business_id)
    session, recorder = sessions.get_or_create(None, None)
    print(f"replaying {len(turns)} turns as {session.call_id}")
    for text in turns:
        result = await deps.engine.run_turn(session, text, recorder=recorder)
        print(json.dumps({"caller": text, "intent": result.intent, "confidence": result.confidence,
                          "tool": result.tool_called, "reply": result.reply_text, "ms": result.timings_ms,
                          "warnings": result.warnings}, ensure_ascii=False))


def replay_via_http(turns: list[str], base_url: str) -> None:
    import httpx

    call_id = None
    with httpx.Client(base_url=base_url, timeout=30) as client:
        for text in turns:
            response = client.post("/sim/text", json={"text": text, "call_id": call_id})
            response.raise_for_status()
            body = response.json()
            call_id = body["call_id"]
            print(json.dumps({"caller": text, "intent": body["intent"], "confidence": body["confidence"],
                              "tool": body["tool_called"], "reply": body["reply_text"], "ms": body["timings_ms"],
                              "warnings": body["warnings"]}, ensure_ascii=False))
    print(f"replayed as {call_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--base-url", help="running simulator (ENABLE_SIM=1); default: in-process")
    args = parser.parse_args()
    turns = caller_turns(args.jsonl)
    if not turns:
        sys.exit(f"no caller turns found in {args.jsonl}")
    if args.base_url:
        replay_via_http(turns, args.base_url)
    else:
        asyncio.run(replay_in_process(turns))
    return 0


if __name__ == "__main__":
    sys.exit(main())
