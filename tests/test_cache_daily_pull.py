import datetime as dt

import pytest

from marketing_radar.cache import LocalCache
from marketing_radar.jobs.daily_pull import ContextMissing, daily_pull
from marketing_radar.packets import BriefRef, ScanBrief, UsageSnapshot
from tests.conftest import FROZEN_NOW, USER_ID


@pytest.fixture
def cache(settings):
    return LocalCache(USER_ID, settings.cache_root)


def _seed_context(backend, sample_context_map):
    backend.set(f"users/{USER_ID}/context", sample_context_map)


def _seed_brief(store, scan_id="2026-09-10"):
    brief = ScanBrief(scan_id=scan_id, generated_at=FROZEN_NOW - dt.timedelta(days=2),
                      **{"global": [BriefRef(post_id="tt_1", packet_ref="posts/tt_1")]})
    store.set(store.paths.scan(scan_id), brief.to_doc())
    store.set(store.paths.latest, {"scan_id": scan_id, "scan_index": 1})
    store.set(store.paths.usage_snapshot, UsageSnapshot(checked_at=FROZEN_NOW).to_doc())
    return brief


def test_first_run_caches_context_only(backend, store, settings, clock, cache, sample_context_map):
    _seed_context(backend, sample_context_map)
    bundle = daily_pull(store, cache, settings, clock)
    assert bundle.context.niche == "Home fitness for busy parents"
    assert bundle.brief is None and bundle.scan_id is None
    assert cache.is_fresh(clock())
    assert store.get(store.paths.context_cache)["niche"] == "Home fitness for busy parents"


def test_missing_context_raises(store, settings, clock, cache):
    with pytest.raises(ContextMissing):
        daily_pull(store, cache, settings, clock)


def test_acceptance_2_pull_is_noop_when_fresh_and_refetches_when_torn(
        backend, store, settings, clock, cache, sample_context_map):
    _seed_context(backend, sample_context_map)
    _seed_brief(store)
    reads = {"n": 0}
    original_get = backend.get

    def counting_get(path):
        reads["n"] += 1
        return original_get(path)

    backend.get = counting_get
    first = daily_pull(store, cache, settings, clock)
    assert first.brief.scan_id == "2026-09-10"
    assert cache.read_part("global") == [{"post_id": "tt_1", "packet_ref": "posts/tt_1"}]
    reads["n"] = 0

    second = daily_pull(store, cache, settings, clock)
    assert reads["n"] == 0, "same day + all files present must not touch Firestore"
    assert second.brief.scan_id == "2026-09-10"

    cache.part_file("global").unlink()
    daily_pull(store, cache, settings, clock)
    assert reads["n"] > 0, "a torn cache must re-hydrate"
    assert cache.files_present()

    clock.advance(days=1)
    reads["n"] = 0
    daily_pull(store, cache, settings, clock)
    assert reads["n"] > 0, "a new calendar day pulls again"


def test_expansion_only_when_hashtags_empty_and_never_writes_parent(backend, store, settings, clock, cache):
    backend.set(f"users/{USER_ID}/context", {"niche": "vegan meal prep", "keywords": "cheap dinners"})
    calls = []

    def expander(profile):
        calls.append(profile.niche)
        return ["veganmealprep", "plantbased", "mealprepsunday"]

    bundle = daily_pull(store, cache, settings, clock, expander=expander)
    assert bundle.context.hashtags == ["veganmealprep", "plantbased", "mealprepsunday"]
    assert bundle.context.hashtags_expanded_by == "gemini"
    assert backend.get(f"users/{USER_ID}/context") == {"niche": "vegan meal prep", "keywords": "cheap dinners"}

    clock.advance(days=1)
    bundle2 = daily_pull(store, cache, settings, clock, expander=expander)
    assert calls == ["vegan meal prep"], "cached expansion must be reused, not re-requested"
    assert bundle2.context.hashtags == bundle.context.hashtags


def test_expansion_falls_back_locally_when_gemini_fails(backend, store, settings, clock, cache):
    backend.set(f"users/{USER_ID}/context", {"niche": "vegan meal prep"})

    def broken(profile):
        raise RuntimeError("429")

    bundle = daily_pull(store, cache, settings, clock, expander=broken)
    assert bundle.context.hashtags_expanded_by == "local"
    assert "veganmealprep" in bundle.context.hashtags


def test_write_through_brief_updates_meta(store, settings, clock, cache, backend, sample_context_map):
    _seed_context(backend, sample_context_map)
    daily_pull(store, cache, settings, clock)
    cache.write_brief(ScanBrief(scan_id="2026-09-12", generated_at=FROZEN_NOW))
    assert cache.read_meta()["scan_id"] == "2026-09-12"
    assert cache.is_fresh(clock())
    assert cache.read().brief.scan_id == "2026-09-12"
