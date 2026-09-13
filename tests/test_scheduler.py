import datetime as dt

import pytest

from marketing_radar.scheduler import start_marketing_radar
from tests.conftest import FROZEN_NOW, USER_ID, make_scan_deps, seed_context


class FakeScheduler:
    def __init__(self):
        self.jobs = {}
        self.running = False

    def add_job(self, func, trigger, **kwargs):
        self.jobs[kwargs["id"]] = (func, trigger, kwargs)

    def start(self):
        self.running = True


def test_refuses_without_user_id(settings):
    with pytest.raises(ValueError):
        start_marketing_radar("", settings=settings, scheduler=FakeScheduler())


def test_acceptance_14_registers_jobs_with_same_call_on_any_platform(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    deps, _ = make_scan_deps(store, settings, clock)
    scheduler = FakeScheduler()
    result = start_marketing_radar(USER_ID, settings=settings, scheduler=scheduler, deps=deps)
    assert result is scheduler and scheduler.running
    assert set(scheduler.jobs) == {"marketing_radar.daily_pull", "marketing_radar.scan", "marketing_radar.expire_drafts"}
    _, trigger, kwargs = scheduler.jobs["marketing_radar.scan"]
    assert trigger == "interval" and kwargs["hours"] == 48
    _, trigger, kwargs = scheduler.jobs["marketing_radar.daily_pull"]
    assert trigger == "cron" and (kwargs["hour"], kwargs["minute"]) == (0, 5)


def test_next_scan_is_48h_after_last_paid_scan(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    deps, _ = make_scan_deps(store, settings, clock)
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=10)
    store.set(store.paths.latest, {"scan_id": "x", "last_paid_scan_at": future.strftime("%Y-%m-%dT%H:%M:%SZ")})
    scheduler = FakeScheduler()
    start_marketing_radar(USER_ID, settings=settings, scheduler=scheduler, deps=deps, start=False)
    next_run = scheduler.jobs["marketing_radar.scan"][2]["next_run_time"]
    assert abs((next_run - (future + dt.timedelta(hours=48))).total_seconds()) < 2
    assert scheduler.running is False


def test_scan_job_runs_the_pipeline(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    deps, _ = make_scan_deps(store, settings, clock)
    scheduler = FakeScheduler()
    start_marketing_radar(USER_ID, settings=settings, scheduler=scheduler, deps=deps, start=False)
    scheduler.jobs["marketing_radar.scan"][0]()
    assert store.get(store.paths.latest)["scan_id"] == "2026-09-12"
    scheduler.jobs["marketing_radar.daily_pull"][0]()
    scheduler.jobs["marketing_radar.expire_drafts"][0]()
