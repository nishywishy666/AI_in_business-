"""Shared builders for the dashboard tests: a seeded local run (through the real engine) and a
FastAPI app with the dashboard mounted over it, all offline and on a frozen clock."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from config import load_yaml
from marketing_radar.config import Settings
from marketing_radar.deps import build_deps as build_marketing_deps
from marketing_radar.deps import offline_backend, offline_settings
from services.common.config import local_config
from services.voice.answerer import FakeAnswerTransport
from services.voice.sinks import LocalJsonlSink
from services.voice.tools import JsonBusinessReader

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

BUSINESS_ID = "uncle_tony"
TZ = "Australia/Melbourne"
SEED_TODAY = dt.date(2026, 9, 10)  # a Thursday, so "today" bookings fall inside the Mon–Fri window
NOW = dt.datetime(2026, 9, 10, 15, 0, tzinfo=ZoneInfo(TZ)).astimezone(dt.timezone.utc)
DATASET = REPO / "data" / "business" / "uncle_tony"


def windows() -> list[dict]:
    return list((load_yaml("capacity.yaml") or {}).get("windows") or [])


def reader(today: dt.date = SEED_TODAY) -> JsonBusinessReader:
    return JsonBusinessReader(DATASET, BUSINESS_ID, windows=windows(), today=today - dt.timedelta(days=7))


def seed_runs(root: Path, *, days: int = 3) -> tuple[LocalJsonlSink, list[dict]]:
    import seed_demo_calls  # scripts/seed_demo_calls.py

    sink = LocalJsonlSink(root, business_id=BUSINESS_ID)
    results = seed_demo_calls.seed(sink=sink, reader=reader(), business_id=BUSINESS_ID, tz=TZ, windows=windows(),
                                   answerer=FakeAnswerTransport(), days=days, today=SEED_TODAY, quiet=True)
    return sink, results


def marketing_hub(tmp_path: Path, clock):
    from dashboard.marketing import MarketingHub
    from dashboard.settings import DashboardSettings

    settings = offline_settings(Settings(), cache_root=tmp_path / "mcache")
    seed = __import__("json").loads((DATASET / "marketing_context.json").read_text())
    backend = offline_backend(BUSINESS_ID, settings, context_seed=seed)
    deps = build_marketing_deps(BUSINESS_ID, settings, backend=backend, clock=clock)
    return MarketingHub(BUSINESS_ID, DashboardSettings.from_yaml(env={}), settings=settings, deps=deps)


def dashboard_app(tmp_path: Path, *, now: dt.datetime = NOW, days: int = 3):
    """App with the dashboard mounted over a seeded local run. Returns (app, ctx, sink)."""
    from api.index import build_deps, create_app
    from dashboard.app import mount_dashboard
    from dashboard.records import LocalJsonlSource
    from dashboard.settings import DashboardSettings
    from services.voice.pipeline import PlaceholderTurnEngine

    clock = lambda: now  # noqa: E731
    sink, _ = seed_runs(tmp_path / ".testruns", days=days)
    config = local_config({"BUSINESS_ID": BUSINESS_ID, "SESSION_SINK": "local", "ENABLE_SIM": "0"})
    deps = build_deps(config, sink=sink, engine=PlaceholderTurnEngine(), greeting=b"")
    app = create_app(config, deps=deps, dashboard=False)
    ctx = mount_dashboard(app, config, deps, settings=DashboardSettings.from_yaml(env={}),
                          source=LocalJsonlSource(sink.root, clock=clock), reader=reader(),
                          marketing=marketing_hub(tmp_path, clock), clock=clock)
    return app, ctx, sink
