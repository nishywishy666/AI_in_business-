"""One live call per platform on every paid scan (plan 0013). These replaced the spec §10.3 rotation
tests when the owner overrode constraint 5 ("max 3 live calls"): TikTok, Instagram, Facebook and
YouTube Shorts are all pulled, every time, and only an empty balance sheds a call."""
import pytest

from marketing_radar.context import parse_context
from marketing_radar.jobs.scan import PLATFORM_SLOTS, plan_calls, run_paid_scan
from marketing_radar.scrapers import CREDIT_BALANCE_PATH, endpoint_by_key
from tests.conftest import FROZEN_NOW, USER_ID, make_scan_deps, seed_context

SC_HOST = "https://api.scrapecreators.com"


@pytest.fixture
def context(sample_context_map):
    return parse_context(sample_context_map, user_id=USER_ID, source_path="x", now=FROZEN_NOW)


def _keys(context, index, settings, credits=100, **kw):
    return [c.endpoint_key for c in plan_calls(index, context, credits, settings, **kw)]


def _platforms(context, index, settings, credits=100):
    return [endpoint_by_key(c.endpoint_key).platform for c in plan_calls(index, context, credits, settings)]


def test_acceptance_7_every_scan_covers_all_four_platforms(context, settings):
    for index in range(8):
        assert _platforms(context, index, settings) == list(PLATFORM_SLOTS), index
    assert _keys(context, 0, settings) == ["tiktok_trending", "instagram_hashtag", "facebook_page_reels", "youtube_shorts_trending"]
    assert _keys(context, 1, settings) == ["tiktok_hashtag", "instagram_reels_trending", "facebook_group_posts", "youtube_shorts_trending"]
    assert _keys(context, 2, settings) == ["tiktok_keyword", "instagram_hashtag", "facebook_page_reels", "youtube_shorts_trending"]
    assert _keys(context, 3, settings) == ["tiktok_trending", "instagram_reels_trending", "facebook_group_posts", "youtube_shorts_trending"]
    assert _keys(context, 4, settings) == _keys(context, 0, settings)
    # the free YouTube Data API no longer displaces the Shorts feed
    assert _keys(context, 1, settings, free_youtube_covers_shorts=True)[3] == "youtube_shorts_trending"


def test_facebook_falls_back_to_the_configured_page_when_the_questionnaire_has_no_urls(context, settings):
    no_fb = context.model_copy(update={"facebook_page_urls": [], "facebook_group_urls": []})
    for index in range(4):
        calls = plan_calls(index, no_fb, 100, settings)
        assert calls[2].endpoint_key == "facebook_page_reels"
        assert calls[2].params["url"] == settings.facebook_fallback_page_urls[0]
    group_only = context.model_copy(update={"facebook_page_urls": []})
    assert plan_calls(0, group_only, 100, settings)[2].endpoint_key == "facebook_group_posts"
    page_only = context.model_copy(update={"facebook_group_urls": []})
    assert plan_calls(1, page_only, 100, settings)[2].endpoint_key == "facebook_page_reels"
    settings.facebook_fallback_page_urls = ()
    assert _platforms(no_fb, 0, settings) == ["tiktok", "instagram", "youtube"], "nothing to call: slot skipped, never a bad request"


def test_niche_params_come_from_context(context, settings):
    calls = plan_calls(0, context, 100, settings)
    assert calls[1].params == {"hashtag": "homeworkout"}
    assert calls[2].params["url"] == "https://www.facebook.com/fitparentsclub"
    calls = plan_calls(1, context, 100, settings)
    assert calls[0].params == {"hashtag": "homeworkout"}
    assert calls[2].params["url"].startswith("https://www.facebook.com/groups/")
    calls = plan_calls(2, context, 100, settings)
    assert calls[0].params == {"query": "home workout"}
    assert calls[2].params["url"] == "https://www.facebook.com/quickhomeworkouts", "the second page gets its turn"


def test_acceptance_8_only_an_empty_balance_sheds_a_platform(context, settings):
    """Owner override of the spec's reserve shedding: the 15-credit reserve still gates Like
    transcripts (agent/like.py) but never removes a platform from the scan."""
    assert len(plan_calls(0, context, 100, settings)) == 4
    assert len(plan_calls(0, context, 16, settings)) == 4
    assert len(plan_calls(0, context, 15, settings)) == 4
    assert len(plan_calls(0, context, 4, settings)) == 4
    assert [c.endpoint_key for c in plan_calls(0, context, 2, settings)] == ["tiktok_trending", "instagram_hashtag"]
    assert [c.endpoint_key for c in plan_calls(0, context, 1, settings)] == ["tiktok_trending"]
    assert plan_calls(0, context, 0, settings) == []
    assert len(plan_calls(0, context, None, settings)) == 4, "unknown balance does not block the scan"


def test_acceptance_3_paid_scan_issues_one_live_call_per_platform(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    deps, transport = make_scan_deps(store, settings, clock, youtube=False)
    outcome = run_paid_scan(deps)
    sc_calls = [c for c in transport.calls if c.url.startswith(SC_HOST) and CREDIT_BALANCE_PATH not in c.url]
    assert len(sc_calls) == 4 and outcome.live_calls == 4 and outcome.credits_spent == 4
    assert [endpoint_by_key(c.endpoint_key).platform for c in outcome.planned] == list(PLATFORM_SLOTS)
    assert outcome.brief is not None and outcome.brief.kind == "paid_scan"
    assert {p.platform for p in outcome.scored} >= set(PLATFORM_SLOTS), "every platform contributed posts"
    assert len(transport.calls_to(CREDIT_BALANCE_PATH)) == 1, "balance only once, before the first scan"

    again = deps.scraper.fetch("tiktok_trending")
    assert again.cached is True and again.credits_charged == 0


def test_fresh_scan_bypasses_the_request_cache(backend, store, settings, clock, sample_context_map):
    """"Refresh now" on the dashboard is a real pull: URLs fetched inside the last 48h go live again."""
    seed_context(backend, sample_context_map)
    deps, transport = make_scan_deps(store, settings, clock, youtube=False)
    first = run_paid_scan(deps)
    second = run_paid_scan(deps)             # not fresh: the Shorts feed repeats, so it comes from cache
    fresh = run_paid_scan(deps, fresh=True)  # the owner's Refresh now: everything live, cache or not
    assert first.credits_spent == 4 and second.credits_spent == 3 and fresh.credits_spent == 4
    shorts = [c for c in transport.calls if "/youtube/shorts/trending" in c.url]
    assert len(shorts) == 2, "served from cache once, then forced live"
    assert fresh.brief is not None and fresh.brief.kind == "paid_scan"


def test_four_scans_rotate_facebook_sources_and_increment_scan_index(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    deps, transport = make_scan_deps(store, settings, clock, youtube=False)
    thirds, fourths = [], []
    for _ in range(4):
        outcome = run_paid_scan(deps)
        assert outcome.brief is not None
        thirds.append(outcome.planned[2].endpoint_key)
        fourths.append(outcome.planned[3].endpoint_key)
        clock.advance(hours=48)
    assert thirds == ["facebook_page_reels", "facebook_group_posts", "facebook_page_reels", "facebook_group_posts"]
    assert fourths == ["youtube_shorts_trending"] * 4
    latest = store.get(store.paths.latest)
    assert latest["scan_index"] == 4 and latest["last_paid_scan_id"] == latest["scan_id"]
    assert len(transport.calls_to(CREDIT_BALANCE_PATH)) == 1


def test_scan_id_is_never_overwritten(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    deps, _ = make_scan_deps(store, settings, clock, youtube=False)
    first = run_paid_scan(deps).brief
    second = run_paid_scan(deps).brief
    assert first.scan_id == "2026-09-12" and second.scan_id == "2026-09-12-2"
    assert store.get(store.paths.scan("2026-09-12"))["generated_at"] == first.to_doc()["generated_at"]


def test_zero_credits_runs_free_sources_and_recap_only(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    deps, transport = make_scan_deps(store, settings, clock)
    assert run_paid_scan(deps).brief is not None
    store.update(store.paths.meta, {"sc_credits_remaining": 0})
    clock.advance(hours=48)
    before = len([c for c in transport.calls if c.url.startswith(SC_HOST)])
    outcome = run_paid_scan(deps)
    after = len([c for c in transport.calls if c.url.startswith(SC_HOST)])
    assert after == before, "0 credits → 0 paid calls"
    assert outcome.brief is not None and outcome.brief.kind == "offday_recap"
    assert "paid_scan_skipped_no_credits" in outcome.note
    assert any("googleapis" in c.url for c in transport.calls[-6:]) or any("reddit" in c.url for c in transport.calls[-6:])
    alerts = {a["alert_id"] for _, a in store.list(store.paths.notifications)}
    assert "scrapecreators_exhausted" in alerts


def test_missing_context_writes_alert_and_does_not_scrape(store, settings, clock):
    deps, transport = make_scan_deps(store, settings, clock)
    outcome = run_paid_scan(deps)
    assert outcome.brief is None and outcome.note == "context_missing"
    assert transport.calls == []
    assert store.get(store.paths.notification("context_critical")) is not None
