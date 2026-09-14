"""Mount the dashboard onto the voice receptionist's FastAPI app (plan 0005).

Called from `api/index.py::create_app`. Everything network-bound (Firestore client, Gemini model
list, marketing deps) is created lazily on first request so importing/booting stays offline-safe.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from marketing_radar.services.api import fastapi_router
from services.common.config import VoiceConfig
from services.common.firestore import BusinessPaths
from services.voice.pipeline import PipelineDeps
from services.voice.sinks import LocalJsonlSink
from services.voice.tools import BusinessReader, FirestoreReader, JsonBusinessReader

from .marketing import MarketingHub
from .packet import PacketStore
from .records import FirestoreSource, LocalJsonlSource, RecordSource
from .routes import DashboardContext, build_router
from .settings import DashboardSettings, get_settings
from .ui import mount_ui

log = logging.getLogger(__name__)
UTC = dt.timezone.utc


def make_source(config: VoiceConfig, deps: PipelineDeps, *, settings: DashboardSettings) -> RecordSource:
    sink = deps.sink
    if sink.kind == "local":
        root = sink.root if isinstance(sink, LocalJsonlSink) else Path(".testruns")
        return LocalJsonlSource(root)
    client = getattr(sink, "client", None)
    if client is None:
        from services.common.firestore import make_client

        client = make_client(config)
    # One pull a day into a packet on disk (dashboard/packet.py); DASHBOARD_PACKET_HOURS tunes it,
    # DASHBOARD_PACKET_DIR says where, and 0 hours means "always live" for debugging.
    try:
        hours = float(os.environ.get("DASHBOARD_PACKET_HOURS", "24") or 24)
    except ValueError:  # a typo in the env is not a reason to refuse to start
        log.warning("DASHBOARD_PACKET_HOURS is not a number; using 24")
        hours = 24.0
    packet = PacketStore(config.business_id, max_age_hours=hours)
    return FirestoreSource(client, BusinessPaths(config.business_id), packet=packet)


def make_reader(config: VoiceConfig, deps: PipelineDeps, *, settings: DashboardSettings) -> BusinessReader:
    """The same reader the engine answers from, so Business Context shows what callers hear."""
    engine_reader = getattr(getattr(deps.engine, "deps", None), "reader", None)
    if engine_reader is not None:
        return engine_reader
    if deps.sink.kind == "local":
        from config import load_yaml

        return JsonBusinessReader(settings.dataset_dir, config.business_id,
                                  windows=list((load_yaml("capacity.yaml") or {}).get("windows") or []))
    from services.common.firestore import make_client

    return FirestoreReader(make_client(config))


class _OverlordModel:
    """Resolves the Gemini answer transport once, lazily; None when no key or resolution fails."""

    def __init__(self, api_key: str | None) -> None:
        self.api_key = api_key
        self._transport: Any = None
        self._tried = False

    def __call__(self):
        if self._tried:
            return self._transport
        self._tried = True
        if not self.api_key or self.api_key in ("offline", "gemini-test"):
            return None
        try:
            from .overlord import GenAiOverlordTransport

            self._transport = GenAiOverlordTransport.resolve(self.api_key)
        except Exception as exc:
            log.warning("overlord model unavailable, template answers only: %s", exc)
            self._transport = None
        return self._transport


def mount_dashboard(app: FastAPI, config: VoiceConfig, deps: PipelineDeps, *, settings: DashboardSettings | None = None,
                    source: RecordSource | None = None, reader: BusinessReader | None = None,
                    marketing: MarketingHub | None = None, clock=lambda: dt.datetime.now(UTC)) -> DashboardContext:
    settings = settings or get_settings()
    source = source or make_source(config, deps, settings=settings)
    reader = reader or make_reader(config, deps, settings=settings)
    marketing = marketing or MarketingHub(config.business_id, settings)
    ctx = DashboardContext(settings=settings, source=source, reader=reader, business_id=config.business_id,
                           marketing=marketing, clock=clock, overlord_transport=_OverlordModel(config.gemini_api_key),
                           calendar_retry=_calendar_retry(config, deps, reader))
    app.include_router(build_router(ctx))
    app.include_router(_marketing_router(marketing), prefix="/api/marketing")
    mount_ui(app)
    app.state.dashboard = ctx
    if os.environ.get("MARKETING_SCHEDULER", "0").strip() == "1":
        _start_scheduler(marketing)
    return ctx


class _LazyRadarApi:
    """Quacks like `RadarApi` but builds the marketing deps on first call, so booting the app never
    touches Firestore or the fixtures. A build failure becomes the spec §18 503 body."""

    def __init__(self, hub: MarketingHub) -> None:
        self.hub = hub

    def __getattr__(self, name: str):
        try:
            api = self.hub.api()
        except Exception as exc:
            detail = str(exc)[:200]
            return lambda *args, **kwargs: (503, {"error": "marketing data unavailable", "detail": detail})
        return getattr(api, name)


def _marketing_router(marketing: MarketingHub):
    return fastapi_router(_LazyRadarApi(marketing))  # type: ignore[arg-type]


def _calendar_retry(config: VoiceConfig, deps: PipelineDeps, reader: BusinessReader):
    def run(now: dt.datetime) -> int:
        if deps.sink.kind != "firestore":
            return 0
        from services.booking.calendar import GoogleCalendarMirror, retry_unsynced

        calendar = GoogleCalendarMirror(config.google_service_account, config.google_calendar_id, config.tz_business)
        return retry_unsynced(reader, calendar, deps.sink, config.business_id, now=now)

    return run


def _start_scheduler(marketing: MarketingHub) -> None:
    """Local/long-running hosts only; Vercel uses `crons` → /api/jobs/* instead."""
    try:
        from marketing_radar.scheduler import start_marketing_radar

        start_marketing_radar(marketing.user_id, settings=marketing.settings, deps=marketing.deps())
    except Exception as exc:
        log.warning("marketing scheduler not started: %s", exc)
