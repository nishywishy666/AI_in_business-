"""Cached usage snapshot (spec §13). Rebuilt after every spend; never a live scrape on read."""
from __future__ import annotations

import datetime as dt

from ..agent.gemini import rung_statuses_from_daily
from ..cache import LocalCache
from ..clock import Clock, humanize_delta, next_pacific_midnight_utc, pacific_date, parse_iso
from ..config import Settings
from ..db import RadarStore
from ..packets import (
    FreeSource,
    GeminiDaily,
    GeminiModelUsage,
    GeminiUsage,
    ScrapeCreatorsUsage,
    UsageSnapshot,
    YouTubeUsage,
)
from .alerts import compute_alerts, sync_notifications


def build_snapshot(store: RadarStore, settings: Settings, clock: Clock, *,
                   free_statuses: dict[str, str] | None = None) -> UsageSnapshot:
    now = clock()
    today = pacific_date(now)
    resets_at = next_pacific_midnight_utc(now)
    meta = store.get(store.paths.meta) or {}
    latest = store.get(store.paths.latest) or {}
    events = [data for _, data in store.list(store.paths.usage_events, where=[("pacific_date", "==", today)])]

    remaining = meta.get("sc_credits_remaining")
    remaining = int(remaining) if remaining is not None else None
    spent_today = sum(int(e.get("credits_charged") or 0) for e in events if e.get("provider") == "scrapecreators")
    cost = settings.next_scan_estimated_cost
    if remaining is None:
        sc_status = "unknown"
    elif remaining == 0:
        sc_status = "exhausted"
    elif remaining <= 25 or remaining < cost:
        sc_status = "low"
    else:
        sc_status = "ok"
    scrapecreators = ScrapeCreatorsUsage(
        remaining=remaining, spent_last_scan=int(latest.get("spent_last_scan") or 0), spent_today=spent_today,
        reserve=settings.reserve, usable_now=max(0, (remaining or 0) - settings.reserve),
        next_scan_estimated_cost=cost, transcripts_affordable=bool(remaining is not None and remaining > settings.reserve),
        status=sc_status,
    )

    daily_doc = store.get(store.paths.gemini_daily(today))
    daily = GeminiDaily.model_validate(daily_doc) if daily_doc else GeminiDaily(pacific_date=today)
    statuses = rung_statuses_from_daily(settings, daily, now=now)  # `now` so a cooling model reads as spent
    models = [GeminiModelUsage(id=s.model_id, label=s.rung.label, quality=s.rung.quality, used_today=s.used,
                               daily_cap=s.rung.daily_cap, remaining=s.remaining, status=s.status) for s in statuses]
    active_index = next((i for i, s in enumerate(statuses) if s.status == "ok"), None)
    active = statuses[active_index] if active_index is not None else None
    quality_warning = None
    if active is not None and (active.rung.quality != "high" or active_index != 0):
        note = active.rung.note or f"Using {active.rung.label}."
        quality_warning = f"{note} Generation quality is lower than {settings.ladder[0].label}."
    gemini = GeminiUsage(
        active_model=active.model_id if active else None,
        active_quality=active.rung.quality if active else None,
        quality_warning=quality_warning, resets_at=resets_at, resets_in=humanize_delta(resets_at - now),
        models=models,
    )

    units = sum(int(e.get("units") or 0) for e in events if e.get("provider") == "youtube_data_api")
    yt_remaining = max(0, settings.youtube_daily_quota - units)
    if not settings.free_youtube_enabled:
        yt_status = "unknown"
    elif yt_remaining == 0:
        yt_status = "exhausted"
    elif yt_remaining <= 0.2 * settings.youtube_daily_quota:
        yt_status = "low"
    else:
        yt_status = "ok"
    youtube = YouTubeUsage(daily_units_used=units, daily_quota=settings.youtube_daily_quota, remaining=yt_remaining,
                           status=yt_status, resets_at=resets_at)

    statuses_free = free_statuses or {}
    free_sources = [FreeSource(id="reddit", status=statuses_free.get("reddit", "ok")),
                    FreeSource(id="google_trends", status=statuses_free.get("google_trends", "ok"))]

    snapshot = UsageSnapshot(checked_at=now, scrapecreators=scrapecreators, gemini=gemini,
                             youtube_data_api=youtube, free_sources=free_sources)
    snapshot.alerts = compute_alerts(snapshot, settings, now)
    return snapshot


def refresh_snapshot(store: RadarStore, settings: Settings, clock: Clock, *, cache: LocalCache | None = None,
                     free_statuses: dict[str, str] | None = None) -> UsageSnapshot:
    snapshot = build_snapshot(store, settings, clock, free_statuses=free_statuses)
    sync_notifications(store, snapshot.alerts, clock())
    store.set(store.paths.usage_snapshot, snapshot.to_doc())
    if cache is not None:
        cache.write_stats(snapshot)
    return snapshot


def mark_stale(snapshot: UsageSnapshot, now: dt.datetime, settings: Settings) -> UsageSnapshot:
    checked = snapshot.checked_at if isinstance(snapshot.checked_at, dt.datetime) else parse_iso(str(snapshot.checked_at))
    stale = checked is None or (now - checked) > dt.timedelta(hours=settings.snapshot_stale_hours)
    return snapshot.model_copy(update={"stale": stale})
