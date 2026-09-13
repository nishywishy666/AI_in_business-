"""Usage snapshot reader. Never calls credit-balance, never scrapes, never imports the scrapers."""
from __future__ import annotations

from ..cache import LocalCache
from ..clock import Clock, utc_now
from ..config import Settings
from ..db import RadarStore
from ..packets import UsageSnapshot
from ..usage.snapshot import mark_stale


def get_stats(store: RadarStore, cache: LocalCache, settings: Settings, *, clock: Clock = utc_now) -> dict | None:
    now = clock()
    bundle = cache.read() if cache.is_fresh(now) else None
    snapshot = bundle.stats if bundle and bundle.stats else None
    if snapshot is None:
        doc = store.get(store.paths.usage_snapshot)
        if doc is None:
            return None
        snapshot = UsageSnapshot.model_validate(doc)
        cache.write_stats(snapshot)
    return mark_stale(snapshot, now, settings).to_doc()
