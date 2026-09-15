"""Metric calculations for the Analytics screen — UI/... uploads/metrics.md, P0 rows.

Pure functions over a `Snapshot`: no I/O, no clock reads (the caller passes `now`). Rules kept
from the contract: rates are fractions; zero denominators produce None ("No data"), never 0 or a
Good badge; unknown task grades leave the denominator; staff time avoided counts contained calls
only while AI cost counts every call; percentiles are nearest-rank over agent-turn samples;
unclassified callback reasons stay visible; containment is one definition for both tabs.

Grading decisions (no graded fields exist in the call documents yet — plan 0005):
- task result: success = booked, or answered with no knowledge gap; failure = error, or answered
  with a gap; abandoned/callback = unknown (excluded).
- coherence: nothing records it → "No data" until a grader writes `coherenceResult`.
- latency sample: agent-turn router + answer ms ("model answer stage, not caller-perceived wait").
- route per call: BOOK intent → Booking; callback or CALLBACK → Callback; ANSWER_QUESTION → Question; else Chitchat.
"""
from __future__ import annotations

import datetime as dt
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from .records import CallRecord, CallbackRecord, ReviewRecord, Snapshot, normalise_question, review_key
from .settings import DashboardSettings

PERIODS = {"today": "today", "week": "this week", "month": "this month", "30d": "last 30 days",
           "custom": "last 30 days"}
GOOD, WATCH, BAD, NO_DATA = "Good", "Watch", "Needs attention", "No data"


@dataclass(frozen=True)
class Period:
    key: str
    start: dt.datetime
    end: dt.datetime
    label: str

    @property
    def days(self) -> float:
        return max((self.end - self.start).total_seconds() / 86_400, 1e-9)

    def previous(self) -> "Period":
        span = self.end - self.start
        return Period(self.key, self.start - span, self.start, "previous period")

    def contains(self, moment: dt.datetime | None) -> bool:
        return moment is not None and self.start <= moment < self.end


def period_for(key: str, now: dt.datetime, settings: DashboardSettings, *,
               start_date: dt.date | None = None, end_date: dt.date | None = None) -> Period:
    key = key if key in PERIODS else "today"
    local_now = now.astimezone(settings.tz)
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if key == "custom" and start_date is not None:
        # the picker's dates are the owner's local days, inclusive at both ends
        end_date = end_date or start_date
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        start = dt.datetime.combine(start_date, dt.time.min, tzinfo=settings.tz)
        end = dt.datetime.combine(end_date, dt.time.min, tzinfo=settings.tz) + dt.timedelta(days=1)
        return Period(key, start.astimezone(dt.timezone.utc), min(end.astimezone(dt.timezone.utc), now),
                      custom_label(start_date, end_date))
    if key == "today":
        start = midnight
    elif key == "week":
        start = midnight - dt.timedelta(days=6)
    elif key == "month":
        # calendar month-to-date, so "this month" means what the owner's calendar says it means
        start = midnight.replace(day=1)
    else:
        start = midnight - dt.timedelta(days=29)
    return Period(key, start.astimezone(dt.timezone.utc), now, PERIODS[key])


def custom_label(start: dt.date, end: dt.date) -> str:
    if start == end:
        return start.strftime("%-d %b") if _dash_ok() else start.strftime("%d %b").lstrip("0")
    same_month = (start.year, start.month) == (end.year, end.month)
    left = str(start.day) if same_month else f"{start.day} {start:%b}"
    return f"{left}–{end.day} {end:%b}"


def _dash_ok() -> bool:
    try:
        dt.date(2026, 1, 5).strftime("%-d")
    except ValueError:  # Windows strftime has no %-d
        return False
    return True


# ---- classification helpers ----------------------------------------------------------------------

def route_of(call: CallRecord, callbacks_by_call: dict[str, list[CallbackRecord]]) -> str:
    """What the caller rang for. A question that ended in a callback is still a Question (the
    mockup's "Couldn't answer" row); Callback is the explicit ask for a person."""
    intents = call.intents
    if "BOOK" in intents or call.effective_outcome == "booked":
        return "Booking"
    if "CALLBACK" in intents:
        return "Callback"
    if "ANSWER_QUESTION" in intents:
        return "Question"
    if callbacks_by_call.get(call.call_id) or call.effective_outcome == "callback":
        return "Callback"
    return "Chitchat"


def outcome_label(call: CallRecord, callbacks_by_call: dict[str, list[CallbackRecord]]) -> str:
    outcome = call.effective_outcome
    if outcome == "booked":
        return "Booked"
    if call.has_gap:
        return "Couldn't answer"
    if outcome == "callback" or callbacks_by_call.get(call.call_id):
        return "Callback logged"
    if outcome == "answered":
        return "Answered"
    if outcome == "abandoned":
        return "Abandoned"
    if outcome == "error":
        return "Error"
    return outcome.title()


def task_result(call: CallRecord, settings: DashboardSettings,
                callbacks_by_call: dict[str, list[CallbackRecord]] | None = None) -> str:
    outcome = call.effective_outcome
    if outcome == "error" or (call.twilio_status in settings.failed_statuses):
        return "failure"
    if outcome == "booked":
        return "success"
    if call.has_gap:
        return "failure"  # the caller did not get what they rang for, even if a callback was logged
    if outcome == "callback" or (callbacks_by_call or {}).get(call.call_id):
        return "unknown"  # a person still has to finish it; neither S nor F (metrics.md 2.1)
    if outcome == "answered":
        return "success"
    return "unknown"


def handoff_class(reason: str, settings: DashboardSettings) -> str:
    if reason in settings.planned_reasons:
        return "planned"
    if reason in settings.forced_reasons:
        return "forced"
    return "unclassified"


def is_failed(call: CallRecord, settings: DashboardSettings) -> bool:
    return call.effective_outcome == "error" or (call.twilio_status or "") in settings.failed_statuses


def percentile(samples: list[int], k: int) -> int | None:
    """Nearest-rank: value at rank ceil(k/100 × n), one-based, ascending."""
    if not samples:
        return None
    ordered = sorted(samples)
    rank = max(1, math.ceil(k / 100 * len(ordered)))
    return ordered[rank - 1]


def band(value: float | None, good: float, bad: float, *, higher_is_better: bool = True) -> str:
    if value is None:
        return NO_DATA
    if higher_is_better:
        return GOOD if value >= good else BAD if value < bad else WATCH
    return GOOD if value < good else BAD if value > bad else WATCH


def compare_label(current: int, previous: int, noun: str) -> str:
    """metrics.md comparison rules: absolute change, percentage only when previous > 0."""
    if current == previous == 0:
        return f"No change vs {previous} {noun}"
    if previous == 0:
        return f"No prior baseline ({current} {noun})"
    delta = current - previous
    pct = round(delta / previous * 100)
    arrow = "↑" if delta > 0 else "↓" if delta < 0 else "↔"
    return f"{arrow} {pct:+d}% vs {previous} {noun}"


def ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


# ---- the calculation -------------------------------------------------------------------------------

@dataclass
class GapGroup:
    key: str
    text: str
    evidence: int
    last_asked: dt.datetime
    call_ids: list[str] = field(default_factory=list)
    review: ReviewRecord | None = None


def group_gaps(calls: list[CallRecord], reviews: dict[str, ReviewRecord]) -> list[GapGroup]:
    groups: dict[str, GapGroup] = {}
    for call in calls:
        for question, asked_at in call.gap_questions:
            key = review_key(question)
            asked_at = asked_at or call.started_at
            group = groups.get(key)
            if group is None:
                groups[key] = group = GapGroup(key=key, text=question, evidence=0, last_asked=asked_at)
            if call.call_id not in group.call_ids:
                group.call_ids.append(call.call_id)
                group.evidence += 1
            if asked_at > group.last_asked:
                group.last_asked = asked_at
                group.text = question
    for key, group in groups.items():
        group.review = reviews.get(key)
    # unreviewed first, then evidence desc, then last asked desc (metrics.md 3.1)
    return sorted(groups.values(), key=lambda g: (g.review is not None, -g.evidence, -g.last_asked.timestamp()))


def compute(snapshot: Snapshot, settings: DashboardSettings, *, period_key: str, now: dt.datetime,
            start_date: dt.date | None = None, end_date: dt.date | None = None) -> dict[str, Any]:
    period = period_for(period_key, now, settings, start_date=start_date, end_date=end_date)
    previous = period.previous()
    callbacks_by_call: dict[str, list[CallbackRecord]] = defaultdict(list)
    for cb in snapshot.callbacks:
        callbacks_by_call[cb.call_id].append(cb)

    calls = [c for c in snapshot.calls if period.contains(c.started_at)]
    prev_calls = [c for c in snapshot.calls if previous.contains(c.started_at)]
    n = len(calls)
    contained = [c for c in calls if not callbacks_by_call.get(c.call_id) and c.effective_outcome != "callback"]
    n_c = len(contained)

    grades = Counter(task_result(c, settings, callbacks_by_call) for c in calls)
    s, f = grades["success"], grades["failure"]
    latencies = [ms for c in calls for ms in c.agent_latencies]
    failed = sum(1 for c in calls if is_failed(c, settings))
    gap_calls = sum(1 for c in calls if c.has_gap)
    selected_ids = {c.call_id for c in calls}
    period_callbacks = [cb for cb in snapshot.callbacks if cb.call_id in selected_ids]
    classes = Counter(handoff_class(cb.reason, settings) for cb in period_callbacks)
    reason_counts = Counter(cb.reason for cb in period_callbacks)

    bookings = [b for b in snapshot.bookings if period.contains(b.created_at) and b.status != "cancelled"]
    prev_bookings = [b for b in snapshot.bookings if previous.contains(b.created_at) and b.status != "cancelled"]
    covers = sum(b.party_size or 0 for b in bookings)
    prev_covers = sum(b.party_size or 0 for b in prev_bookings)
    covers_incomplete = any(b.party_size is None for b in bookings)

    outside = [c for c in calls if not settings.is_open(c.started_at.astimezone(settings.tz))]
    durations = [c.duration_secs for c in calls]
    minutes = sum(d for d in durations if d is not None) / 60
    minutes_incomplete = any(d is None for d in durations)
    month_start = now.astimezone(settings.tz).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_minutes = sum((c.duration_secs or 0) for c in snapshot.calls if c.started_at >= month_start) / 60

    ai_cost_cents = minutes * settings.ai_cost_per_minute_cents if settings.ai_cost_per_minute_cents is not None else None
    staff_hours_avoided = n_c * settings.manual_minutes_per_call / 60
    labour_avoided_cents = staff_hours_avoided * settings.labour_cost_per_hour_cents
    measured_cost_cents = sum(c.cost_cents for c in calls)

    open_callbacks = sorted((cb for cb in snapshot.callbacks if cb.status not in ("done", "cancelled")),
                            key=lambda cb: cb.created_at)
    oldest_wait_secs = (now - open_callbacks[0].created_at).total_seconds() if open_callbacks else None

    p50, p90 = percentile(latencies, 50), percentile(latencies, 90)
    volume_per_day = n / period.days
    volume_multiple = ratio(volume_per_day, settings.baseline_per_day)
    volume_band = NO_DATA if volume_multiple is None or n == 0 else (
        GOOD if abs(volume_multiple - 1) <= settings.band_tolerance() else
        BAD if volume_multiple > settings.band_bad_multiple() else WATCH)

    route_counts = Counter(route_of(c, callbacks_by_call) for c in calls)
    fallback_rate = ratio(gap_calls, n)
    planned_rate, forced_rate = ratio(classes["planned"], n), ratio(classes["forced"], n)
    stale_after = dt.timedelta(minutes=settings.stale_after_minutes)

    return {
        "period": {"key": period.key, "label": period.label, "start": period.start.isoformat(), "end": period.end.isoformat(),
                   "days": round(period.days, 4), "previous_label": _previous_label(period.key)},
        "calls": {"n": n, "previous": len(prev_calls), "compare": compare_label(n, len(prev_calls), f"calls {_previous_label(period.key)}"),
                  "by_route": dict(route_counts), "contained": n_c, "containment": ratio(n_c, n),
                  "containment_band": band(ratio(n_c, n), settings.band("containment").good, settings.band("containment").bad)},
        "task": {"success": s, "failure": f, "unknown": grades["unknown"], "rate": ratio(s, s + f),
                 "band": band(ratio(s, s + f), settings.band("task_success").good, settings.band("task_success").bad)},
        "coherence": {"rate": None, "band": NO_DATA, "note": "no coherence grader writes coherenceResult yet"},
        "latency": {"p50_ms": p50, "p90_ms": p90, "samples": len(latencies),
                    "p50_band": band(p50, settings.band("latency_p50_ms").good, settings.band("latency_p50_ms").bad, higher_is_better=False),
                    "p90_band": band(p90, settings.band("latency_p90_ms").good, settings.band("latency_p90_ms").bad, higher_is_better=False)},
        "errors": {"failed": failed, "rate": ratio(failed, n),
                   "band": band(ratio(failed, n), settings.band("error_rate").good, settings.band("error_rate").bad, higher_is_better=False)},
        "volume": {"per_day": volume_per_day, "baseline": settings.baseline_per_day, "multiple": volume_multiple, "band": volume_band},
        "fallback": {"gap_calls": gap_calls, "rate": fallback_rate,
                     "band": band(fallback_rate, settings.band("fallback_rate").good, settings.band("fallback_rate").bad, higher_is_better=False)},
        "handoffs": {"planned": classes["planned"], "forced": classes["forced"], "unclassified": classes["unclassified"],
                     "planned_rate": planned_rate, "forced_rate": forced_rate, "total_requests": len(period_callbacks),
                     "band": band(forced_rate, settings.band("forced_handoff").good, settings.band("forced_handoff").bad, higher_is_better=False)
                     if n else NO_DATA,
                     "reasons": [{"reason": r, "count": c, "class": handoff_class(r, settings)} for r, c in reason_counts.most_common()]},
        "bookings": {"count": len(bookings), "covers": covers, "covers_incomplete": covers_incomplete,
                     "previous_count": len(prev_bookings), "previous_covers": prev_covers,
                     "compare": _bookings_compare(len(bookings), covers, len(prev_bookings), prev_covers, _previous_label(period.key)),
                     "conversion": ratio(sum(1 for c in calls if c.effective_outcome == "booked"), n)},
        "outside_hours": {"count": len(outside), "share": ratio(len(outside), n), "hours_label": settings.opening_hours_label()},
        "minutes": {"connected": round(minutes, 2), "incomplete": minutes_incomplete, "month_to_date": round(month_minutes, 2),
                    "included_per_month": settings.included_minutes_per_month},
        "cost": {"estimated_ai_cents": ai_cost_cents, "measured_ai_cents": round(measured_cost_cents, 4),
                 "staff_hours_avoided": round(staff_hours_avoided, 3), "labour_avoided_cents": round(labour_avoided_cents, 2),
                 "ai_cost_per_call_cents": ratio(ai_cost_cents, n) if ai_cost_cents is not None else None,
                 "staff_cost_per_call_cents": settings.manual_minutes_per_call / 60 * settings.labour_cost_per_hour_cents,
                 "net_saving_cents": round(labour_avoided_cents - ai_cost_cents, 2) if ai_cost_cents is not None else None,
                 "method": "estimated" if ai_cost_cents is not None else "no ai_cost_per_minute_cents configured"},
        "gaps": [{"key": g.key, "text": g.text, "evidence": g.evidence, "last_asked": g.last_asked.isoformat(),
                  "call_ids": g.call_ids, "review": g.review.status if g.review else None,
                  "answer": g.review.answer if g.review else None}
                 for g in group_gaps(snapshot.calls, snapshot.reviews)],
        "open_callbacks": [{"id": cb.id, "name": cb.name, "phone": cb.phone, "question": cb.question, "reason": cb.reason,
                            "status": cb.status, "created_at": cb.created_at.isoformat(),
                            "wait_secs": (now - cb.created_at).total_seconds()} for cb in open_callbacks],
        "oldest_wait_secs": oldest_wait_secs,
        "oldest_wait_warn": oldest_wait_secs is not None and oldest_wait_secs > settings.callback_age_warn_minutes * 60,
        "freshness": {"as_of": snapshot.as_of.isoformat(), "stale": (now - snapshot.as_of) > stale_after,
                      "stale_after_minutes": settings.stale_after_minutes, "timezone": settings.business_timezone},
        "demo": snapshot.has_demo_calls,
    }


def daily_series(snapshot: Snapshot, settings: DashboardSettings, *, now: dt.datetime, days: int = 7) -> dict[str, list]:
    """Calls per local day for the Overview line chart, oldest → today."""
    local_now = now.astimezone(settings.tz)
    today = local_now.date()
    counts = Counter(c.started_at.astimezone(settings.tz).date() for c in snapshot.calls)
    dates = [today - dt.timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    labels = [("Today" if d == today else "Yesterday" if (today - d).days == 1 else f"{(today - d).days}d ago") for d in dates]
    if days > 10:
        # one label per point is unreadable at 30 days: keep every fifth, plus today
        labels = [lbl if i % 5 == 0 or i == len(dates) - 1 else "" for i, lbl in enumerate(labels)]
    return {"values": [counts.get(d, 0) for d in dates], "dates": [d.strftime("%a, %b ") + str(d.day) for d in dates],
            "labels": labels, "days": days}


def _previous_label(key: str) -> str:
    return {"today": "yesterday", "week": "the week before", "month": "the period before",
            "30d": "the 30 days before", "custom": "the 30 days before"}[key]


def _bookings_compare(count: int, covers: int, prev_count: int, prev_covers: int, previous_label: str) -> str:
    if count == prev_count == 0:
        return f"No change vs {previous_label}"
    if prev_count == 0:
        return f"No prior baseline (0 bookings {previous_label})"
    arrow = "↑" if count > prev_count else "↓" if count < prev_count else "↔"
    return f"{arrow} vs {prev_count} bookings / {prev_covers} covers {previous_label}"


def summary_for_prompt(analytics: dict[str, Any]) -> dict[str, Any]:
    """Compact numbers the overlord can ground an answer on."""
    return {
        "period": analytics["period"]["label"],
        "calls": analytics["calls"]["n"], "calls_previous": analytics["calls"]["previous"],
        "by_route": analytics["calls"]["by_route"],
        "containment": _pct(analytics["calls"]["containment"]),
        "bookings": analytics["bookings"]["count"], "covers": analytics["bookings"]["covers"],
        "bookings_previous": analytics["bookings"]["previous_count"],
        "task_success": _pct(analytics["task"]["rate"]),
        "fallback_rate": _pct(analytics["fallback"]["rate"]),
        "open_callbacks": [{"name": c["name"], "question": c["question"], "reason": c["reason"]} for c in analytics["open_callbacks"][:5]],
        "unanswered_questions": [{"question": g["text"], "asked_by_calls": g["evidence"], "review": g["review"]} for g in analytics["gaps"][:5]],
        "handoffs": {k: analytics["handoffs"][k] for k in ("planned", "forced", "unclassified")},
        "outside_hours_calls": analytics["outside_hours"]["count"],
        "connected_minutes": analytics["minutes"]["connected"],
    }


def _pct(value: float | None) -> str | None:
    return None if value is None else f"{round(value * 100)}%"
