"""Read helpers for the Marketing UI and the overlord. Cache first, Firestore on miss, never a scrape."""
from __future__ import annotations

from ..cache import LocalCache
from ..clock import Clock, utc_now
from ..config import Settings
from ..db import RadarStore
from ..jobs.daily_pull import ContextMissing, daily_pull
from ..packets import ScanBrief, TrendPacket


def get_brief(store: RadarStore, cache: LocalCache, settings: Settings, *, scan_id: str | None = None,
              clock: Clock = utc_now) -> dict | None:
    if scan_id is None:
        try:
            bundle = daily_pull(store, cache, settings, clock)
        except ContextMissing:
            return None
        return bundle.brief.to_doc() if bundle.brief else None
    doc = store.get(store.paths.scan(scan_id))
    return ScanBrief.model_validate(doc).to_doc() if doc else None


def get_post(store: RadarStore, post_id: str) -> dict | None:
    doc = store.get(store.paths.post(post_id))
    return TrendPacket.model_validate(doc).to_doc() if doc else None


def get_saved_scripts(store: RadarStore) -> list[dict]:
    return [data for _, data in store.list(store.paths.scripts, where=[("status", "==", "saved")])]
