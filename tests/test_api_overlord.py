import pytest

from marketing_radar.agent import FakeGeminiTransport, RateLimited
from marketing_radar.jobs.scan import run_paid_scan
from marketing_radar.services import get_marketing_summary
from marketing_radar.services.api import RadarApi
from tests.conftest import make_scan_deps, seed_context


@pytest.fixture
def fresh(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    deps, transport = make_scan_deps(store, settings, clock)
    return deps, transport


@pytest.fixture
def scanned(fresh):
    deps, transport = fresh
    brief = run_paid_scan(deps).brief
    return deps, transport, brief


def test_empty_state_before_first_scan(fresh):
    deps, _ = fresh
    api = RadarApi(deps)
    assert api.brief()[0] == 404 and api.stats()[0] == 404
    assert get_marketing_summary(deps.store, deps.cache, deps.settings, clock=deps.clock) is None


def test_full_flow_through_api(scanned):
    deps, _, brief = scanned
    api = RadarApi(deps)
    status, body = api.brief()
    assert status == 200 and body["scan_id"] == brief.scan_id
    assert api.brief(brief.scan_id)[1]["scan_id"] == brief.scan_id
    assert api.brief("2020-01-01")[0] == 404
    post_id = brief.film_this.post_id
    assert api.post(post_id)[1]["post_id"] == post_id and api.post("tt_nope")[0] == 404
    assert api.stats()[1]["scrapecreators"]["remaining"] == 95

    status, liked = api.like(post_id)
    assert status == 200 and len(liked["angles"]) == 3
    assert api.like("tt_nope")[0] == 404
    status, script = api.choose_angle(post_id, 2)
    assert status == 200 and script["status"] == "draft"
    assert api.choose_angle(post_id, 9)[0] == 400
    assert api.scripts("saved")[1] == []
    status, saved = api.save_script(script["script_id"])
    assert status == 200 and saved["status"] == "saved" and saved["expires_at"] is None
    assert api.save_script("missing")[0] == 404
    assert [s["script_id"] for s in api.scripts("saved")[1]] == [script["script_id"]]

    status, chat = api.chat("what should I post tomorrow?", thread_id="ui")
    assert status == 200 and chat["actions"] == ["recommend_tomorrow"] and chat["scan_id"] == brief.scan_id
    assert api.chat("   ")[0] == 400


def test_gemini_exhausted_maps_to_503(scanned):
    deps, _, brief = scanned
    models = ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-2.0-flash-lite"]
    deps.gemini.transport = FakeGeminiTransport(scripts={m: [RateLimited("429")] for m in models})
    status, body = RadarApi(deps).like(brief.film_this.post_id)
    assert status == 503 and body["error"] == "gemini_exhausted" and body["resets_at"] == "2026-09-12T07:00:00Z"
    assert RadarApi(deps).brief()[0] == 200, "brief still served from cache"


def test_backend_failure_maps_to_503(scanned):
    deps, _, _ = scanned
    deps.cache.clear()

    def boom(*args, **kwargs):
        raise ConnectionError("firestore down")

    deps.store.backend.get = boom
    status, body = RadarApi(deps).brief()
    assert status == 503 and body["error"] == "marketing data unavailable"


def test_overlord_summary_cites_scan_and_never_scrapes(scanned):
    deps, transport, brief = scanned
    calls_before = len(transport.calls)
    gemini_before = len(deps.gemini.transport.calls)
    summary = get_marketing_summary(deps.store, deps.cache, deps.settings, clock=deps.clock)
    assert summary["scan_id"] == brief.scan_id and brief.scan_id in summary["source_note"]
    assert summary["film_this"]["post_id"] == brief.film_this.post_id and summary["film_this"]["why"]
    assert len(summary["niche"]) == 3 and summary["niche"][0]["likes"] is not None
    assert summary["stats"]["scrapecreators_remaining"] == 95 and summary["stats"]["scrapecreators_resets"] is False
    assert summary["stats"]["gemini_resets_at"] == "2026-09-12T07:00:00Z"
    assert len(transport.calls) == calls_before and len(deps.gemini.transport.calls) == gemini_before
