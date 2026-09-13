import pytest

from marketing_radar.context import parse_context
from marketing_radar.jobs.scan import plan_calls, run_paid_scan
from marketing_radar.scrapers import CREDIT_BALANCE_PATH
from tests.conftest import FROZEN_NOW, USER_ID, make_scan_deps, seed_context

SC_HOST = "https://api.scrapecreators.com"


@pytest.fixture
def context(sample_context_map):
    return parse_context(sample_context_map, user_id=USER_ID, source_path="x", now=FROZEN_NOW)


def _third(context, index, settings, credits=100, **kw):
    calls = plan_calls(index, context, credits, settings, **kw)
    return [c.endpoint_key for c in calls]


def test_acceptance_7_call_3_alternates_per_table(context, settings):
    assert _third(context, 0, settings) == ["tiktok_trending", "instagram_hashtag", "facebook_page_reels"]
    assert _third(context, 1, settings) == ["tiktok_hashtag", "instagram_reels_trending", "youtube_shorts_trending"]
    assert _third(context, 2, settings) == ["tiktok_keyword", "instagram_hashtag", "facebook_group_posts"]
    assert _third(context, 3, settings) == ["tiktok_trending", "instagram_reels_trending", "youtube_shorts_trending"]
    assert _third(context, 4, settings) == _third(context, 0, settings)
    assert _third(context, 1, settings, free_youtube_covers_shorts=True)[2] == "tiktok_keyword"


def test_missing_facebook_urls_never_call_facebook(context, settings):
    no_fb = context.model_copy(update={"facebook_page_urls": [], "facebook_group_urls": []})
    for index in range(8):
        keys = _third(no_fb, index, settings)
        assert not any(k.startswith("facebook") for k in keys), (index, keys)
    assert _third(no_fb, 0, settings)[2] == "youtube_shorts_trending"
    assert _third(no_fb, 2, settings)[2] == "youtube_shorts_trending"
    page_only = context.model_copy(update={"facebook_group_urls": []})
    assert _third(page_only, 2, settings)[2] == "facebook_page_reels"


def test_niche_params_come_from_context(context, settings):
    calls = plan_calls(0, context, 100, settings)
    assert calls[1].params == {"hashtag": "homeworkout"}
    calls = plan_calls(2, context, 100, settings)
    assert calls[0].params == {"query": "home workout"}
    assert calls[2].params["url"].startswith("https://www.facebook.com/groups/")


def test_acceptance_8_credit_shedding(context, settings):
    assert len(plan_calls(0, context, 100, settings)) == 3
    assert len(plan_calls(0, context, 16, settings)) == 3
    assert [c.endpoint_key for c in plan_calls(0, context, 15, settings)] == ["tiktok_trending", "instagram_hashtag"]
    assert [c.endpoint_key for c in plan_calls(0, context, 2, settings)] == ["tiktok_trending", "instagram_hashtag"]
    assert [c.endpoint_key for c in plan_calls(0, context, 1, settings)] == ["tiktok_trending"]
    assert plan_calls(0, context, 0, settings) == []
    assert len(plan_calls(0, context, None, settings)) == 3, "unknown balance does not block the scan"


def test_acceptance_3_paid_scan_issues_at_most_three_live_calls(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    deps, transport = make_scan_deps(store, settings, clock, youtube=False)
    outcome = run_paid_scan(deps)
    sc_calls = [c for c in transport.calls if c.url.startswith(SC_HOST) and CREDIT_BALANCE_PATH not in c.url]
    assert len(sc_calls) == 3 and outcome.live_calls == 3 and outcome.credits_spent == 3
    assert outcome.brief is not None and outcome.brief.kind == "paid_scan"
    assert len(transport.calls_to(CREDIT_BALANCE_PATH)) == 1, "balance only once, before the first scan"

    again = deps.scraper.fetch("tiktok_trending")
    assert again.cached is True and again.credits_charged == 0


def test_four_scans_rotate_and_increment_scan_index(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    deps, transport = make_scan_deps(store, settings, clock, youtube=False)
    thirds = []
    for _ in range(4):
        outcome = run_paid_scan(deps)
        assert outcome.brief is not None
        thirds.append(outcome.planned[2].endpoint_key)
        clock.advance(hours=48)
    assert thirds == ["facebook_page_reels", "youtube_shorts_trending", "facebook_group_posts", "youtube_shorts_trending"]
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
