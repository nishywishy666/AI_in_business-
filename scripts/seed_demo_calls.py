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


# ---- the realistic profile: what a toastie shop in Eaton Mall actually gets asked (plan 0012) ---------
# One persona per number. Rotated by day so a week has variety and nothing repeats daily.

def realistic_pool(when: str, sat: str) -> list[dict]:
    return [
        # bookings
        {"from": "+61412555210", "turns": booking("Priya", "two", when, "12:15", "priya.n@gmail.com",
                                                  opener=f"Hi, could I book a table for two at 12:15 {when}?")},
        {"from": "+61398221004", "turns": booking("Marco", "four", sat, "1pm", "marco.r@outlook.com",
                                                  opener=f"Table for four this {sat} at 1?")},
        {"from": "+61411887320", "turns": booking("Hannah", "three", when, "8:45", "hannah.lee@gmail.com",
                                                  opener=f"Can I grab a table for three {when} at 8:45? It's a breakfast meeting.")},
        {"from": "+61433908871", "turns": booking("Dev", "six", sat, "12:30", "dev.patel@gmail.com",
                                                  opener=f"Six of us for lunch this {sat}, 12:30 if you can.")},
        {"from": "+61444213377", "turns": booking("Liam", "two", when, "1pm", "liam.oc@gmail.com",
                                                  opener=f"Hey, any chance of a table for two at 1 {when}?")},
        {"from": "+61456781234", "turns": booking("Aisha", "five", sat, "11:30", "aisha.m@outlook.com",
                                                  opener=f"Could I book five people for {sat} at 11:30, please?")},
        # questions the receptionist answers from facts and the menu
        {"from": "+61400111222", "turns": question("Is there parking near you?")},
        {"from": "+61466123123", "turns": question("Are you on Uber Eats, or do you deliver?")},
        {"from": "+61455000111", "turns": question("Can I order ahead and pick up at twelve?")},
        {"from": "+61421334455", "turns": question("Are you open on the public holiday Monday?")},
        {"from": "+61407556677", "turns": question("Do you do coffee as well, or just toasties?")},
        {"from": "+61418223344", "turns": question("Can I bring my dog if we sit outside?")},
        {"from": "+61439887766", "turns": question("Do you have high chairs?")},
        {"from": "+61402998877", "turns": question("Do you take card, or is it cash only?")},
        {"from": "+61415667788", "turns": question("Is the shop wheelchair accessible?")},
        {"from": "+61424556699", "turns": question("Do you sell gift cards?")},
        {"from": "+61431776655", "turns": question("Are you BYO?")},
        {"from": "+61409112233", "turns": question("How busy are you around one o'clock?")},
        {"from": "+61436445566", "turns": question("Are you hiring at the moment?")},
        {"from": "+61428334455", "turns": question("Do you have any vegan options?")},
        {"from": "+61403667788", "turns": question("What time do you close today?")},
        {"from": "+61417889900", "turns": question("Where exactly are you in Oakleigh?")},
        {"from": "+61455000111", "turns": question("How much is the Uncle Tony?")},
        {"from": "+61426778899", "turns": question("What's in the Frank Fungini?")},
        {"from": "+61438990011", "turns": question("Do you do catering platters for an office lunch?")},
        {"from": "+61401223344", "turns": question("Do you take bookings, or is it just walk-in?")},
        # gaps: allergen-shaped or off-menu, so the receptionist takes a callback rather than guess
        {"from": "+61432118764", "turns": question("Do you have anything gluten-free?")},
        {"from": "+61419445566", "turns": question("Do you have oat milk?")},
        {"from": "+61427556677", "turns": question("Is the Luca Brasi nut free?")},
        {"from": "+61434667788", "turns": question("Do you do breakfast, like eggs on toast?")},
        # explicit callbacks
        {"from": "+61432118764", "turns": callback("I need catering for about 30 people on Saturday, is that something you do?")},
        {"from": "+61440778899", "turns": callback("It's a birthday for 15 people next Friday, can I talk to someone about a set menu?")},
        {"from": "+61411000999", "turns": callback("I ordered two toasties yesterday and both were cold. I'd like to speak to the owner.")},
        {"from": "+61395551234", "turns": callback("It's Dean from the bakery wholesale. Can Tony call me back about next week's bread order?")},
        # chitchat
        {"from": "+61411000999", "turns": chitchat("Just checking you're open, that's all!")},
        {"from": "+61498001122", "turns": chitchat("Oh sorry, wrong number!")},
        {"from": "+61452334455", "turns": chitchat("Hey, are you the toastie place from TikTok? Love your stuff.")},
    ]


_TIMES = ["07:48", "08:12", "08:35", "08:47", "09:15", "09:40", "10:02", "10:30", "11:05", "11:30", "12:05", "12:20",
          "12:40", "13:05", "13:20", "13:45", "14:05", "14:25"]


def realistic_day_scenarios(local_date: dt.date, *, offset: int) -> list[dict]:
    """`offset` days before today. Recent days are fuller; Sunday (closed) gets a couple of stragglers.
    The pool is walked with a stride so consecutive days do not share a run of questions."""
    weekday = local_date.weekday()
    when = "today" if weekday < 6 else "Monday"
    sat = "Saturday" if weekday != 5 else "next Saturday"
    pool = realistic_pool(when, sat)
    count = 2 if weekday == 6 else 9 if offset <= 1 else 6
    start = (offset * 7) % len(pool)
    picks = [pool[(start + i * 5) % len(pool)] for i in range(count)]
    if weekday == 6:  # no bookings on a closed day
        not_booking = [p for p in pool if p["turns"][0][1]["intent"] != "BOOK"]
        picks = [p for p in picks if p["turns"][0][1]["intent"] != "BOOK"] or not_booking[:1]
    step = max(1, len(_TIMES) // max(1, len(picks)))
    return [{"time": _TIMES[min(i * step + (offset % step), len(_TIMES) - 1)], **pick} for i, pick in enumerate(picks)]


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
         today: dt.date | None = None, quiet: bool = False, profile: str = "mockup") -> list[dict]:
    """`profile="mockup"` is the eight-call day the design shows (the dashboard tests pin its numbers);
    `"realistic"` rotates the wider pool above."""
    zone = ZoneInfo(tz)
    today = today or dt.datetime.now(zone).date()
    results = []
    for offset in range(days - 1, -1, -1):
        local_date = today - dt.timedelta(days=offset)
        scenarios = (realistic_day_scenarios(local_date, offset=offset) if profile == "realistic"
                     else day_scenarios(local_date, full=offset in (0, 1)))
        for scenario in scenarios:
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


def reset_firestore_demo(client, business_id: str) -> None:
    """Remove a previous demo seed from Firestore. Refuses to run if any call is not a `CAsim*` demo
    call, so real traffic can never be deleted by a reseed."""
    paths = BusinessPaths(business_id)
    calls = list(client.collection(paths.calls).stream())
    real = [c.id for c in calls if not c.id.startswith("CAsim")]
    if real:
        sys.exit(f"refusing --reset: {len(real)} non-demo call(s) present, e.g. {real[0]}")
    refs = []
    for call in calls:
        refs.extend(t.reference for t in client.collection(paths.turns(call.id)).stream())
        refs.append(call.reference)
    for coll in (paths.callbacks, paths.bookings, paths.emails_sent):
        refs.extend(d.reference for d in client.collection(coll).stream()
                    if str((d.to_dict() or {}).get("callId") or "CAsim").startswith("CAsim"))
    refs.extend(d.reference for d in client.collection(paths.rollups).stream())
    for i in range(0, len(refs), 400):
        batch = client.batch()
        for ref in refs[i:i + 400]:
            batch.delete(ref)
        batch.commit()
    print(f"reset: deleted {len(calls)} demo calls and {len(refs) - len(calls)} dependent documents")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sink", choices=["local", "firestore"], default="local")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--reset", action="store_true",
                        help="delete previous demo calls first: .testruns/*.jsonl locally, or every CAsim* call (and what "
                             "references it) in Firestore — refused if any real call is present")
    parser.add_argument("--fake-gemini", action="store_true", help="use the deterministic answer fake even if GEMINI_API_KEY is set")
    parser.add_argument("--profile", choices=["realistic", "mockup"], default="realistic",
                        help="realistic = the rotated pool of everyday questions (plan 0012); mockup = the design's eight-call day")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()
    if args.sink == "firestore":
        from services.common.config import load_config
        from services.voice.sinks import make_sink

        config = load_config()
        sink = make_sink(config)
        if args.reset:
            reset_firestore_demo(sink.client, config.business_id)
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
    # Slots must exist for the days being seeded, or every past-day booking bails to a callback
    # (the JSON reader materialises capacity from `today` forward).
    first_day = dt.datetime.now(ZoneInfo(config.tz_business)).date() - dt.timedelta(days=args.days)
    reader = JsonBusinessReader(dash.dataset_dir, config.business_id, windows=windows, today=first_day,
                                slot_days=args.days + 60)
    answerer = FakeAnswerTransport()
    if not args.fake_gemini and os.environ.get("GEMINI_API_KEY"):
        try:
            from services.voice.answerer import GenAiAnswerTransport

            answerer = GenAiAnswerTransport.resolve(os.environ["GEMINI_API_KEY"])
            print(f"Gemini phrasing: {answerer.model_id}")
        except Exception as exc:
            print(f"Gemini unavailable ({exc}); using the fake answerer")
    print(f"seeding {args.days} days of {args.profile} calls for {config.business_id} into the {sink.kind} sink")
    results = seed(sink=sink, reader=reader, business_id=config.business_id, tz=config.tz_business, windows=windows,
                   answerer=answerer, days=args.days, profile=args.profile)
    outcomes = {}
    for r in results:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    print(f"{len(results)} calls: {outcomes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
