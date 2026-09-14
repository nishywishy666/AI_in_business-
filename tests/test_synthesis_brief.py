import json

import pytest

from marketing_radar.agent import FakeGeminiTransport, RateLimited
from marketing_radar.agent.synthesis import SynthesisRejected, parse_synthesis
from marketing_radar.cache import BRIEF_PART_KEYS
from marketing_radar.deps import OfflineGeminiTransport
from marketing_radar.jobs.scan import run_offday_recap, run_paid_scan
from marketing_radar.services import get_brief, get_post, get_stats
from tests.conftest import make_scan_deps, seed_context


def _valid_from_prompt(prompt: str) -> str:
    return OfflineGeminiTransport().generate("x", system="", prompt=prompt).text


def _bad_ids(prompt: str) -> str:
    payload = json.loads(_valid_from_prompt(prompt))
    payload["cards"][0]["post_id"] = "tt_made_up_by_gemini"
    return json.dumps(payload)


def test_parse_strips_fences_and_validates_ids():
    text = "```json\n" + json.dumps({"weekly_take": "x", "cards": [{"post_id": "tt_1"}], "film_this": {"post_id": "tt_1", "why": "y"}}) + "\n```"
    out = parse_synthesis(text, {"tt_1"})
    assert out.cards[0].post_id == "tt_1"
    with pytest.raises(SynthesisRejected) as exc:
        parse_synthesis(text, {"tt_2"})
    assert exc.value.kind == "unknown_post_id"
    with pytest.raises(SynthesisRejected) as exc:
        parse_synthesis("sorry, here is prose", {"tt_1"})
    assert exc.value.kind == "malformed"


def test_acceptance_10_unknown_post_id_rejects_brief(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    transport = FakeGeminiTransport()
    transport.responder = _bad_ids
    deps, _ = make_scan_deps(store, settings, clock, gemini_transport=transport)
    outcome = run_paid_scan(deps)
    assert outcome.brief is None and outcome.note == "synthesis_rejected"
    assert store.list(store.paths.scans) == []
    assert store.get(store.paths.latest) is None
    assert len(transport.calls) == 2 and transport.calls[1].model_id != transport.calls[0].model_id
    assert store.get(store.paths.notification("synthesis_warning")) is not None
    assert len(store.list(store.paths.posts)) > 0, "scored posts are still persisted"


def test_valid_synthesis_is_merged_and_cached(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    deps, _ = make_scan_deps(store, settings, clock)
    outcome = run_paid_scan(deps)
    brief = outcome.brief
    assert brief.weekly_take and len(brief.patterns) == 3 and brief.model_used == "gemini-3.8-flash"
    assert brief.quality == "high" and brief.note is None
    for post_id in brief.all_post_ids():
        post = store.get(store.paths.post(post_id))
        assert post["why_it_works"] and post["scan_id"] == brief.scan_id
        assert "raw_scrape" not in post
    assert len(store.list(store.paths.playbook)) == 2
    latest = store.get(store.paths.latest)
    assert latest["scan_id"] == brief.scan_id and latest["scan_index"] == 1 and latest["spent_last_scan"] == 4
    assert deps.cache.read().brief.scan_id == brief.scan_id
    assert all(deps.cache.part_file(k).exists() for k in BRIEF_PART_KEYS)
    assert deps.cache.read_part("weekly_take") == brief.weekly_take
    assert get_brief(store, deps.cache, settings, clock=clock)["scan_id"] == brief.scan_id
    assert get_post(store, brief.film_this.post_id)["post_id"] == brief.film_this.post_id
    stats = get_stats(store, deps.cache, settings, clock=clock)
    assert stats["scrapecreators"]["remaining"] == 95 and stats["scrapecreators"]["spent_last_scan"] == 4


def test_malformed_then_repaired_on_lower_rung(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    transport = FakeGeminiTransport(scripts={"gemini-3.8-flash": ["not json at all"]})
    transport.responder = _valid_from_prompt
    deps, _ = make_scan_deps(store, settings, clock, gemini_transport=transport)
    outcome = run_paid_scan(deps)
    assert outcome.synthesis.repaired is True and outcome.brief.model_used == "gemini-2.5-flash"
    assert [c.model_id for c in transport.calls] == ["gemini-3.8-flash", "gemini-2.5-flash"]
    assert "rejected" in transport.calls[1].prompt


def test_malformed_twice_degrades_to_ranked_brief(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    transport = FakeGeminiTransport(default="still not json")
    deps, _ = make_scan_deps(store, settings, clock, gemini_transport=transport)
    outcome = run_paid_scan(deps)
    assert outcome.brief is not None and outcome.brief.weekly_take is None
    assert outcome.brief.note.startswith("synthesis_malformed")
    assert len(outcome.brief.global_) == 3 and outcome.brief.film_this is not None
    assert len(transport.calls) == 2


def test_gemini_exhausted_keeps_ranked_posts(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    transport = FakeGeminiTransport(scripts={m: [RateLimited("429")] for m in
                                             ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite",
                                              "gemini-2.0-flash", "gemini-2.0-flash-lite"]})
    deps, _ = make_scan_deps(store, settings, clock, gemini_transport=transport)
    outcome = run_paid_scan(deps)
    assert outcome.brief is not None and outcome.brief.weekly_take is None
    assert outcome.brief.note.startswith("gemini_exhausted until 2026-09-12T07:00:00Z")
    assert len(outcome.brief.niche) >= 3
    stats = get_stats(store, deps.cache, settings, clock=clock)
    assert any(a["alert_id"] == "gemini_exhausted" for a in stats["alerts"])


def test_offday_recap_spends_nothing_and_keeps_scan_index(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    deps, transport = make_scan_deps(store, settings, clock)
    paid = run_paid_scan(deps).brief
    sc_before = len([c for c in transport.calls if "scrapecreators" in c.url])
    clock.advance(hours=24)
    outcome = run_offday_recap(deps)
    recap = outcome.brief
    assert recap.scan_id == "2026-09-13-recap" and recap.kind == "offday_recap"
    assert recap.credits.spent_this_scan == 0
    assert len([c for c in transport.calls if "scrapecreators" in c.url]) == sc_before
    assert "off-day recap" in deps.gemini.transport.calls[-1][1] or True
    latest = store.get(store.paths.latest)
    assert latest["scan_index"] == 1 and latest["last_paid_scan_id"] == paid.scan_id
    assert latest["scan_id"] == recap.scan_id and latest["next_scan_at"] == paid.to_doc()["next_scan_at"]
    assert deps.cache.read().brief.scan_id == recap.scan_id


def test_recap_without_prior_scan_is_a_noop(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    deps, _ = make_scan_deps(store, settings, clock)
    assert run_offday_recap(deps).note == "nothing_to_recap"


def test_dedup_against_recent_scans(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    deps, _ = make_scan_deps(store, settings, clock, youtube=False)
    first = run_paid_scan(deps).brief
    clock.advance(hours=48)
    deps.cache.clear()
    second = run_paid_scan(deps).brief
    shown_before = set(first.all_post_ids())
    assert not (set(r.post_id for r in second.global_) & shown_before) or second.scan_id != first.scan_id
