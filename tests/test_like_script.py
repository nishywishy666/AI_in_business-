import datetime as dt

import pytest

from marketing_radar.agent import FakeGeminiTransport
from marketing_radar.agent.like import PostNotFound, choose_angle, like_post
from marketing_radar.jobs.expire_drafts import expire_drafts
from marketing_radar.jobs.scan import run_paid_scan
from marketing_radar.packets import Script
from marketing_radar.scrapers import LIKE_ENDPOINTS
from marketing_radar.services import ScriptNotFound, delete_script, get_script, list_scripts, save_script
from tests.conftest import FROZEN_NOW, USER_ID, make_scan_deps, seed_context

TRANSCRIPT_PATHS = [e.path for e in LIKE_ENDPOINTS.values()]


@pytest.fixture
def scanned(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    deps, transport = make_scan_deps(store, settings, clock)
    brief = run_paid_scan(deps).brief
    return deps, transport, brief


def _transcript_calls(transport):
    return [c for c in transport.calls if any(c.path == p for p in TRANSCRIPT_PATHS)]


def _first(store, platform, brief):
    for post_id in brief.all_post_ids():
        if post_id.startswith({"tiktok": "tt_", "instagram": "ig_", "youtube": "yt_", "facebook": "fb_"}[platform]):
            return post_id
    for post_id, _ in store.list(store.paths.posts):
        if post_id.startswith({"tiktok": "tt_", "instagram": "ig_", "youtube": "yt_", "facebook": "fb_"}[platform]):
            return post_id
    raise AssertionError(f"no {platform} post")


def test_like_uses_one_transcript_credit_when_affordable(scanned, store):
    deps, transport, brief = scanned
    post_id = _first(store, "tiktok", brief)
    result = like_post(deps, post_id)
    assert result.transcript_source == "scrapecreators" and len(_transcript_calls(transport)) == 1
    assert len(result.angles) == 3 and result.breakdown
    post = store.get(store.paths.post(post_id))
    assert post["liked"] is True and post["angles"] == result.angles and post["transcript"]
    assert "use_ai_as_fallback" not in _transcript_calls(transport)[0].params

    again = like_post(deps, post_id)
    assert again.transcript_source == "cached" and len(_transcript_calls(transport)) == 1


def test_acceptance_11_like_at_reserve_skips_transcript(scanned, store):
    deps, transport, brief = scanned
    store.update(store.paths.meta, {"sc_credits_remaining": 15})
    result = like_post(deps, _first(store, "tiktok", brief))
    assert result.transcript_source == "caption_only"
    assert _transcript_calls(transport) == []
    assert len(result.angles) == 3


def test_like_skips_transcript_over_120s(scanned, store):
    deps, transport, brief = scanned
    post_id = _first(store, "instagram", brief)
    store.update(store.paths.post(post_id), {"duration_sec": 121})
    assert like_post(deps, post_id).transcript_source == "caption_only"
    assert _transcript_calls(transport) == []


def test_youtube_uses_free_transcript_never_credits(scanned, store):
    deps, transport, brief = scanned
    post_id = "yt_ytfree00001"
    assert store.get(store.paths.post(post_id)) is not None
    result = like_post(deps, post_id)
    assert result.transcript_source == "youtube_free" and _transcript_calls(transport) == []
    assert deps.youtube_transcripts.calls == ["ytfree00001"]


def test_transcript_failure_continues_with_caption(scanned, store):
    deps, transport, brief = scanned
    transport.fail_next("/v1/tiktok/video/transcript", 500)
    result = like_post(deps, _first(store, "tiktok", brief))
    assert result.transcript_source == "caption_only" and len(result.angles) == 3


def test_like_unknown_post_raises(scanned):
    deps, _, _ = scanned
    with pytest.raises(PostNotFound):
        like_post(deps, "tt_nope")


def test_like_rejects_fewer_than_three_angles(scanned, store):
    deps, _, brief = scanned
    deps.gemini.transport = FakeGeminiTransport(default='{"breakdown": "x", "angles": ["only one"]}')
    with pytest.raises(ValueError):
        like_post(deps, _first(store, "tiktok", brief))


def test_choose_angle_creates_draft_and_save_clears_expiry(scanned, store, clock):
    deps, _, brief = scanned
    post_id = _first(store, "tiktok", brief)
    like_post(deps, post_id)
    script = choose_angle(deps, post_id, 1)
    assert script.status == "draft" and script.expires_at == FROZEN_NOW + dt.timedelta(days=14)
    assert script.script and script.filming_guide and script.caption
    post = store.get(store.paths.post(post_id))
    assert post["script_id"] == script.script_id and post["chosen_angle"] == post["angles"][1]
    assert list_scripts(store, status="saved") == []

    saved = save_script(store, script.script_id, clock=clock)
    assert saved.status == "saved" and saved.expires_at is None and saved.saved_at == FROZEN_NOW
    assert [s["script_id"] for s in list_scripts(store, status="saved")] == [script.script_id]

    regenerated = choose_angle(deps, post_id, "a custom angle typed by the user")
    assert regenerated.script_id != script.script_id and regenerated.status == "draft"
    assert store.get(store.paths.post(post_id))["script_id"] == regenerated.script_id
    assert get_script(store, script.script_id).status == "saved", "old saved script untouched"
    with pytest.raises(ValueError):
        choose_angle(deps, post_id, 7)


def test_acceptance_12_expire_deletes_old_drafts_keeps_saved(scanned, store, clock, settings):
    deps, _, brief = scanned
    post_id = _first(store, "tiktok", brief)
    like_post(deps, post_id)
    draft = choose_angle(deps, post_id, 0)
    kept = choose_angle(deps, post_id, 2)
    save_script(store, kept.script_id, clock=clock)
    old_saved = Script(script_id="old", user_id=USER_ID, post_id=post_id, status="saved", script="s",
                       created_at=FROZEN_NOW - dt.timedelta(days=90))
    store.set(store.paths.script("old"), old_saved.to_doc())

    clock.advance(days=13)
    assert expire_drafts(store, settings, clock).deleted_scripts == []
    clock.advance(days=2)
    result = expire_drafts(store, settings, clock)
    assert result.deleted_scripts == [draft.script_id]
    assert {s["script_id"] for s in list_scripts(store, status=None)} == {kept.script_id, "old"}
    with pytest.raises(ScriptNotFound):
        get_script(store, draft.script_id)


def test_expire_clears_dangling_post_pointer_and_prunes_cache(scanned, store, clock, settings):
    deps, _, brief = scanned
    post_id = _first(store, "tiktok", brief)
    like_post(deps, post_id)
    draft = choose_angle(deps, post_id, 0)
    cache_entries = len(store.list(store.paths.scrape_cache))
    assert cache_entries > 0
    clock.advance(days=15)
    result = expire_drafts(store, settings, clock)
    assert result.deleted_scripts == [draft.script_id] and result.pruned_cache == cache_entries
    assert store.get(store.paths.post(post_id))["script_id"] is None


def test_delete_script_clears_pointer(scanned, store):
    deps, _, brief = scanned
    post_id = _first(store, "tiktok", brief)
    like_post(deps, post_id)
    script = choose_angle(deps, post_id, 0)
    delete_script(store, script.script_id)
    assert store.get(store.paths.post(post_id))["script_id"] is None
    with pytest.raises(ScriptNotFound):
        delete_script(store, script.script_id)
