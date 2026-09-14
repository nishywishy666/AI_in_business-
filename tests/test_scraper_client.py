import pytest

from marketing_radar.scoring.normalize import normalize_response
from marketing_radar.scrapers import (
    CREDIT_BALANCE_PATH,
    SCAN_ENDPOINTS,
    CreditsExhausted,
    FixtureTransport,
    ForbiddenParameter,
    NotAllowlisted,
    ScrapeCreatorsClient,
    ScrapeCreatorsError,
)
from marketing_radar.scrapers.endpoints import Endpoint
from tests.conftest import FIXTURES


@pytest.fixture
def transport():
    t = FixtureTransport(FIXTURES / "scrapecreators")
    for ep in SCAN_ENDPOINTS.values():
        t.route(ep.path, f"{ep.key}.json")
    t.route(CREDIT_BALANCE_PATH, {"credits_remaining": 100})
    return t


@pytest.fixture
def client(store, transport, settings, clock):
    return ScrapeCreatorsClient(store, transport, settings, clock)


def test_non_allowlisted_path_raises(client, transport):
    rogue = Endpoint("tiktok_comments", "/v1/tiktok/video/comments", "tiktok", "scan", "global")
    with pytest.raises(NotAllowlisted):
        client.fetch(rogue, {"url": "x"})
    assert transport.calls == []


def test_forbidden_params_raise(client, transport):
    with pytest.raises(ForbiddenParameter):
        client.fetch("tiktok_hashtag", {"hashtag": "x", "cursor": "abc"})
    with pytest.raises(ForbiddenParameter):
        client.fetch("tiktok_transcript", {"url": "x", "use_ai_as_fallback": True})
    assert transport.calls == []


def test_acceptance_3_identical_request_within_48h_hits_cache(client, transport, store, clock):
    first = client.fetch("tiktok_trending")
    assert first.cached is False and first.credits_charged == 1 and first.credits_remaining == 97
    assert client.cached_credits_remaining() == 97
    assert transport.calls[-1].headers["x-api-key"] == "test-key"

    clock.advance(hours=47)
    second = client.fetch("tiktok_trending")
    assert second.cached is True and second.credits_charged == 0
    assert len(transport.calls) == 1

    clock.advance(hours=2)
    third = client.fetch("tiktok_trending")
    assert third.cached is False and len(transport.calls) == 2

    events = store.list(store.paths.usage_events)
    assert len(events) == 2 and all(e["provider"] == "scrapecreators" for _, e in events)


def test_params_change_the_cache_key(client, transport):
    client.fetch("tiktok_hashtag", {"hashtag": "homeworkout"})
    client.fetch("tiktok_hashtag", {"hashtag": "fitmom"})
    assert len(transport.calls) == 2


def test_retry_once_on_502_never_on_200(client, transport):
    transport.fail_next("/v1/tiktok/get-trending-feed", 502)
    result = client.fetch("tiktok_trending")
    assert result.cached is False and len(transport.calls) == 2
    transport.fail_next("/v1/instagram/reels/trending", 500)
    with pytest.raises(ScrapeCreatorsError):
        client.fetch("instagram_reels_trending")
    assert len(transport.calls) == 3


def test_402_marks_credits_exhausted(client, transport):
    transport.fail_next("/v1/tiktok/get-trending-feed", 402, {"error": "no credits"})
    with pytest.raises(CreditsExhausted):
        client.fetch("tiktok_trending")
    assert client.cached_credits_remaining() == 0


def test_acceptance_4_balance_only_before_scan_without_cached_figure(client, transport):
    assert client.balance() is None
    assert transport.calls_to(CREDIT_BALANCE_PATH) == []
    assert client.balance(about_to_scan=True) == 100
    assert len(transport.calls_to(CREDIT_BALANCE_PATH)) == 1
    client.fetch("tiktok_trending")
    assert client.balance(about_to_scan=True) == 97
    assert len(transport.calls_to(CREDIT_BALANCE_PATH)) == 1, "never re-polled once a figure is cached"


@pytest.mark.parametrize("key,expected_platform,min_items", [
    ("tiktok_trending", "tiktok", 4), ("tiktok_hashtag", "tiktok", 2), ("tiktok_keyword", "tiktok", 1),
    ("instagram_reels_trending", "instagram", 3), ("instagram_hashtag", "instagram", 3),
    ("youtube_shorts_trending", "youtube", 2), ("facebook_page_reels", "facebook", 2),
    ("facebook_group_posts", "facebook", 1), ("youtube_search", "youtube", 1),
])
def test_every_scan_fixture_normalizes(client, clock, key, expected_platform, min_items):
    result = client.fetch(key, {"hashtag": "homeworkout", "query": "home workout", "url": "https://www.facebook.com/x"})
    packets = normalize_response(expected_platform, result.body, source=key, scraped_at=clock())
    assert len(packets) >= min_items
    for p in packets:
        assert p.platform == expected_platform
        assert p.post_id.startswith({"tiktok": "tt_", "instagram": "ig_", "youtube": "yt_", "facebook": "fb_"}[expected_platform])
        assert p.published_at is not None and p.hook


def test_posts_are_found_inside_an_envelope_nobody_guessed(clock):
    """The live ScrapeCreators envelopes were never recorded (CLAUDE.md), and a wrapper key we did
    not guess used to normalise to zero posts — which is how a scan that really did call TikTok and
    Instagram came back all-YouTube. The deep fallback finds the list wherever it is."""
    body = {"status": "ok", "payload": {"page": 1, "collection": {"nodes": [
        {"aweme_id": "7311", "desc": "60-second toastie #cheese", "statistics": {"digg_count": 900, "play_count": 41000},
         "create_time": 1757000000, "author": {"unique_id": "tony"}},
        {"aweme_id": "7312", "desc": "grill sizzle", "statistics": {"digg_count": 120, "play_count": 5000},
         "create_time": 1757000500, "author": {"unique_id": "tony"}},
    ]}}}
    packets = normalize_response("tiktok", body, source="tiktok_trending", scraped_at=clock())
    assert [p.provider_id for p in packets] == ["7311", "7312"]
    assert all(p.platform == "tiktok" and p.post_id.startswith("tt_") for p in packets)


def test_an_envelope_with_no_posts_still_normalises_to_nothing(clock):
    body = {"status": "error", "meta": {"credits": 94}, "message": "no results"}
    assert normalize_response("tiktok", body, source="tiktok_trending", scraped_at=clock()) == []


def test_facebook_null_views_and_reaction_fallback(client, clock):
    packets = normalize_response("facebook", client.fetch("facebook_page_reels", {"url": "https://www.facebook.com/x"}).body,
                                 source="facebook_page_reels", scraped_at=clock())
    first = packets[0]
    assert first.views is None and first.likes == 8400 and first.comments == 310 and first.shares == 1900
    assert "homeworkout" in first.hashtags


def test_fresh_fetch_bypasses_the_cache_but_still_records_it(client, transport):
    """`fresh=True` is the dashboard's "Refresh now" (plan 0013): a live call inside the 48h window,
    and its body becomes the cache entry the next scheduled scan reuses."""
    first = client.fetch("tiktok_trending")
    cached = client.fetch("tiktok_trending")
    fresh = client.fetch("tiktok_trending", fresh=True)
    assert first.cached is False and cached.cached is True and fresh.cached is False
    assert fresh.credits_charged == 1 and len(transport.calls) == 2
    assert client.fetch("tiktok_trending").cached is True
