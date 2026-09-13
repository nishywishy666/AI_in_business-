from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from marketing_radar.config import Settings
from marketing_radar.db import MemoryBackend, RadarStore

FIXTURES = Path(__file__).parent / "fixtures"
USER_ID = "user_demo"
FROZEN_NOW = dt.datetime(2026, 9, 12, 2, 0, 0, tzinfo=dt.timezone.utc)


class FrozenClock:
    def __init__(self, start: dt.datetime = FROZEN_NOW) -> None:
        self.now = start

    def __call__(self) -> dt.datetime:
        return self.now

    def advance(self, **kwargs) -> dt.datetime:
        self.now = self.now + dt.timedelta(**kwargs)
        return self.now


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        offline=True,
        cache_root=tmp_path / "cache",
        fixtures_dir=FIXTURES,
        scrapecreators_api_key="test-key",
        gemini_api_key="test-gemini",
        youtube_api_key="test-youtube",
    )


@pytest.fixture
def backend() -> MemoryBackend:
    return MemoryBackend()


@pytest.fixture
def store(backend: MemoryBackend, settings: Settings) -> RadarStore:
    return RadarStore(USER_ID, backend, settings)


def make_scan_deps(store: RadarStore, settings: Settings, clock, *, gemini_transport=None, youtube: bool = True,
                   trends_series: dict | None = None):
    """ScanDeps wired to fixtures: SC + free HTTP via FixtureTransport, Gemini via a fake."""
    from marketing_radar.agent import GeminiLadder
    from marketing_radar.cache import LocalCache
    from marketing_radar.deps import OfflineGeminiTransport, offline_transport
    from marketing_radar.jobs.scan import ScanDeps
    from marketing_radar.scrapers import ScrapeCreatorsClient
    from marketing_radar.scrapers.free_reddit import FreeReddit
    from marketing_radar.scrapers.free_trends import FakeTrendsProvider, FreeTrends
    from marketing_radar.scrapers.free_youtube import FreeYouTube
    from marketing_radar.scrapers.transcripts import FakeTranscriptProvider

    if not youtube:
        settings.youtube_api_key = None
    transport = offline_transport(FIXTURES)
    gemini = GeminiLadder(store, settings, gemini_transport or OfflineGeminiTransport(), clock)
    return ScanDeps(
        store=store, cache=LocalCache(USER_ID, settings.cache_root), settings=settings, clock=clock,
        scraper=ScrapeCreatorsClient(store, transport, settings, clock),
        youtube=FreeYouTube(store, transport, settings, clock),
        reddit=FreeReddit(transport, settings, clock),
        trends=FreeTrends(FakeTrendsProvider(trends_series)),
        gemini=gemini,
        youtube_transcripts=FakeTranscriptProvider({"ytfree00001": "free youtube transcript text"}),
    ), transport


def seed_context(backend: MemoryBackend, context: dict) -> None:
    backend.set(f"users/{USER_ID}/context", context)


@pytest.fixture
def sample_context_md() -> str:
    return (FIXTURES / "context.md").read_text()


@pytest.fixture
def sample_context_map() -> dict:
    import json

    return json.loads((FIXTURES / "context_map.json").read_text())
