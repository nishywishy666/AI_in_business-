#!/usr/bin/env python3
"""Seed a realistic week of Uncle Tony calls through the REAL turn engine (plan 0005).

    uv run python scripts/seed_demo_calls.py                  # local sink → .testruns/*.jsonl
    uv run python scripts/seed_demo_calls.py --reset          # wipe previous local runs first
    uv run python scripts/seed_demo_calls.py --sink firestore # write to the configured Firestore project
    uv run python scripts/seed_demo_calls.py --days 3 --fake-gemini

Every call runs `ReceptionistTurnEngine.run_turn` with a scripted router output per caller turn
(what Groq would have returned) — the booking machine, lookups, callbacks and turn logs are the
production code path (R7). Gemini phrasing uses the real key when GEMINI_API_KEY is set, otherwise
a deterministic fake. Call ids start with `CAsim`, so the dashboard labels the result "Demo data".
With `--sink firestore` bookings are recorded through the local committer: capacitySlots are never
touched, so demo seats are not consumed.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import secrets
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import load_yaml  # noqa: E402
from contracts.voice import RouterOutput  # noqa: E402
from services.booking.calendar import NullCalendar  # noqa: E402
from services.booking.capacity import parse_windows  # noqa: E402
from services.booking.commit import LocalCommitter  # noqa: E402
from services.booking.email import LogMailer  # noqa: E402
from services.common.firestore import BusinessPaths  # noqa: E402
from services.voice.answerer import FakeAnswerTransport  # noqa: E402
from services.voice.booking_machine import BookingDeps, BookingMachine  # noqa: E402
from services.voice.engine import EngineDeps, ReceptionistTurnEngine  # noqa: E402
from services.voice.pipeline import _call_start_doc  # noqa: E402
from services.voice.router import FakeRouterTransport  # noqa: E402
from services.voice.session import CallSession  # noqa: E402
from services.voice.sinks import LocalJsonlSink  # noqa: E402
from services.voice.telemetry import HudBus, TurnRecorder  # noqa: E402
from services.voice.tools import JsonBusinessReader  # noqa: E402

UTC = dt.timezone.utc


def router(intent: str, *, confidence: float = 0.94, question: str | None = None, field: str | None = None,
           value: str | None = None, wants_human: bool = False) -> dict:
    return {"intent": intent, "confidence": confidence, "question": question, "field_name": field, "field_value": value,
            "wants_human": wants_human}


# ---- scenarios: (caller text, scripted router output) per caller turn --------------------------------

def booking(name: str, party: str, when_word: str, time_text: str, email: str | None, *, opener: str | None = None) -> list[tuple[str, dict]]:
    turns = [
        (opener or f"Hi, can I get a table for {party} {when_word}?", router("BOOK", field="date", value=when_word)),
        (time_text, router("BOOK", field="time", value=time_text)),
        (party, router("BOOK", field="party_size", value=party)),
        (name, router("BOOK", field="name", value=name)),
        ("Yes, that's the best number.", router("BOOK", confidence=0.9)),
    ]
    # TODO(spec): the booking machine has no "no email, thanks" path yet, so every demo booking supplies one.
    turns += [(email or "no-reply@example.com", router("BOOK", field="email_raw", value=email or "no-reply@example.com")),
              ("Yes, that's right.", router("BOOK", confidence=0.9))]
    turns += [("Yes please, lock it in.", router("BOOK", confidence=0.95)), ("Great, thanks, bye!", router("END", confidence=0.98))]
    return turns


def question(text: str, *, close: bool = True) -> list[tuple[str, dict]]:
    turns = [(text, router("ANSWER_QUESTION", question=text))]
    if close:
        turns.append(("Perfect, thanks. Bye.", router("END", confidence=0.97)))
    return turns


def callback(text: str) -> list[tuple[str, dict]]:
    return [(text, router("CALLBACK", confidence=0.91)), ("Thanks, bye.", router("END", confidence=0.97))]


def chitchat(text: str) -> list[tuple[str, dict]]:
    return [(text, router("CHITCHAT", confidence=0.88)), ("Cheers, bye!", router("END", confidence=0.97))]


def day_scenarios(local_date: dt.date, *, full: bool) -> list[dict]:
    """A day of calls. `full` = the eight-call day the mockup shows; other days get a lighter mix."""
    weekday = local_date.weekday()
    when = "today" if weekday < 6 else "Monday"
    sat = "Saturday" if weekday != 5 else "next Saturday"
    scenarios = [
        {"time": "08:12", "from": "+61412555210", "turns": booking("Sarah", "two", when, "12:30", "sarah.k@gmail.com",
                                                                    opener=f"Hi, can I get a table for two around 12:30 {when}?")},
        {"time": "08:47", "from": "+61432118764", "turns": question("Do you have anything gluten-free?")},
        {"time": "09:15", "from": "+61400111222", "turns": question("What time do you open on Saturdays?")},
        {"time": "10:02", "from": "+61432118764", "turns": callback("I need catering for about 30 people on Saturday, is that something you do?")},
    ]
    if full:
        scenarios += [
            {"time": "11:30", "from": "+61398221004", "turns": booking("James", "six", sat, "1pm", "james@gmail.com",
                                                                        opener=f"Table for 6 this {sat}, 1pm?")},
            {"time": "12:05", "from": "+61411000999", "turns": chitchat("Just checking you're open, that's all!")},
            {"time": "12:40", "from": "+61398221004", "turns": question("Do you do dairy-free toasties?")},
            {"time": "13:20", "from": "+61411887320", "turns": booking("Owen", "four", when, "8:30", "owen@outlook.com",
                                                                        opener=f"Table for 4 {when} morning, around 8:30?")},
            {"time": "13:45", "from": "+61455000111", "turns": question("How much is the Uncle Tony?")},
            {"time": "14:05", "from": "+61466123123", "turns": question("Do you do delivery?")},
        ]
    else:
        scenarios += [
            {"time": "12:20", "from": "+61455000111", "turns": question("Where are you located?")},
            {"time": "13:05", "from": "+61466123123", "turns": question("Do you do delivery?")},
        ]
    return scenarios


class SeedClock:
    def __init__(self, start: dt.datetime) -> None:
        self.now = start

    def __call__(self) -> dt.datetime:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now = self.now + dt.timedelta(seconds=seconds)


def build_engine(reader, sink, clock, business_id: str, tz: str, windows: list[dict], answerer) -> ReceptionistTurnEngine:
    paths = BusinessPaths(business_id)
    booking_machine = BookingMachine(BookingDeps(reader=reader, sink=sink, committer=LocalCommitter(reader, paths, sink),
                                                 mailer=LogMailer(), calendar=NullCalendar(), windows=parse_windows(windows), tz=tz),
                                     clock=clock)
    deps = EngineDeps(reader=reader, sink=sink, bus=HudBus(), router=FakeRouterTransport(), answerer=answerer,
                      business_id=business_id, tz=tz, booking=booking_machine, clock=clock, windows=windows)
    return ReceptionistTurnEngine(deps)


def _outcome(session: CallSession) -> str:
    # mirrors services/voice/call_pipeline._outcome without importing the audio stack
    if session.slot_state.stage == "done":
        return "booked"
    if session.pending_callback is not None:
        return "callback"
    if session.ended:
        return "answered"
    return "abandoned" if session.turn_index == 0 else "answered"


async def run_call(engine: ReceptionistTurnEngine, sink, clock: SeedClock, *, business_id: str, tz: str,
                   from_number: str, turns: list[tuple[str, dict]]) -> dict:
    call_id = f"CAsim{secrets.token_hex(8)}"
    session = CallSession(call_id=call_id, stream_sid=f"MZsim{secrets.token_hex(6)}", business_id=business_id,
                          from_number=from_number, started_at=clock(), disclosure_done=True)
    sink.write_call(call_id, _call_start_doc(session))
    recorder = TurnRecorder(sink, engine.deps.bus, call_id)
    engine.deps.router.scripts = [json.dumps(output) for _, output in turns]
    clock.tick(6)
    ended = False
    for text, _ in turns:
        if ended:
            break
        clock.tick(4 + len(text) / 12)
        result = await engine.run_turn(session, text, recorder=recorder)
        clock.tick(2 + len(result.reply_text) / 14)
        ended = result.end_call
    engine.forget(call_id)
    now = clock()
    local = now.astimezone(ZoneInfo(tz))
    outcome = _outcome(session)
    duration = session.elapsed_ms(now)
    doc = {"endedAt": now.isoformat(), "durationMs": duration, "outcome": outcome, "costCents": 0,
           "hourLocal": local.hour, "weekdayLocal": local.strftime("%a").lower(), "turns": session.turn_index,
           **session.to_resume_doc()}
    sink.write_call(call_id, doc, merge=True)
    sink.increment_rollup(local.date().isoformat(), {"calls": 1, f"outcome_{outcome}": 1, "durationMs": duration,
                                                     "costCents": 0, "bookings": 1 if session.slot_state.stage == "done" else 0})
    return {"call_id": call_id, "outcome": outcome, "turns": session.turn_index, "duration_ms": duration}


def seed(*, sink, reader, business_id: str, tz: str, windows: list[dict], answerer, days: int = 7,
         today: dt.date | None = None, quiet: bool = False) -> list[dict]:
    zone = ZoneInfo(tz)
    today = today or dt.datetime.now(zone).date()
    results = []
    for offset in range(days - 1, -1, -1):
        local_date = today - dt.timedelta(days=offset)
        for scenario in day_scenarios(local_date, full=offset in (0, 1)):
            hour, minute = (int(x) for x in scenario["time"].split(":"))
            start = dt.datetime.combine(local_date, dt.time(hour, minute), tzinfo=zone).astimezone(UTC)
            if start > dt.datetime.now(UTC) and offset == 0:
                continue  # never seed calls in the future
            clock = SeedClock(start)
            engine = build_engine(reader, sink, clock, business_id, tz, windows, answerer)
            result = asyncio.run(run_call(engine, sink, clock, business_id=business_id, tz=tz, from_number=scenario["from"],
                                          turns=scenario["turns"]))
            result["local_date"] = local_date.isoformat()
            results.append(result)
            if not quiet:
                print(f"  {local_date} {scenario['time']}  {result['outcome']:<9} {result['turns']:>2} turns  {result['call_id']}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sink", choices=["local", "firestore"], default="local")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--reset", action="store_true", help="delete previous .testruns/*.jsonl first (local sink only)")
    parser.add_argument("--fake-gemini", action="store_true", help="use the deterministic answer fake even if GEMINI_API_KEY is set")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()
    if args.sink == "firestore":
        from services.common.config import load_config
        from services.voice.sinks import make_sink

        config = load_config()
        sink = make_sink(config)
    else:
        from services.common.config import local_config

        config = local_config()
        root = Path(".testruns")
        if args.reset and root.exists():
            for path in root.glob("*.jsonl"):
                path.unlink()
        sink = LocalJsonlSink(root, business_id=config.business_id)

    from dashboard.settings import get_settings

    dash = get_settings()
    windows = list((load_yaml("capacity.yaml") or {}).get("windows") or [])
    reader = JsonBusinessReader(dash.dataset_dir, config.business_id, windows=windows)
    answerer = FakeAnswerTransport()
    if not args.fake_gemini and os.environ.get("GEMINI_API_KEY"):
        try:
            from services.voice.answerer import GenAiAnswerTransport

            answerer = GenAiAnswerTransport.resolve(os.environ["GEMINI_API_KEY"])
            print(f"Gemini phrasing: {answerer.model_id}")
        except Exception as exc:
            print(f"Gemini unavailable ({exc}); using the fake answerer")
    print(f"seeding {args.days} days of calls for {config.business_id} into the {sink.kind} sink")
    results = seed(sink=sink, reader=reader, business_id=config.business_id, tz=config.tz_business, windows=windows,
                   answerer=answerer, days=args.days)
    outcomes = {}
    for r in results:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    print(f"{len(results)} calls: {outcomes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
