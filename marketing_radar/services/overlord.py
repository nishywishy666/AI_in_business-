"""Read-only helpers for the overlord (main agent). Cache/Firestore only — never scrapes, never
calls Gemini, never writes marketingRadar/**. See OVERLORD.md."""
from __future__ import annotations

from ..cache import LocalCache
from ..clock import Clock, utc_now
from ..config import Settings
from ..db import RadarStore
from .brief import get_brief, get_post, get_saved_scripts
from .stats import get_stats


def get_marketing_summary(store: RadarStore, cache: LocalCache, settings: Settings, *,
                          clock: Clock = utc_now) -> dict | None:
    """One packet the overlord can hand to its model to answer 'what's trending / what should I film /
    how many credits'. Always cites the scan_id it came from."""
    brief = get_brief(store, cache, settings, clock=clock)
    stats = get_stats(store, cache, settings, clock=clock)
    if brief is None:
        return None if stats is None else {"scan_id": None, "empty": True, "stats": _stats_line(stats)}

    def card(post_id: str) -> dict:
        post = get_post(store, post_id) or {"post_id": post_id}
        return {k: post.get(k) for k in ("post_id", "platform", "url", "author", "hook", "likes", "comments",
                                         "shares", "views", "why_it_works", "format_guess", "final")}

    film = brief.get("film_this")
    return {
        "scan_id": brief["scan_id"],
        "generated_at": brief["generated_at"],
        "kind": brief["kind"],
        "next_scan_at": brief.get("next_scan_at"),
        "weekly_take": brief.get("weekly_take"),
        "patterns": brief.get("patterns", []),
        "ignore": brief.get("ignore", []),
        "film_this": ({**card(film["post_id"]), "why": film.get("why")} if film else None),
        "niche": [card(r["post_id"]) for r in brief.get("niche", [])[:3]],
        "global": [card(r["post_id"]) for r in brief.get("global", [])[:3]],
        "credits": brief.get("credits"),
        "saved_scripts": len(get_saved_scripts(store)),
        "stats": _stats_line(stats) if stats else None,
        "alerts": [a["message"] for a in (stats or {}).get("alerts", [])],
        "source_note": f"From ScanBrief {brief['scan_id']}. Not a live scrape.",
    }


def get_marketing_stats(store: RadarStore, cache: LocalCache, settings: Settings, *,
                        clock: Clock = utc_now) -> dict | None:
    return get_stats(store, cache, settings, clock=clock)


def _stats_line(stats: dict) -> dict:
    sc, gm, yt = stats["scrapecreators"], stats["gemini"], stats["youtube_data_api"]
    return {
        "scrapecreators_remaining": sc["remaining"], "scrapecreators_status": sc["status"],
        "scrapecreators_resets": False,
        "gemini_active_model": gm["active_model"], "gemini_quality_warning": gm["quality_warning"],
        "gemini_resets_at": gm["resets_at"],
        "youtube_units_remaining": yt["remaining"], "youtube_resets_at": yt["resets_at"],
        "stale": stats["stale"],
    }
