"""metrics.md formulas, checked against a hand-built snapshot (no engine, no I/O)."""
import datetime as dt

from dashboard import analytics as an
from dashboard.records import BookingRecord, CallRecord, CallbackRecord, ReviewRecord, Snapshot, TurnRecord
from dashboard.settings import DashboardSettings

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 10, 5, 0, tzinfo=UTC)  # 15:00 Melbourne, Thursday
SETTINGS = DashboardSettings.from_yaml(env={})


def turn(call_id, index, speaker, text, **kw):
    return TurnRecord(call_id=call_id, turn_index=index, speaker=speaker, text=text, at=NOW - dt.timedelta(hours=2), **kw)


def call(call_id, hours_ago, *, outcome, turns, session_type="phone", duration=120):
    started = NOW - dt.timedelta(hours=hours_ago)
    return CallRecord(call_id=call_id, started_at=started, outcome=outcome, duration_ms=duration * 1000,
                      session_type=session_type, turns=turns)


def snapshot():
    calls = [
        call("c1", 2, outcome="booked", turns=[turn("c1", 1, "caller", "table for two", intent="BOOK", router_confidence=0.9),
                                                turn("c1", 2, "agent", "what time?", router_ms=100, answer_ms=200)]),
        call("c2", 3, outcome="answered", turns=[turn("c2", 1, "caller", "gluten free?", intent="ANSWER_QUESTION", router_confidence=0.8),
                                                  turn("c2", 2, "agent", "cannot confirm", answer_source="not_found", router_ms=300)]),
        call("c3", 4, outcome="answered", turns=[turn("c3", 1, "caller", "hours?", intent="ANSWER_QUESTION"),
                                                  turn("c3", 2, "agent", "7:30 to 2:30", answer_source="fact", router_ms=400, answer_ms=600)]),
        call("c4", 5, outcome="answered", turns=[turn("c4", 1, "caller", "catering for 30", intent="CALLBACK"),
                                                  turn("c4", 2, "agent", "passed on", answer_source="template", router_ms=1500)]),
        call("c5", 6, outcome="abandoned", turns=[]),
        call("c6", 30, outcome="answered", turns=[turn("c6", 1, "caller", "hi", intent="CHITCHAT"), turn("c6", 2, "agent", "hello")]),  # yesterday
        call("c7", 0.5, outcome="error", turns=[]),
    ]
    callbacks = [
        CallbackRecord(id="cb1", call_id="c4", reason="catering", status="open", created_at=NOW - dt.timedelta(hours=5), question="catering for 30"),
        CallbackRecord(id="cb2", call_id="c2", reason="allergen_unknown", status="done", created_at=NOW - dt.timedelta(hours=3), question="gluten free?"),
        CallbackRecord(id="cb3", call_id="c6", reason="mystery_code", status="open", created_at=NOW - dt.timedelta(hours=30)),
    ]
    bookings = [BookingRecord(key="b1", call_id="c1", party_size=2, created_at=NOW - dt.timedelta(hours=2)),
                BookingRecord(key="b0", call_id="c0", party_size=9, created_at=NOW - dt.timedelta(hours=26))]
    return Snapshot(calls=calls, callbacks=callbacks, bookings=bookings, as_of=NOW - dt.timedelta(minutes=3),
                    reviews={an.review_key("gluten free?"): ReviewRecord(key=an.review_key("gluten free?"), status="approved")})


def test_period_windows_are_business_local_and_previous_is_equal_length():
    today = an.period_for("today", NOW, SETTINGS)
    assert today.start == dt.datetime(2026, 9, 9, 14, 0, tzinfo=UTC) and today.end == NOW  # midnight Melbourne
    prev = today.previous()
    assert prev.end == today.start and (prev.end - prev.start) == (today.end - today.start)
    assert an.period_for("week", NOW, SETTINGS).start == dt.datetime(2026, 9, 3, 14, 0, tzinfo=UTC)
    assert an.period_for("bogus", NOW, SETTINGS).key == "today"


def test_containment_task_success_and_unknowns():
    a = an.compute(snapshot(), SETTINGS, period_key="today", now=NOW)
    assert a["calls"]["n"] == 6  # c1..c5 + c7 today; c6 yesterday
    # contained = no callback request: c1, c3, c5, c7 → 4 of 6
    assert a["calls"]["contained"] == 4 and abs(a["calls"]["containment"] - 4 / 6) < 1e-9
    assert a["calls"]["containment_band"] == "Watch"
    # success: c1 (booked), c3 (answered, no gap); failure: c2 (gap), c7 (error); unknown: c4 (callback), c5 (abandoned)
    assert (a["task"]["success"], a["task"]["failure"], a["task"]["unknown"]) == (2, 2, 2)
    assert a["task"]["rate"] == 0.5 and a["task"]["band"] == "Needs attention"
    assert a["coherence"]["rate"] is None and a["coherence"]["band"] == "No data"


def test_latency_is_nearest_rank_over_agent_turn_samples():
    assert an.percentile([], 50) is None
    assert an.percentile([5, 1, 3], 50) == 3 and an.percentile([5, 1, 3], 90) == 5
    a = an.compute(snapshot(), SETTINGS, period_key="today", now=NOW)
    # samples: c1 300, c2 300, c3 1000, c4 1500 → p50 = rank 2 = 300, p90 = rank 4 = 1500
    assert a["latency"]["samples"] == 4 and a["latency"]["p50_ms"] == 300 and a["latency"]["p90_ms"] == 1500
    assert a["latency"]["p50_band"] == "Good" and a["latency"]["p90_band"] == "Good"


def test_errors_fallback_volume_and_handoffs():
    a = an.compute(snapshot(), SETTINGS, period_key="today", now=NOW)
    assert a["errors"]["failed"] == 1 and abs(a["errors"]["rate"] - 1 / 6) < 1e-9 and a["errors"]["band"] == "Needs attention"
    assert a["fallback"]["gap_calls"] == 1 and a["fallback"]["band"] == "Watch"
    # 6 calls over 15 elapsed hours (0.625 d) against a baseline of 6/day → 1.6× → Watch
    assert a["volume"]["band"] == "Watch" and 1.5 < a["volume"]["multiple"] < 1.7
    h = a["handoffs"]
    assert (h["planned"], h["forced"], h["unclassified"]) == (1, 1, 0)  # cb3 belongs to yesterday's call
    assert h["planned_rate"] == 1 / 6 and h["forced_rate"] == 1 / 6
    week = an.compute(snapshot(), SETTINGS, period_key="week", now=NOW)["handoffs"]
    assert week["unclassified"] == 1 and any(r["reason"] == "mystery_code" and r["class"] == "unclassified" for r in week["reasons"])


def test_bookings_compare_and_outside_hours():
    a = an.compute(snapshot(), SETTINGS, period_key="today", now=NOW)
    assert a["bookings"]["count"] == 1 and a["bookings"]["covers"] == 2
    assert a["bookings"]["previous_count"] == 1 and a["bookings"]["previous_covers"] == 9
    assert a["bookings"]["compare"].startswith("↔ vs 1 bookings / 9 covers yesterday")
    assert a["calls"]["compare"] == "↑ +500% vs 1 calls yesterday"  # c6 fell inside yesterday's equal-length window
    # c7 started at 14:30 local (outside 07:30–14:30) → 1 of 6
    assert a["outside_hours"]["count"] == 1
    assert an.compare_label(0, 0, "calls") == "No change vs 0 calls" and an.compare_label(8, 6, "calls").startswith("↑ +33%")


def test_gaps_group_unreviewed_first_and_open_callbacks_are_a_current_backlog():
    a = an.compute(snapshot(), SETTINGS, period_key="today", now=NOW)
    assert [g["review"] for g in a["gaps"]] == ["approved"] and a["gaps"][0]["evidence"] == 1
    # open backlog ignores the period filter: cb1 (5 h) and cb3 (30 h)
    assert [c["id"] for c in a["open_callbacks"]] == ["cb3", "cb1"]
    assert a["oldest_wait_secs"] == 30 * 3600 and a["oldest_wait_warn"] is True
    assert a["freshness"]["stale"] is False and a["demo"] is False


def test_zero_denominators_give_no_data_not_zero():
    empty = Snapshot(as_of=NOW)
    a = an.compute(empty, SETTINGS, period_key="today", now=NOW)
    assert a["calls"]["containment"] is None and a["calls"]["containment_band"] == "No data"
    assert a["task"]["rate"] is None and a["latency"]["p50_ms"] is None and a["volume"]["band"] == "No data"
    assert a["oldest_wait_secs"] is None and a["bookings"]["compare"] == "No change vs yesterday"


def test_cost_uses_contained_calls_for_staff_time_and_every_call_for_ai_minutes():
    a = an.compute(snapshot(), SETTINGS, period_key="today", now=NOW)
    assert a["cost"]["staff_hours_avoided"] == round(4 * 11.5 / 60, 3)
    assert a["minutes"]["connected"] == 12.0  # six calls × 120 s
    assert a["cost"]["estimated_ai_cents"] is None and a["cost"]["net_saving_cents"] is None  # no rate configured
    priced = DashboardSettings.from_yaml({"ai_cost_per_minute_cents": 10}, env={})
    b = an.compute(snapshot(), priced, period_key="today", now=NOW)
    assert b["cost"]["estimated_ai_cents"] == 120.0 and b["cost"]["net_saving_cents"] == round(4 * 11.5 / 60 * 4250 - 120, 2)


def test_daily_series_covers_seven_local_days_ending_today():
    series = an.daily_series(snapshot(), SETTINGS, now=NOW)
    assert len(series["values"]) == 7 and series["labels"][-1] == "Today" and series["labels"][-2] == "Yesterday"
    assert series["values"][-1] == 6 and series["values"][-2] == 1 and series["dates"][-1] == "Thu, Sep 10"
