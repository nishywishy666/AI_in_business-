"""Entities → the exact shapes the UI template binds (UI/... Uncle Tony Overlord Dashboard.dc.html).

The mockup consumes display strings ('8:12am', '3m42s', '$0.18', 'Today, 10:02am'), so every
formatting decision lives here, in Python, never in a model (R4). Field names match the template's
class fields (`callsData`, `callbacksData`, `trendsData`, `unansweredDefs`, `menuRows`,
`packetFields`) and the bindings the bridge overrides in `renderVals()`.
"""
from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict
from typing import Any

from services.common.firestore import BusinessPaths
from services.voice.tools import BusinessReader, load_business_context

from . import analytics as an
from .records import CallRecord, CallbackRecord, Snapshot
from .settings import DashboardSettings, clock_label

ROUTE_COLOURS = {"Booking": "var(--color-accent)", "Question": "var(--color-accent-500)",
                 "Callback": "var(--color-neutral-500)", "Chitchat": "var(--color-neutral-300)"}
ROUTE_ORDER = ["Booking", "Question", "Callback", "Chitchat"]


# ---- formatting helpers -----------------------------------------------------------------------------

def time_label(moment: dt.datetime, settings: DashboardSettings) -> str:
    return clock_label(moment.astimezone(settings.tz).time())


def duration_label(secs: float | None) -> str:
    if secs is None:
        return "—"
    m, s = divmod(int(round(secs)), 60)
    return f"{m}m{s:02d}s"


def money(cents: float | None) -> str:
    return "—" if cents is None else f"${cents / 100:,.2f}"


def relative_day_label(moment: dt.datetime, now: dt.datetime, settings: DashboardSettings, *, with_time: bool = True) -> str:
    local, today = moment.astimezone(settings.tz), now.astimezone(settings.tz).date()
    delta = (today - local.date()).days
    if delta == 0:
        return f"Today, {clock_label(local.time())}" if with_time else "Today"
    if delta == 1:
        return "Yesterday"
    if 1 < delta < 7:
        return local.strftime("%A")
    return f"{local.strftime('%a, %b')} {local.day}"


def ago_label(moment: dt.datetime, now: dt.datetime) -> str:
    secs = max(0, int((now - moment).total_seconds()))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86_400:
        return f"{secs // 3600} h ago"
    return f"{secs // 86_400} d ago"


def wait_label(secs: float | None) -> str:
    if secs is None:
        return "None waiting"
    secs = int(secs)
    if secs < 3600:
        return f"{max(1, secs // 60)} min"
    if secs < 86_400:
        return f"{secs // 3600} h {secs % 3600 // 60} min"
    return f"{secs // 86_400} d {secs % 86_400 // 3600} h"


def mask_phone(phone: str | None) -> str:
    if not phone:
        return "no number"
    digits = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    if digits.startswith("+61") and len(digits) > 4:
        digits = "0" + digits[3:]
    if len(digits) <= 4:
        return "••••"
    keep = digits[:-3] if len(digits) <= 8 else digits[:len(digits) - 3]
    keep = keep[:4] + " " + keep[4:] if len(keep) > 4 else keep
    return f"{keep.rstrip()} •••"


def display_name(name: str | None, phone: str | None) -> str:
    if name:
        parts = name.split()
        return parts[0] + (f" {parts[-1][0]}." if len(parts) > 1 else "")
    return "Caller"


def pct(value: float | None, *, digits: int = 0) -> str:
    return "No data" if value is None else f"{round(value * 100, digits):g}%"


# ---- calls ------------------------------------------------------------------------------------------

SPEAKER = {"caller": "Caller", "agent": "Receptionist"}


def call_row(call: CallRecord, callbacks_by_call: dict[str, list[CallbackRecord]], settings: DashboardSettings) -> dict:
    route = an.route_of(call, callbacks_by_call)
    outcome = an.outcome_label(call, callbacks_by_call)
    latencies = call.agent_latencies
    ttfb = int(statistics.median(latencies)) if latencies else None
    confidences = call.confidences
    confidence = round(statistics.fmean(confidences) * 100) if confidences else None
    transcript = []
    for turn in call.turns:
        if not turn.text:
            continue
        offset = int((turn.at - call.started_at).total_seconds()) if turn.at else 0
        transcript.append({"speaker": SPEAKER.get(turn.speaker, turn.speaker.title()), "text": turn.text,
                           "ts": f"{max(0, offset) // 60}:{max(0, offset) % 60:02d}"})
    grade = an.task_result(call, settings, callbacks_by_call)
    callbacks = callbacks_by_call.get(call.call_id) or []
    handoff = "None"
    if callbacks:
        cls = an.handoff_class(callbacks[0].reason, settings)
        handoff = f"{cls.title()} — {callbacks[0].reason.replace('_', ' ')}"
    agent_turns = sum(1 for t in call.turns if t.speaker == "agent" and t.text)
    metrics = [
        {"label": "Handle time", "value": duration_label(call.duration_secs)},
        {"label": "Answer latency", "value": f"{ttfb} ms" if ttfb is not None else "No data"},
        {"label": "Router confidence", "value": f"{confidence}%" if confidence is not None else "No data"},
        {"label": "Agent turns", "value": str(agent_turns)},
        {"label": "Interruptions", "value": str(call.interruptions)},
        {"label": "Task grade", "value": grade.title()},
        {"label": "Handoff", "value": handoff},
        {"label": "Cost", "value": money(call.cost_cents) + (" (pricing not set)" if call.cost_cents == 0 else "")},
    ]
    caller_first = next((t.text for t in call.turns if t.speaker == "caller" and t.text), None)
    summary = _summary(call, route, outcome, caller_first, callbacks)
    return {
        "id": call.call_id, "time": time_label(call.started_at, settings), "duration": duration_label(call.duration_secs),
        "route": route, "outcome": outcome, "confidence": confidence if confidence is not None else 0,
        "cost": money(call.cost_cents), "transcript": transcript, "ttfb": ttfb if ttfb is not None else 0,
        "metrics": metrics, "summaryText": summary, "startedAt": call.started_at.isoformat(),
        "sessionType": call.session_type, "taskGrade": grade, "hasGap": call.has_gap,
    }


def _summary(call: CallRecord, route: str, outcome: str, caller_first: str | None, callbacks: list[CallbackRecord]) -> str:
    ask = (caller_first or "the caller's request").rstrip(".?!")
    result = {
        "Booked": "the receptionist confirmed a booking and took the caller's details.",
        "Answered": "the receptionist answered from the confirmed business data on file.",
        "Callback logged": "the receptionist could not resolve it on the call and logged a callback for the owner.",
        "Couldn't answer": "no confirmed answer was on file, so the receptionist flagged it for the owner.",
        "Abandoned": "the caller hung up before the call finished.",
        "Error": "the call ended in an error.",
    }.get(outcome, "the call was handled.")
    tail = f" Callback reason: {callbacks[0].reason.replace('_', ' ')}." if callbacks else ""
    return (f"Caller asked: \"{ask}\". Route: {route}, {duration_label(call.duration_secs)} long. Result: {result}"
            f"{tail} Cost {money(call.cost_cents)}.")


def calls_payload(snapshot: Snapshot, settings: DashboardSettings, *, now: dt.datetime) -> list[dict]:
    by_call: dict[str, list[CallbackRecord]] = defaultdict(list)
    for cb in snapshot.callbacks:
        by_call[cb.call_id].append(cb)
    return [call_row(c, by_call, settings) for c in snapshot.calls]


# ---- callbacks + gaps -----------------------------------------------------------------------------------

def callback_row(cb: CallbackRecord, settings: DashboardSettings, *, now: dt.datetime) -> dict:
    cls = an.handoff_class(cb.reason, settings)
    status = {"open": "open", "in_progress": "in progress", "in progress": "in progress", "done": "done"}.get(cb.status, cb.status)
    return {
        "id": cb.id, "name": display_name(cb.name, cb.phone), "number": mask_phone(cb.phone),
        "question": cb.question or f"{cb.reason.replace('_', ' ').capitalize()} — no question recorded",
        "time": relative_day_label(cb.created_at, now, settings), "priority": "High" if cls == "planned" else "Medium",
        "status": status, "reason": cb.reason, "handoffClass": cls, "createdAt": cb.created_at.isoformat(),
        "callId": cb.call_id, "waitSecs": (now - cb.created_at).total_seconds() if status != "done" else None,
    }


def gap_rows(analytics: dict, settings: DashboardSettings, *, now: dt.datetime) -> list[dict]:
    rows = []
    for gap in analytics["gaps"]:
        last = dt.datetime.fromisoformat(gap["last_asked"])
        rows.append({"id": gap["key"], "text": gap["text"], "evidence": gap["evidence"],
                     "lastAsked": relative_day_label(last, now, settings), "review": gap["review"], "answer": gap["answer"],
                     "callIds": gap["call_ids"]})
    return rows


# ---- tiles ------------------------------------------------------------------------------------------------

def kpis(analytics: dict) -> dict:
    calls, bookings, outside = analytics["calls"], analytics["bookings"], analytics["outside_hours"]
    n = calls["n"]
    return {
        "kpiBookings": str(bookings["count"]), "kpiCovers": str(bookings["covers"]) + ("+" if bookings["covers_incomplete"] else ""),
        "kpiCalls": str(n),
        "kpiContainment": pct(calls["containment"]),
        "kpiContainmentMeta": f"{calls['contained']} of {n} calls, no callback requested" if n else "No calls in this period",
        "kpiOutside": str(outside["count"]), "kpiOutsidePct": pct(outside["share"]),
        "kpiOutsideMeta": f"started outside {outside['hours_label']}",
        "impact": {"bookings": {"compare": bookings["compare"]}, "calls": {"compare": calls["compare"]}},
    }


def impact_tiles(analytics: dict) -> list[dict]:
    calls, bookings, outside = analytics["calls"], analytics["bookings"], analytics["outside_hours"]
    n = calls["n"]
    return [
        {"kicker": "Calls handled", "headline": str(n), "sub": calls["compare"]},
        {"kicker": "Bookings & covers", "headline": f"{bookings['count']} / {bookings['covers']} covers", "sub": bookings["compare"]},
        {"kicker": "Containment", "headline": pct(calls["containment"]),
         "sub": f"{calls['contained']} of {n} calls, no callback requested" if n else "No calls in this period"},
        {"kicker": "Outside opening hours", "headline": f"{outside['count']} / {pct(outside['share'])}",
         "sub": f"started outside {outside['hours_label']}"},
    ]


def voice_ops_band_tiles(analytics: dict) -> list[dict]:
    calls, task, lat, err, vol, fb, ho, coh = (analytics[k] for k in ("calls", "task", "latency", "errors", "volume", "fallback", "handoffs", "coherence"))
    n = calls["n"]
    graded = task["success"] + task["failure"]
    tiles = [
        {"tier": "Business", "label": "Task success", "value": pct(task["rate"]),
         "note": f"{graded} of {n} graded · {task['failure']} failure{'s' if task['failure'] != 1 else ''} · {task['unknown']} unknown excluded", "band": task["band"]},
        {"tier": "Business", "label": "Containment", "value": pct(calls["containment"]),
         "note": f"{calls['contained']} of {n}, no callback requested", "band": calls["containment_band"]},
        {"tier": "Handoffs", "label": "Planned vs forced", "value": f"{pct(ho['planned_rate'])} / {pct(ho['forced_rate'])}",
         "note": f"{ho['planned']} planned · {ho['forced']} forced · {ho['unclassified']} unclassified of {n} calls", "band": ho["band"]},
        {"tier": "Quality", "label": "Answer latency p50 / p90",
         "value": (f"{lat['p50_ms']} / {lat['p90_ms']} ms" if lat["p50_ms"] is not None else "No data"),
         "note": f"{lat['samples']} agent turns · model answer stage, not caller-perceived wait",
         "band": lat["p90_band"] if lat["p90_ms"] is not None else an.NO_DATA},
        {"tier": "Quality", "label": "Conversation coherence", "value": pct(coh["rate"]), "note": coh["note"], "band": coh["band"]},
        {"tier": "Ops", "label": "Error rate", "value": pct(err["rate"]), "note": f"{err['failed']} of {n} · status coverage {n}/{n}", "band": err["band"]},
        {"tier": "Ops", "label": "Call volume", "value": f"{vol['per_day']:.1f} / day",
         "note": (f"{vol['multiple']:.2f}× baseline of {vol['baseline']:g}/day" if vol["multiple"] is not None else "no baseline configured"), "band": vol["band"]},
        {"tier": "Ops", "label": "Fallback rate", "value": pct(fb["rate"]),
         "note": f"{fb['gap_calls']} of {n} calls hit a knowledge gap", "band": fb["band"]},
    ]
    return tiles


def voice_ops_tiles(analytics: dict) -> list[dict]:
    """The four compact tiles at the top of the Voice AI calls screen."""
    return [{"label": t["label"], "value": t["value"], "note": t["note"]} for t in voice_ops_band_tiles(analytics)
            if t["label"] in ("Task success", "Containment", "Error rate", "Planned vs forced")]


def handoff_reason_bars(analytics: dict) -> list[dict]:
    reasons = analytics["handoffs"]["reasons"]
    total = max((r["count"] for r in reasons), default=0)
    colours = {"planned": "var(--color-accent)", "forced": "var(--color-accent-600)", "unclassified": "var(--color-neutral-400)"}
    bars = [{"label": f"{r['class'].title()} — {r['reason'].replace('_', ' ')}", "count": r["count"],
             "pct": round(r["count"] / total * 100) if total else 0, "color": colours[r["class"]]} for r in reasons]
    if not bars:
        bars = [{"label": "No callbacks in this period", "count": 0, "pct": 0, "color": colours["unclassified"]}]
    return bars


def route_mix(analytics: dict) -> dict:
    by_route = analytics["calls"]["by_route"]
    total = sum(by_route.values())
    legend, stops, cursor = [], [], 0.0
    for route in ROUTE_ORDER:
        count = by_route.get(route, 0)
        share = count / total * 100 if total else 0
        legend.append({"label": route, "count": count, "pct": round(share, 1), "color": ROUTE_COLOURS[route]})
        if share:
            stops.append(f"{ROUTE_COLOURS[route]} {cursor:.1f}% {cursor + share:.1f}%")
            cursor += share
    gradient = ", ".join(stops) if stops else "var(--color-neutral-200) 0% 100%"
    return {"legend": legend, "total": str(total),
            "donutStyle": ("width:88px;height:88px;border-radius:50%;flex:none;position:relative;"
                           f"background:conic-gradient({gradient})")}


# ---- business context (menu, packet, profile) -------------------------------------------------------------

def setup_payload(reader: BusinessReader, business_id: str, settings: DashboardSettings, *, now: dt.datetime) -> dict:
    ctx = load_business_context(reader, business_id)
    paths = BusinessPaths(business_id)
    facts = {key: (reader.get_doc(paths.fact(key)) or {}).get("value") for key in sorted(ctx.fact_keys)}
    menu_rows = [{"name": item.name, "ingredients": item.doc.get("description") or ", ".join(item.dietary_tags) or "—",
                  "price": item.price_spoken or "price not confirmed"} for item in ctx.items]
    allergens = sorted({a for item in ctx.items for a, entry in item.allergens.items() if entry.get("confirmed")})
    packet = [
        {"label": "Hours", "value": facts.get("hours") or settings.opening_hours_label()},
        {"label": "Location", "value": facts.get("address") or "not on file"},
        {"label": "Phone", "value": facts.get("phone") or "not on file"},
        {"label": "Seat capacity", "value": facts.get("seating") or "not on file"},
        {"label": "Known allergens on file", "value": ", ".join(a.replace("_", " ") for a in allergens) or "none confirmed"},
        {"label": "Socials", "value": facts.get("socials") or "not on file"},
    ]
    owner = facts.get("owner_name") or "Owner"
    address = facts.get("address") or ""
    suburb = _suburb(address)
    profile = [
        {"label": "Email", "value": facts.get("owner_email") or "not on file"},
        {"label": "Phone", "value": facts.get("phone") or "not on file"},
        {"label": "Business", "value": f"{ctx.business_name} · {address}" if address else ctx.business_name},
        {"label": "Timezone", "value": settings.business_timezone},
        {"label": "Agents active", "value": "Voice AI, Marketing, Overlord"},
    ]
    return {
        "businessName": ctx.business_name, "menuRows": menu_rows, "packetFields": packet, "profileFields": profile,
        "ownerName": owner, "ownerSub": f"Owner · {suburb}" if suburb else "Owner", "businessLocation": suburb or ctx.business_name,
        "menuCountLabel": f"Menu · {len(menu_rows)} item{'s' if len(menu_rows) != 1 else ''}",
        "menuMetaLabel": f"{len(menu_rows)} confirmed items · loaded {ago_label(ctx.loaded_at, now)}",
        "firstName": owner.split()[0] if owner else "there",
    }


def _suburb(address: str) -> str:
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if len(parts) >= 2:
        state = parts[-1].split()
        return f"{parts[-2]}, {state[0][:3].upper()}" if state else parts[-2]
    return ""


# ---- the whole page --------------------------------------------------------------------------------------

def freshness(analytics: dict, *, now: dt.datetime, settings: DashboardSettings) -> dict:
    as_of = dt.datetime.fromisoformat(analytics["freshness"]["as_of"])
    return {"asOf": ago_label(as_of, now), "asOfIso": as_of.isoformat(), "timezone": settings.business_timezone,
            "staleAfterMinutes": settings.stale_after_minutes, "stale": analytics["freshness"]["stale"]}


def analytics_payload(analytics: dict, settings: DashboardSettings, *, now: dt.datetime) -> dict:
    """Everything the Analytics + Overview tiles need for one period."""
    return {
        "period": analytics["period"],
        "kpis": kpis(analytics),
        "impactTiles": impact_tiles(analytics),
        "voiceOpsBandTiles": voice_ops_band_tiles(analytics),
        "voiceOpsTiles": voice_ops_tiles(analytics),
        "handoffReasonBars": handoff_reason_bars(analytics),
        "openCallbacksList": [{"name": display_name(c["name"], c["phone"]), "wait": wait_label(c["wait_secs"])} for c in analytics["open_callbacks"]],
        "oldestCallbackWait": wait_label(analytics["oldest_wait_secs"]) + (" ⚠" if analytics["oldest_wait_warn"] else ""),
        "routeMix": route_mix(analytics),
        "gaps": gap_rows(analytics, settings, now=now),
        "freshness": freshness(analytics, now=now, settings=settings),
        "dataSourceLabel": "Demo data" if analytics["demo"] else "Live data",
        "cost": analytics["cost"], "minutes": analytics["minutes"], "raw": {k: analytics[k] for k in ("calls", "task", "latency", "errors", "volume", "fallback", "handoffs", "bookings", "outside_hours")},
    }
