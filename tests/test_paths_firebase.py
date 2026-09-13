import datetime as dt

import pytest

from marketing_radar.clock import next_pacific_midnight_utc, pacific_date
from marketing_radar.config import DEFAULT_LADDER, Settings, parse_ladder_json
from marketing_radar.db import MemoryBackend, NamespaceViolation, RadarPaths, RadarStore
from tests.conftest import USER_ID


def test_paths_are_all_inside_namespace():
    p = RadarPaths("u1")
    for path in [p.meta, p.latest, p.context_cache, p.usage_snapshot, p.usage_event("e"),
                 p.gemini_daily("2026-09-12"), p.scan("s"), p.post("tt_1"), p.script("x"),
                 p.scrape_cache_entry("h"), p.playbook_entry("p"), p.notification("a"),
                 p.chat_message("t", "m")]:
        assert p.is_inside_namespace(path), path
        assert len(path.split("/")) % 2 == 0, f"not a document path: {path}"
    assert not p.is_inside_namespace("users/u1/context")
    assert not p.is_inside_namespace("users/u1/marketingRadarX/meta")


def test_acceptance_1_write_outside_namespace_raises(store: RadarStore):
    with pytest.raises(NamespaceViolation):
        store.set(f"users/{USER_ID}/settings", {"x": 1})
    with pytest.raises(NamespaceViolation):
        store.set(f"users/{USER_ID}/context", {"niche": "hacked"})
    with pytest.raises(NamespaceViolation):
        store.delete("users/other/marketingRadar/latest")
    store.set(store.paths.latest, {"scan_id": "2026-09-12"})
    assert store.get(store.paths.latest) == {"scan_id": "2026-09-12"}


def test_context_is_readable_but_not_writable(backend: MemoryBackend, store: RadarStore):
    backend.set(f"users/{USER_ID}/context", {"niche": "fitness"})
    assert store.read_context() == {"niche": "fitness"}
    assert store.get(store.context_path) == {"niche": "fitness"}
    with pytest.raises(NamespaceViolation):
        store.get("users/other/context")


def test_memory_backend_list_filters_orders_and_limits(store: RadarStore):
    for i, score in enumerate([0.2, 0.9, 0.5]):
        store.set(store.paths.post(f"tt_{i}"), {"final": score, "platform": "tiktok"})
    store.set(store.paths.post("ig_9"), {"final": 0.7, "platform": "instagram"})
    store.set(store.paths.scan("2026-09-12"), {"kind": "paid_scan"})  # sibling collection, must not leak

    rows = store.list(store.paths.posts, order_by="final", descending=True)
    assert [doc_id for doc_id, _ in rows] == ["tt_1", "ig_9", "tt_2", "tt_0"]
    rows = store.list(store.paths.posts, where=[("platform", "==", "tiktok")], order_by="final", limit=2)
    assert [doc_id for doc_id, _ in rows] == ["tt_0", "tt_2"]


def test_merge_update_keeps_existing_fields(store: RadarStore):
    store.set(store.paths.post("tt_1"), {"likes": 1, "liked": False})
    store.update(store.paths.post("tt_1"), {"liked": True})
    assert store.get(store.paths.post("tt_1")) == {"likes": 1, "liked": True}


def test_pacific_day_helpers():
    now = dt.datetime(2026, 9, 12, 2, 0, tzinfo=dt.timezone.utc)  # 19:00 PDT on 2026-09-11
    assert pacific_date(now) == "2026-09-11"
    assert next_pacific_midnight_utc(now) == dt.datetime(2026, 9, 12, 7, 0, tzinfo=dt.timezone.utc)


def test_ladder_override_and_defaults():
    assert parse_ladder_json(None) == DEFAULT_LADDER
    rungs = parse_ladder_json('[{"ids":["gemini-3-flash"],"quality":"high","daily_cap":1500}]')
    assert rungs[0].daily_cap == 1500 and rungs[0].ids == ("gemini-3-flash",)
    s = Settings.from_env({"CONTEXT_PATH": "tenants/{userId}/profile", "MARKETING_RADAR_OFFLINE": "1"})
    assert s.offline and s.context_path("abc") == "tenants/abc/profile"
