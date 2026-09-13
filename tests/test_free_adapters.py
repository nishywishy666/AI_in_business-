import pytest

from marketing_radar.context import parse_context
from marketing_radar.scrapers import FixtureTransport
from marketing_radar.scrapers.free_reddit import FreeReddit, default_subreddits
from marketing_radar.scrapers.free_trends import FakeTrendsProvider, FreeTrends
from marketing_radar.scrapers.free_youtube import FreeYouTube
from tests.conftest import FIXTURES, FROZEN_NOW, USER_ID


@pytest.fixture
def transport():
    t = FixtureTransport(FIXTURES / "free")
    t.route("https://www.googleapis.com/youtube/v3/search", "youtube_search.json")
    t.route("https://www.googleapis.com/youtube/v3/videos", "youtube_videos.json")
    t.route("https://www.reddit.com/r/", "reddit_hot.json")
    return t


def test_youtube_tracks_units_and_filters_long_form(store, transport, settings, clock):
    yt = FreeYouTube(store, transport, settings, clock)
    packets = yt.search(["home workout"], max_duration_sec=60)
    assert yt.units_used_today() == 101 and yt.remaining() == 9899
    assert [p.provider_id for p in packets] == ["ytfree00001", "ytfree00003"]
    assert packets[0].platform == "youtube" and packets[0].post_id == "yt_ytfree00001"
    assert packets[0].views == 410000 and packets[0].shares == 0 and packets[0].duration_sec == 38


def test_youtube_skips_without_key_or_quota(store, transport, settings, clock):
    settings.youtube_api_key = None
    assert FreeYouTube(store, transport, settings, clock).search(["x"]) == []
    assert transport.calls == []

    settings.youtube_api_key = "k"
    settings.youtube_daily_quota = 100
    assert FreeYouTube(store, transport, settings, clock).search(["x"]) == []
    assert transport.calls == []


def test_youtube_quota_resets_on_pacific_day(store, transport, settings, clock):
    yt = FreeYouTube(store, transport, settings, clock)
    yt.search(["home workout"])
    assert yt.units_used_today() == 101
    clock.advance(hours=6)  # 02:00Z -> 08:00Z crosses midnight Pacific (07:00Z)
    assert yt.units_used_today() == 0


def test_reddit_normalizes_and_drops_stickies(transport, settings, clock):
    reddit = FreeReddit(transport, settings, clock)
    packets = reddit.hot(["homeworkout"])
    assert [p.provider_id for p in packets] == ["1abcd02", "1abcd03"]
    assert packets[0].platform == "reddit" and packets[0].likes == 1340 and packets[0].comments == 287
    assert transport.calls[0].headers["User-Agent"].startswith("marketing-radar")
    assert reddit.last_status == "ok"


def test_reddit_failure_sets_unknown_status(settings, clock):
    transport = FixtureTransport()
    reddit = FreeReddit(transport, settings, clock)
    assert reddit.hot(["nope"]) == []
    assert reddit.last_status == "unknown"


def test_default_subreddits_from_context(sample_context_map):
    ctx = parse_context(sample_context_map, user_id=USER_ID, source_path="x", now=FROZEN_NOW)
    assert default_subreddits(ctx) == ["homeworkout", "busyparents"]
    ctx2 = ctx.model_copy(update={"subreddits": ["fitness", "homegym", "extra"]})
    assert default_subreddits(ctx2) == ["fitness", "homegym"]


def test_trends_signals_and_failure():
    trends = FreeTrends(FakeTrendsProvider({"home workout": [40, 40, 40, 80]}))
    signals = trends.signals(["home workout", "busy parents"])
    by_kw = {s.keyword: s for s in signals}
    assert by_kw["home workout"].label == "rising" and by_kw["busy parents"].label == "steady"
    assert trends.last_status == "ok"

    broken = FreeTrends(FakeTrendsProvider(fail=True))
    assert broken.signals(["x"]) == [] and broken.last_status == "unknown"
