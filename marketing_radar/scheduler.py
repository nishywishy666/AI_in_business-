"""APScheduler registration inside the parent process (spec §3). No launchd, no Task Scheduler."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from .clock import parse_iso, utc_now
from .config import Settings
from .deps import build_deps
from .jobs.daily_pull import daily_pull
from .jobs.expire_drafts import expire_drafts
from .jobs.scan import ScanDeps, run_paid_scan

log = logging.getLogger(__name__)


def start_marketing_radar(user_id: str, *, settings: Settings | None = None, scheduler: Any = None,
                          deps: ScanDeps | None = None, start: bool = True) -> Any:
    """Register the daily pull, the 48h scan, and the draft expiry job. Returns the scheduler.

    Refuses to start without a user id (spec §18: "Parent userId missing → refuse to start scheduler").
    """
    if not user_id:
        raise ValueError("marketing_radar: parent userId is required to start the scheduler")
    settings = settings or Settings.from_env()
    deps = deps or build_deps(user_id, settings)
    if scheduler is None:
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler(timezone="UTC")

    scheduler.add_job(lambda: daily_pull(deps.store, deps.cache, deps.settings, deps.clock),
                      "cron", hour=0, minute=5, id="marketing_radar.daily_pull", replace_existing=True,
                      misfire_grace_time=3600)

    latest = deps.store.get(deps.store.paths.latest) or {}
    last_paid = parse_iso(latest.get("last_paid_scan_at"))
    interval = dt.timedelta(hours=settings.scan_interval_hours)
    now = utc_now()
    next_run = (last_paid + interval) if last_paid else now + dt.timedelta(minutes=1)
    if next_run < now:
        next_run = now + dt.timedelta(minutes=1)
    scheduler.add_job(lambda: run_paid_scan(deps), "interval", hours=settings.scan_interval_hours,
                      next_run_time=next_run, id="marketing_radar.scan", replace_existing=True,
                      misfire_grace_time=6 * 3600, coalesce=True)

    scheduler.add_job(lambda: expire_drafts(deps.store, deps.settings, deps.clock), "cron", hour=1, minute=0,
                      id="marketing_radar.expire_drafts", replace_existing=True, misfire_grace_time=3600)

    if start and not getattr(scheduler, "running", False):
        scheduler.start()
    log.info("marketing_radar scheduler registered for user %s (next scan %s)", user_id, next_run.isoformat())
    return scheduler
