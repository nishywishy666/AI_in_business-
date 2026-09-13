import ast
import datetime as dt
from pathlib import Path

import pytest

from marketing_radar.cache import LocalCache
from marketing_radar.packets import UsageEvent, UsageSnapshot
from marketing_radar.services import get_stats
from marketing_radar.usage import build_snapshot, mark_stale, refresh_snapshot, sync_notifications
from tests.conftest import FROZEN_NOW, USER_ID

SPEC_TOP = {"schema_version", "checked_at", "stale", "scrapecreators", "gemini", "youtube_data_api", "free_sources", "alerts"}
SPEC_SC = {"remaining", "spent_last_scan", "spent_today", "reserve", "usable_now", "next_scan_estimated_cost",
           "transcripts_affordable", "status", "resets", "resets_at", "reset_note"}
SPEC_GEMINI = {"tier", "active_model", "active_quality", "quality_warning", "resets", "resets_at", "resets_in",
               "reset_note", "models"}
SPEC_MODEL = {"id", "label", "quality", "used_today", "daily_cap", "remaining", "status"}
SPEC_YT = {"daily_units_used", "daily_quota", "remaining", "status", "resets", "resets_at", "reset_note"}


def _credits(store, remaining):
    store.update(store.paths.meta, {"sc_credits_remaining": remaining})


def test_snapshot_shape_matches_spec_exactly(store, settings, clock):
    _credits(store, 82)
    doc = build_snapshot(store, settings, clock).to_doc()
    assert set(doc) == SPEC_TOP
    assert set(doc["scrapecreators"]) == SPEC_SC
    assert set(doc["gemini"]) == SPEC_GEMINI
    assert set(doc["gemini"]["models"][0]) == SPEC_MODEL
    assert set(doc["youtube_data_api"]) == SPEC_YT
    assert [s["id"] for s in doc["free_sources"]] == ["reddit", "google_trends"]
    assert doc["scrapecreators"]["resets"] is False and doc["scrapecreators"]["resets_at"] is None
    assert doc["gemini"]["resets_at"] == "2026-09-12T07:00:00Z" and doc["gemini"]["resets_in"] == "5h"
    assert doc["scrapecreators"]["usable_now"] == 67 and doc["scrapecreators"]["transcripts_affordable"] is True


@pytest.mark.parametrize("remaining,expected", [
    (None, set()), (60, set()), (25, {"scrapecreators_warning"}), (15, {"scrapecreators_warning"}),
    (2, {"scrapecreators_critical"}), (0, {"scrapecreators_exhausted"}),
])
def test_scrapecreators_alert_thresholds(store, settings, clock, remaining, expected):
    if remaining is not None:
        _credits(store, remaining)
    snap = build_snapshot(store, settings, clock)
    ids = {a.alert_id for a in snap.alerts if a.provider == "scrapecreators"}
    assert ids == expected
    for a in snap.alerts:
        if a.provider == "scrapecreators":
            assert a.resets is False and "do not reset" in a.message


def test_reserve_message_pauses_transcripts(store, settings, clock):
    _credits(store, 15)
    snap = build_snapshot(store, settings, clock)
    assert "Transcripts are paused" in snap.alerts[0].message and snap.scrapecreators.transcripts_affordable is False


def test_youtube_alerts(store, settings, clock):
    _credits(store, 90)

    def spend(units):
        e = UsageEvent(event_id=f"yt_{units}", provider="youtube_data_api", purpose="search", at=FROZEN_NOW,
                       pacific_date="2026-09-11", units=units)
        store.set(store.paths.usage_event(e.event_id), e.to_doc())

    spend(8500)
    snap = build_snapshot(store, settings, clock)
    assert snap.youtube_data_api.remaining == 1500 and snap.youtube_data_api.status == "low"
    assert {a.alert_id for a in snap.alerts} == {"youtube_data_api_warning"}
    spend(1500)
    snap = build_snapshot(store, settings, clock)
    assert snap.youtube_data_api.status == "exhausted"
    alert = next(a for a in snap.alerts if a.provider == "youtube_data_api")
    assert alert.severity == "exhausted" and alert.resets is True and "midnight Pacific" in alert.message


def test_one_alert_per_provider_severity_and_clearing(store, settings, clock):
    _credits(store, 10)
    refresh_snapshot(store, settings, clock)
    ids = [doc_id for doc_id, _ in store.list(store.paths.notifications)]
    assert ids == ["scrapecreators_warning"]
    created = store.get(store.paths.notification("scrapecreators_warning"))["created_at"]

    clock.advance(hours=1)
    refresh_snapshot(store, settings, clock)
    assert store.get(store.paths.notification("scrapecreators_warning"))["created_at"] == created

    _credits(store, 90)
    refresh_snapshot(store, settings, clock)
    assert store.list(store.paths.notifications) == []
    assert store.get(store.paths.usage_snapshot)["alerts"] == []


def test_sync_removes_stale_alert_docs(store, clock):
    store.set(store.paths.notification("old_warning"), {"alert_id": "old_warning"})
    sync_notifications(store, [], clock())
    assert store.list(store.paths.notifications) == []


def test_stale_flag_after_26h(settings):
    snap = UsageSnapshot(checked_at=FROZEN_NOW)
    assert mark_stale(snap, FROZEN_NOW + dt.timedelta(hours=25), settings).stale is False
    assert mark_stale(snap, FROZEN_NOW + dt.timedelta(hours=27), settings).stale is True


def test_get_stats_reads_cache_then_store_and_never_scrapes(store, settings, clock):
    cache = LocalCache(USER_ID, settings.cache_root)
    assert get_stats(store, cache, settings, clock=clock) is None
    _credits(store, 50)
    refresh_snapshot(store, settings, clock)
    doc = get_stats(store, cache, settings, clock=clock)
    assert doc["scrapecreators"]["remaining"] == 50 and doc["stale"] is False
    assert cache.stats_file.exists()


def test_acceptance_13_read_helpers_do_not_import_scrapers_or_gemini():
    services = Path(__file__).resolve().parents[1] / "marketing_radar" / "services"
    for file in ("brief.py", "stats.py", "overlord.py", "scripts.py"):
        tree = ast.parse((services / file).read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            for name in names:
                assert "scrapers" not in name and "gemini" not in name and "agent" not in name, (file, name)
