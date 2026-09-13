"""Daily Firestore → local cache pull (spec §7.3). At most one pull per calendar day."""
from __future__ import annotations

from typing import Callable

from ..cache import CacheBundle, LocalCache
from ..clock import Clock
from ..config import Settings
from ..context import local_expand, parse_context
from ..db import RadarStore
from ..packets import ContextProfile, ScanBrief, UsageSnapshot

Expander = Callable[[ContextProfile], list[str] | None]


class ContextMissing(RuntimeError):
    """The parent has not stored a questionnaire context yet."""


def daily_pull(store: RadarStore, cache: LocalCache, settings: Settings, clock: Clock, *,
               force: bool = False, expander: Expander | None = None) -> CacheBundle:
    now = clock()
    if not force and cache.is_fresh(now):
        cached = cache.read()
        if cached is not None:
            return cached

    raw = store.read_context()
    if raw is None:
        raise ContextMissing(store.context_path)
    context = parse_context(raw, user_id=store.user_id, source_path=store.context_path, now=now)
    context = _apply_cached_expansion(store, context, expander)
    store.set(store.paths.context_cache, context.to_doc())

    latest = store.get(store.paths.latest) or {}
    brief = None
    if latest.get("scan_id"):
        brief_doc = store.get(store.paths.scan(latest["scan_id"]))
        brief = ScanBrief.model_validate(brief_doc) if brief_doc else None
    stats_doc = store.get(store.paths.usage_snapshot)
    stats = UsageSnapshot.model_validate(stats_doc) if stats_doc else None
    return cache.write(context=context, brief=brief, stats=stats, now=now)


def _apply_cached_expansion(store: RadarStore, context: ContextProfile,
                            expander: Expander | None) -> ContextProfile:
    if context.hashtags:
        return context
    previous = store.get(store.paths.context_cache) or {}
    if previous.get("raw_text") == context.raw_text and previous.get("hashtags"):
        return context.model_copy(update={
            "hashtags": list(previous["hashtags"]),
            "subreddits": context.subreddits or list(previous.get("subreddits") or []),
            "hashtags_expanded_by": previous.get("hashtags_expanded_by"),
        })
    expanded: list[str] | None = None
    if expander is not None:
        try:
            expanded = expander(context)
        except Exception:
            expanded = None
    if expanded:
        return context.model_copy(update={"hashtags": expanded, "hashtags_expanded_by": "gemini"})
    return context.model_copy(update={"hashtags": local_expand(context), "hashtags_expanded_by": "local"})
