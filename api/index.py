"""Single ASGI entry for Vercel and uvicorn: mounts the voice routers, the dashboard (UI at `/`,
JSON under /api/dashboard, /api/marketing, /api/overlord, /api/jobs — plan 0005) and /sim when ENABLE_SIM=1.

`app` is created lazily (PEP 562 `__getattr__`) so importing this module in tests does not require
the full environment, while a Vercel/uvicorn import of `api.index:app` fails fast at boot on a
missing variable (§14).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from services.common.config import VoiceConfig, get_config
from services.common.logging import configure_logging
from services.voice.pipeline import (
    PipelineDeps,
    PipelineFactory,
    PlaceholderTurnEngine,
    TurnEngine,
    build_pipeline_factory,
    load_greeting,
)
from services.voice.signature import UsedTokenCache
from services.voice.sinks import CallSink, make_sink
from services.voice.telemetry import HudBus

log = logging.getLogger(__name__)


def build_engine(config: VoiceConfig, sink: CallSink, bus: HudBus, *, reader=None, router=None, answerer=None,
                 booking=None, windows: list[dict] | None = None) -> TurnEngine:
    """Real Groq/Gemini/Firestore unless a fake is injected. Clients are created lazily, so this is
    safe to call with dummy keys in tests as long as the fakes are supplied."""
    from services.voice.answerer import GenAiAnswerTransport
    from services.voice.engine import EngineDeps, ReceptionistTurnEngine
    from services.voice.router import GroqTransport
    from services.voice.tools import FirestoreReader

    if reader is None:
        if sink.kind == "local":
            # Offline: answer from the dashboard-owned dataset (data/business/<BUSINESS_DATASET>), plan 0005.
            from config import load_yaml
            from dashboard.settings import get_settings
            from services.voice.tools import JsonBusinessReader

            reader = JsonBusinessReader(get_settings().dataset_dir, config.business_id,
                                        windows=list((load_yaml("capacity.yaml") or {}).get("windows") or []))
        else:
            from services.common.firestore import make_client

            reader = FirestoreReader(make_client(config))
    if router is None:
        router = GroqTransport(config.groq_api_key, config.groq_router_model)
    if answerer is None:
        answerer = GenAiAnswerTransport.resolve(config.gemini_api_key)
    if windows is None:
        from config import load_yaml

        windows = list((load_yaml("capacity.yaml") or {}).get("windows") or [])
    if booking is None:
        booking = build_booking(config, sink, reader, windows)
    return ReceptionistTurnEngine(EngineDeps(reader=reader, sink=sink, bus=bus, router=router, answerer=answerer,
                                             business_id=config.business_id, tz=config.tz_business, booking=booking,
                                             windows=windows))


def build_booking(config: VoiceConfig, sink: CallSink, reader, windows: list[dict]):
    """Firestore committer + real mailer/calendar in production; local committer + log mailer +
    null calendar whenever the sink is not Firestore, so a simulator run cannot consume real
    seats, send real mail, or touch the owner's calendar."""
    from services.booking.calendar import GoogleCalendarMirror, NullCalendar
    from services.booking.capacity import parse_windows
    from services.booking.commit import FirestoreCommitter, LocalCommitter
    from services.booking.email import GmailMailer, LogMailer, ResendMailer
    from services.common.firestore import BusinessPaths
    from services.voice.booking_machine import BookingDeps, BookingMachine

    paths = BusinessPaths(config.business_id)
    if sink.kind == "firestore":
        committer = FirestoreCommitter(reader.client, paths)
        mailer = (ResendMailer(config.resend_api_key or "", config.gmail_user or "bookings@localhost")
                  if config.email_provider == "resend" else GmailMailer(config.gmail_user or "", config.gmail_app_password or ""))
        calendar = GoogleCalendarMirror(config.google_service_account, config.google_calendar_id, config.tz_business)
    else:
        committer = LocalCommitter(reader, paths, sink)
        mailer, calendar = LogMailer(), NullCalendar()
    return BookingMachine(BookingDeps(reader=reader, sink=sink, committer=committer, mailer=mailer, calendar=calendar,
                                      windows=parse_windows(windows), tz=config.tz_business,
                                      owner_email=config.gmail_user))


def build_deps(config: VoiceConfig, *, sink: CallSink | None = None, engine: TurnEngine | None = None,
               greeting: bytes | None = None, placeholder_engine: bool = False, providers=None,
               audio: bool = False, **engine_kwargs) -> PipelineDeps:
    """`audio=True` (production) loads the VAD/turn/STT/TTS providers so the websocket runs the full
    receptionist pipeline; otherwise the stream echoes (Phase 1) and only Mode A is intelligent."""
    sink = sink or make_sink(config)
    bus = HudBus()
    if engine is None:
        engine = PlaceholderTurnEngine() if placeholder_engine else build_engine(config, sink, bus, **engine_kwargs)
    if providers is None and audio:
        from services.voice.providers import load_default_providers

        providers = load_default_providers(elevenlabs_api_key=config.elevenlabs_api_key,
                                           voice_id=config.elevenlabs_voice_id, tts_model=config.elevenlabs_tts_model)
    return PipelineDeps(sink=sink, bus=bus, engine=engine,
                        greeting=greeting if greeting is not None else load_greeting(), providers=providers,
                        tz=config.tz_business, business_id=config.business_id)


def create_app(config: VoiceConfig | None = None, *, pipeline_factory: PipelineFactory | None = None,
               deps: PipelineDeps | None = None, dashboard: bool = True) -> FastAPI:
    from api.voice import fallback, incoming, status, ws

    configure_logging()
    config = config or get_config()
    # Audio providers only when ElevenLabs is configured; otherwise the socket echoes (Phase 1) and
    # Mode A / the dashboard stay fully usable without keys.
    deps = deps or build_deps(config, audio=bool(config.elevenlabs_api_key and config.elevenlabs_voice_id))
    app = FastAPI(title="Uncle Tony Overlord Dashboard", docs_url=None, redoc_url=None)
    app.state.config = config
    app.state.deps = deps
    app.state.used_tokens = UsedTokenCache()
    factory = pipeline_factory or build_pipeline_factory(deps)
    app.include_router(incoming.build_router(config))
    app.include_router(ws.build_router(config, factory, app.state.used_tokens))
    app.include_router(status.build_router(config, sink=deps.sink))
    app.include_router(fallback.build_router())

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "business_id": config.business_id, "sink": deps.sink.kind}

    if config.enable_sim:
        from api.sim.app import mount_sim

        mount_sim(app, config, deps)
    if dashboard:
        from dashboard.app import mount_dashboard

        mount_dashboard(app, config, deps)
    return app


def __getattr__(name: str):
    if name == "app":
        return create_app()
    raise AttributeError(name)
