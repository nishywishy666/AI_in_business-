"""JSON API for the dashboard page (plan 0005). Same origin as the page, so no CORS.

    GET  /api/dashboard/bootstrap?period=today     everything the page needs in one call
    GET  /api/dashboard/analytics?period=week      tiles for the period selector
    GET  /api/dashboard/calls[/{call_id}]          call log + transcript
    POST /api/dashboard/callbacks/{id}/status      {status}
    POST /api/dashboard/gaps/{key}/review          {status: approved|dismissed, answer?}
    POST /api/dashboard/settings                   {notifPrefs?, packetConfirmed?}
    GET  /api/dashboard/trends                     UI-shaped trend cards
    POST /api/dashboard/trends/refresh             re-read the brief now, bypassing the daily cache
    POST /api/dashboard/trends/{post_id}/save      Like if needed → angle 0 → saved script
    POST /api/overlord/ask                         {question}
    GET|POST /api/jobs/{name}                      Vercel Cron targets (Bearer CRON_SECRET)
    /api/marketing/*                               marketing_radar.services.api.fastapi_router
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from services.voice.tools import BusinessReader

from . import analytics as an
from . import overlord, present
from .marketing import MarketingHub
from .records import RecordSource, ReviewRecord
from .settings import DashboardSettings

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
JOB_NAMES = ("marketing-scan", "marketing-daily-pull", "marketing-expire", "calendar-retry")


@dataclass
class DashboardContext:
    settings: DashboardSettings
    source: RecordSource
    reader: BusinessReader
    business_id: str
    marketing: MarketingHub
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(UTC)
    overlord_transport: Callable[[], overlord.OverlordTransport | None] = lambda: None
    calendar_retry: Callable[[dt.datetime], int] | None = None
    cron_secret: str | None = field(default_factory=lambda: os.environ.get("CRON_SECRET") or None)

    # ---- composed reads ------------------------------------------------------------------------
    def analytics(self, period: str, *, now: dt.datetime | None = None, snapshot=None,
                  start_date: dt.date | None = None, end_date: dt.date | None = None) -> dict:
        now = now or self.clock()
        snapshot = snapshot or self.source.load()
        raw = an.compute(snapshot, self.settings, period_key=period, now=now,
                         start_date=start_date, end_date=end_date)
        return present.analytics_payload(raw, self.settings, now=now)

    def bootstrap(self, period: str) -> dict:
        now = self.clock()
        snapshot = self.source.load()
        raw = an.compute(snapshot, self.settings, period_key=period, now=now)
        raw_today = raw if raw["period"]["key"] == "today" else an.compute(snapshot, self.settings, period_key="today", now=now)
        trends = self.marketing.trends()
        setup = present.setup_payload(self.reader, self.business_id, self.settings, now=now)
        chart = an.daily_series(snapshot, self.settings, now=now)
        chart30 = an.daily_series(snapshot, self.settings, now=now, days=30)  # the 7D/30D toggle's other half
        gaps = present.gap_rows(raw, self.settings, now=now)
        open_callbacks = [c for c in snapshot.callbacks if c.status not in ("done", "cancelled")]
        top_gap = gaps[0] if gaps else None
        quick_actions = [
            {"icon": "●", "label": f"{len(open_callbacks)} open callback{'s' if len(open_callbacks) != 1 else ''}",
             "sub": (present.callback_row(sorted(open_callbacks, key=lambda c: c.created_at)[0], self.settings, now=now)["question"]
                     if open_callbacks else "All caught up"),
             "tag": "High" if open_callbacks else "Clear", "screen": "callbacks"},
            {"icon": "?", "label": f"{len([g for g in gaps if not g['review']])} unanswered question{'s' if len(gaps) != 1 else ''} flagged",
             "sub": f"{top_gap['text']} — {top_gap['evidence']} ask{'s' if top_gap['evidence'] != 1 else ''}" if top_gap else "Nothing flagged",
             "tag": "Review", "screen": "callbacks"},
            {"icon": "▲", "label": "Top trend this week" if not trends["empty"] else "No trend scan yet",
             "sub": trends["items"][0]["title"] if trends["items"] else trends.get("note") or "", "tag": "Marketing", "screen": "marketing"},
        ]
        analytics_out = present.analytics_payload(raw, self.settings, now=now)
        notifications = present.notifications(analytics_out=analytics_out, trends=trends,
                                              open_callbacks=open_callbacks, now=now)
        month_minutes = raw["minutes"]["month_to_date"]
        included = self.settings.included_minutes_per_month
        return {
            "generatedAt": now.isoformat(),
            "business": {"id": self.business_id, "name": setup["businessName"], "timezone": self.settings.business_timezone,
                         "source": snapshot.source, "demo": snapshot.has_demo_calls},
            "period": raw["period"],
            "analytics": analytics_out,
            "notifications": notifications,
            "overview": {"kpis": present.kpis(raw_today), "chart": chart, "chart30": chart30, "routeMix": present.route_mix(raw_today),
                         "quickActions": quick_actions,
                         "callLogCountLabel": f"calls on record · {self.settings.business_timezone}"},
            "calls": present.calls_payload(snapshot, self.settings, now=now),
            "callbacks": [present.callback_row(c, self.settings, now=now) for c in sorted(snapshot.callbacks, key=lambda c: c.created_at, reverse=True)],
            "gaps": gaps,
            "trends": trends,
            "ai": self.marketing.ai_status(),
            "setup": setup,
            "settings": {"notifPrefs": snapshot.settings.get("notifPrefs") or {"callbacks": True, "digest": True, "trends": False},
                         "packetConfirmed": bool(snapshot.settings.get("packetConfirmed", False))},
            "usage": {"minutesUsed": round(month_minutes), "minutesIncluded": included,
                      "label": f"{round(month_minutes):,} of {included:,} included call minutes used this month",
                      "pct": min(100, round(month_minutes / included * 100)) if included else 0},
            "overlord": {"greeting": "I read the same records as the dashboard — calls, bookings, callbacks and the latest trend scan. Ask me anything."},
        }


def build_router(ctx: DashboardContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/dashboard/bootstrap")
    def bootstrap(period: str = Query("today")) -> JSONResponse:
        return JSONResponse(ctx.bootstrap(_period(period)))

    @router.get("/api/dashboard/analytics")
    def analytics(period: str = Query("today"), start: str | None = Query(None),
                  end: str | None = Query(None)) -> JSONResponse:
        """`period=custom&start=YYYY-MM-DD&end=YYYY-MM-DD` is the Analytics calendar picker; without
        dates, custom falls back to the last 30 days as it always did."""
        return JSONResponse(ctx.analytics(_period(period), start_date=_date(start), end_date=_date(end)))

    @router.get("/api/dashboard/calls")
    def calls(period: str | None = Query(None)) -> JSONResponse:
        now = ctx.clock()
        snapshot = ctx.source.load()
        rows = present.calls_payload(snapshot, ctx.settings, now=now)
        if period:
            window = an.period_for(_period(period), now, ctx.settings)
            rows = [r for r in rows if window.contains(dt.datetime.fromisoformat(r["startedAt"]))]
        return JSONResponse({"calls": rows, "asOf": snapshot.as_of.isoformat()})

    @router.get("/api/dashboard/calls/{call_id}")
    def call(call_id: str) -> JSONResponse:
        for row in present.calls_payload(ctx.source.load(), ctx.settings, now=ctx.clock()):
            if row["id"] == call_id:
                return JSONResponse(row)
        raise HTTPException(404, f"call {call_id} not found")

    @router.post("/api/dashboard/callbacks/{callback_id}/status")
    def callback_status(callback_id: str, payload: dict = Body(...)) -> JSONResponse:
        status = str(payload.get("status") or "").strip().lower().replace(" ", "_")
        if status not in ("open", "in_progress", "done"):
            raise HTTPException(400, "status must be open, in_progress or done")
        if not ctx.source.set_callback_status(callback_id, status, now=ctx.clock()):
            raise HTTPException(404, f"callback {callback_id} not found")
        return JSONResponse({"id": callback_id, "status": status})

    @router.post("/api/dashboard/gaps/{key}/review")
    def gap_review(key: str, payload: dict = Body(...)) -> JSONResponse:
        status = str(payload.get("status") or "").strip().lower()
        if status not in ("approved", "dismissed"):
            raise HTTPException(400, "status must be approved or dismissed")
        review = ReviewRecord(key=key, status=status, answer=(payload.get("answer") or None),
                              question=payload.get("question"), reviewed_at=ctx.clock())
        ctx.source.set_gap_review(review)
        return JSONResponse({"key": key, "status": status, "answer": review.answer})

    @router.post("/api/dashboard/settings")
    def settings(payload: dict = Body(...)) -> JSONResponse:
        fields: dict[str, Any] = {}
        if isinstance(payload.get("notifPrefs"), dict):
            fields["notifPrefs"] = {k: bool(v) for k, v in payload["notifPrefs"].items()}
        if "packetConfirmed" in payload:
            fields["packetConfirmed"] = bool(payload["packetConfirmed"])
            if fields["packetConfirmed"]:
                fields["packetConfirmedAt"] = ctx.clock().isoformat()
        if not fields:
            raise HTTPException(400, "nothing to update")
        return JSONResponse(ctx.source.update_settings(fields))

    @router.get("/api/dashboard/trends")
    def trends() -> JSONResponse:
        return JSONResponse(ctx.marketing.trends())

    @router.post("/api/dashboard/trends/refresh")
    def refresh_trends() -> JSONResponse:
        """"Refresh now" on the Marketing screen: rebuild the marketing deps and re-read Firestore
        instead of the once-a-day local cache. A read only — no scrape, no credit spent."""
        return JSONResponse(ctx.marketing.trends(force=True))

    @router.post("/api/dashboard/trends/{post_id}/save")
    def save_trend(post_id: str) -> JSONResponse:
        status, body = ctx.marketing.save(post_id)
        return JSONResponse(status_code=status, content=body)

    @router.post("/api/overlord/ask")
    async def ask(payload: dict = Body(...)) -> JSONResponse:
        question = str(payload.get("question") or "").strip()
        if not question:
            raise HTTPException(400, "question is required")
        now = ctx.clock()
        snapshot = ctx.source.load()
        today = an.summary_for_prompt(an.compute(snapshot, ctx.settings, period_key="today", now=now))
        week = an.summary_for_prompt(an.compute(snapshot, ctx.settings, period_key="week", now=now))
        setup = present.setup_payload(ctx.reader, ctx.business_id, ctx.settings, now=now)
        packet = overlord.build_packet(business_name=setup["businessName"], voice_today=today, voice_week=week,
                                       marketing=ctx.marketing.summary())
        result = await overlord.answer(question, packet, transport=ctx.overlord_transport())
        return JSONResponse(result)

    @router.get("/api/dashboard/ai")
    def ai_status() -> JSONResponse:
        """What the chats show above themselves — model in use and credits left."""
        return JSONResponse(ctx.marketing.ai_status())

    @router.api_route("/api/jobs/{name}", methods=["GET", "POST"])
    def job(name: str, authorization: str | None = Header(None)) -> JSONResponse:
        if name not in JOB_NAMES:
            raise HTTPException(404, f"unknown job; expected one of {', '.join(JOB_NAMES)}")
        if ctx.cron_secret:
            if authorization != f"Bearer {ctx.cron_secret}":
                raise HTTPException(401, "bad or missing cron secret")
        elif ctx.source.kind != "local":
            raise HTTPException(503, "CRON_SECRET is not configured")
        return JSONResponse(run_job(ctx, name))

    return router


def run_job(ctx: DashboardContext, name: str) -> dict:
    now = ctx.clock()
    if name == "marketing-scan":
        from marketing_radar.jobs.scan import run_paid_scan

        outcome = run_paid_scan(ctx.marketing.deps())
        return {"job": name, "ran_at": now.isoformat(), "note": outcome.note, "live_calls": outcome.live_calls,
                "credits_spent": outcome.credits_spent, "scan_id": outcome.brief.scan_id if outcome.brief else None}
    if name == "marketing-daily-pull":
        from marketing_radar.jobs.daily_pull import daily_pull

        deps = ctx.marketing.deps()
        bundle = daily_pull(deps.store, deps.cache, deps.settings, deps.clock, force=True)
        return {"job": name, "ran_at": now.isoformat(), "scan_id": bundle.scan_id}
    if name == "marketing-expire":
        from marketing_radar.jobs.expire_drafts import expire_drafts

        deps = ctx.marketing.deps()
        result = expire_drafts(deps.store, deps.settings, deps.clock)
        return {"job": name, "ran_at": now.isoformat(), "deleted_scripts": result.deleted_scripts, "pruned_cache": result.pruned_cache}
    if name == "calendar-retry":
        synced = ctx.calendar_retry(now) if ctx.calendar_retry else 0
        return {"job": name, "ran_at": now.isoformat(), "synced": synced}
    raise HTTPException(404, name)


def _date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(400, f"bad date {value!r}; expected YYYY-MM-DD")


def _period(value: str) -> str:
    value = (value or "today").lower()
    return value if value in an.PERIODS else "today"
