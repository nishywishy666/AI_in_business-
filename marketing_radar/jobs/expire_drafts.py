"""Daily cleanup: delete unsaved drafts past expires_at; prune raw scrape cache older than 14 days."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from ..clock import Clock, parse_iso
from ..config import Settings
from ..db import RadarStore


@dataclass
class ExpireResult:
    deleted_scripts: list[str] = field(default_factory=list)
    pruned_cache: int = 0


def expire_drafts(store: RadarStore, settings: Settings, clock: Clock) -> ExpireResult:
    now = clock()
    result = ExpireResult()
    for script_id, data in store.list(store.paths.scripts, where=[("status", "==", "draft")]):
        expires = parse_iso(data.get("expires_at"))
        if expires is None or expires >= now:
            continue
        store.delete(store.paths.script(script_id))
        result.deleted_scripts.append(script_id)
        post_id = data.get("post_id")
        post = store.get(store.paths.post(post_id)) if post_id else None
        if post and post.get("script_id") == script_id:
            store.update(store.paths.post(post_id), {"script_id": None})

    cutoff = now - dt.timedelta(days=settings.dedup_window_days)
    for request_hash, entry in store.list(store.paths.scrape_cache):
        fetched = parse_iso(entry.get("fetched_at"))
        if fetched is not None and fetched < cutoff:
            store.delete(store.paths.scrape_cache_entry(request_hash))
            result.pruned_cache += 1
    return result
