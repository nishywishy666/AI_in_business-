"""Developer harness: run the pipeline from a terminal. Not a server, not the product.

    python -m marketing_radar.cli --offline scan --user-id demo
    python -m marketing_radar.cli --offline recap --user-id demo
    python -m marketing_radar.cli --offline brief --user-id demo
    python -m marketing_radar.cli --offline stats --user-id demo
    python -m marketing_radar.cli --offline daily-pull --user-id demo
    python -m marketing_radar.cli --offline like <post_id> --user-id demo
    python -m marketing_radar.cli --offline angle <post_id> <0|1|2> --user-id demo
    python -m marketing_radar.cli --offline save <script_id> --user-id demo
    python -m marketing_radar.cli --offline scripts --user-id demo
    python -m marketing_radar.cli --offline chat "what should I post tomorrow?" --user-id demo
    python -m marketing_radar.cli --offline overlord --user-id demo
    python -m marketing_radar.cli --offline expire --user-id demo
    python -m marketing_radar.cli --offline reset --user-id demo

Offline mode uses a JSON-file Firestore stand-in under the cache directory plus recorded fixtures,
and seeds the parent context from tests/fixtures/context_map.json. Zero credits are spent.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings
from .deps import DEFAULT_FIXTURES, build_deps, offline_backend, offline_settings
from .agent.chat import ChatSession
from .agent.like import choose_angle, like_post
from .jobs.daily_pull import daily_pull
from .jobs.expire_drafts import expire_drafts
from .jobs.scan import run_offday_recap, run_paid_scan
from .services import get_brief, get_marketing_summary, get_stats, list_scripts, save_script

def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    if args.cache_dir:
        settings.cache_root = Path(args.cache_dir)
    if args.offline:
        offline_settings(settings, fixtures_dir=Path(args.fixtures) if args.fixtures else DEFAULT_FIXTURES)
    return settings


def _deps(args: argparse.Namespace):
    settings = _settings(args)
    user_id = args.user_id or settings.marketing_user_id
    if not user_id:
        sys.exit("--user-id (or MARKETING_USER_ID) is required")
    backend = None
    if settings.offline:
        seed = json.loads((settings.fixtures_dir / "context_map.json").read_text())
        backend = offline_backend(user_id, settings, context_seed=seed)
    return build_deps(user_id, settings, backend=backend)


def _print(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    def common(suppress: bool) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(add_help=False)
        kw = {"default": argparse.SUPPRESS} if suppress else {}
        p.add_argument("--offline", action="store_true", help="in-memory store + recorded fixtures, no network", **kw)
        p.add_argument("--fixtures", help="fixture directory for --offline (default: tests/fixtures)", **kw)
        p.add_argument("--cache-dir", help="local cache root (default: platform user cache dir)", **kw)
        p.add_argument("--user-id", help="parent user id (default: MARKETING_USER_ID)", **kw)
        return p

    parser = argparse.ArgumentParser(prog="marketing_radar", description=__doc__, parents=[common(False)],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "recap", "brief", "stats", "daily-pull", "reset", "scripts", "overlord", "expire"):
        p = sub.add_parser(name, parents=[common(True)])
        if name == "scan":
            p.add_argument("--fresh", action="store_true",
                           help="bypass the 48h request cache: pull every platform live (what the dashboard's Refresh now does)")
    sub.add_parser("like", parents=[common(True)]).add_argument("post_id")
    angle = sub.add_parser("angle", parents=[common(True)])
    angle.add_argument("post_id")
    angle.add_argument("index", type=int)
    sub.add_parser("save", parents=[common(True)]).add_argument("script_id")
    chat = sub.add_parser("chat", parents=[common(True)])
    chat.add_argument("message")
    chat.add_argument("--post-id", dest="post_id")
    chat.add_argument("--thread", default="default")
    args = parser.parse_args(argv)

    deps = _deps(args)
    if args.command == "reset":
        deps.cache.clear()
        path = getattr(deps.store.backend, "path", None)
        if path and Path(path).exists():
            Path(path).unlink()
        print("offline state cleared")
        return 0
    if args.command == "daily-pull":
        bundle = daily_pull(deps.store, deps.cache, deps.settings, deps.clock, force=True)
        _print({"context": bundle.context.to_doc(), "scan_id": bundle.scan_id})
        return 0
    if args.command == "scan":
        outcome = run_paid_scan(deps, fresh=bool(getattr(args, "fresh", False)))
        _print({"note": outcome.note, "planned": [c.endpoint_key for c in outcome.planned],
                "live_calls": outcome.live_calls, "credits_spent": outcome.credits_spent,
                "brief": outcome.brief.to_doc() if outcome.brief else None})
        return 0 if outcome.brief else 1
    if args.command == "recap":
        outcome = run_offday_recap(deps)
        _print({"note": outcome.note, "brief": outcome.brief.to_doc() if outcome.brief else None})
        return 0 if outcome.brief else 1
    if args.command == "brief":
        _print(get_brief(deps.store, deps.cache, deps.settings, clock=deps.clock))
        return 0
    if args.command == "stats":
        _print(get_stats(deps.store, deps.cache, deps.settings, clock=deps.clock))
        return 0
    if args.command == "like":
        result = like_post(deps, args.post_id)
        _print({"post_id": result.post.post_id, "transcript_source": result.transcript_source,
                "breakdown": result.breakdown, "angles": result.angles, "model_used": result.model_used})
        return 0
    if args.command == "angle":
        _print(choose_angle(deps, args.post_id, args.index).to_doc())
        return 0
    if args.command == "save":
        _print(save_script(deps.store, args.script_id, clock=deps.clock).to_doc())
        return 0
    if args.command == "scripts":
        _print({"saved": list_scripts(deps.store, status="saved"), "drafts": list_scripts(deps.store, status="draft")})
        return 0
    if args.command == "chat":
        reply = ChatSession(deps, args.thread).send(args.message, attached_post_id=args.post_id)
        _print({"reply": reply.text, "actions": reply.actions, "model_used": reply.model_used, "scan_id": reply.scan_id})
        return 0
    if args.command == "overlord":
        _print(get_marketing_summary(deps.store, deps.cache, deps.settings, clock=deps.clock))
        return 0
    if args.command == "expire":
        result = expire_drafts(deps.store, deps.settings, deps.clock)
        _print({"deleted_scripts": result.deleted_scripts, "pruned_cache": result.pruned_cache})
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
