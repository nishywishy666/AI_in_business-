"""Shared builders for the voice tests: a MemoryReader seeded with the TEST-ONLY business docs,
and an engine wired with fake Groq/Gemini transports. Reads go through the same tools.py code
production uses; only the transports are fakes."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from services.common.firestore import BusinessPaths
from services.voice.answerer import FakeAnswerTransport
from services.voice.engine import EngineDeps, ReceptionistTurnEngine
from services.voice.router import FakeRouterTransport
from services.voice.sinks import LocalJsonlSink
from services.voice.telemetry import HudBus
from services.voice.tools import MemoryReader

FIXTURES = Path(__file__).parent / "fixtures" / "business"
BUSINESS_ID = "biz_test"
FIXED_NOW = dt.datetime(2026, 9, 14, 1, 0, tzinfo=dt.timezone.utc)  # Monday 11:00 in Melbourne


def business_docs(business_id: str = BUSINESS_ID, *, extra: dict | None = None) -> dict[str, dict]:
    paths = BusinessPaths(business_id)
    docs: dict[str, dict] = {}
    for item_id, doc in json.loads((FIXTURES / "menu_items.json").read_text()).items():
        docs[paths.menu_item(item_id)] = doc
    for key, doc in json.loads((FIXTURES / "facts.json").read_text()).items():
        docs[paths.fact(key)] = doc
    slots_file = FIXTURES / "capacity_slots.json"
    if slots_file.exists():
        for slot_id, doc in json.loads(slots_file.read_text()).items():
            docs[paths.capacity_slot(slot_id)] = doc
    docs.update(extra or {})
    return docs


# TEST-ONLY service windows (config/capacity.yaml is TODO(spec)). Lunch Tue–Sat, dinner Thu–Sat.
TEST_WINDOWS = [
    {"days": ["tue", "wed", "thu", "fri", "sat"], "open": "12:00", "close": "14:30", "slot_minutes": 30, "seats_total": 40},
    {"days": ["thu", "fri", "sat"], "open": "17:30", "close": "21:00", "slot_minutes": 30, "seats_total": 40},
]


def make_engine(tmp_path, *, router: FakeRouterTransport | None = None, answerer: FakeAnswerTransport | None = None,
                docs: dict | None = None, booking=None, windows: list[dict] | None = None,
                clock=lambda: FIXED_NOW, with_booking: bool = False, mailer=None, calendar=None):
    reader = MemoryReader(docs if docs is not None else business_docs())
    sink = LocalJsonlSink(Path(tmp_path) / ".testruns", business_id=BUSINESS_ID)
    windows = windows if windows is not None else TEST_WINDOWS
    if booking is None and with_booking:
        from services.booking.calendar import NullCalendar
        from services.booking.capacity import parse_windows
        from services.booking.commit import LocalCommitter
        from services.booking.email import LogMailer
        from services.voice.booking_machine import BookingDeps, BookingMachine

        booking = BookingMachine(BookingDeps(reader=reader, sink=sink, committer=LocalCommitter(reader, BusinessPaths(BUSINESS_ID), sink),
                                             mailer=mailer or LogMailer(), calendar=calendar or NullCalendar(),
                                             windows=parse_windows(windows)), clock=clock)
    deps = EngineDeps(reader=reader, sink=sink, bus=HudBus(), router=router or FakeRouterTransport(),
                      answerer=answerer or FakeAnswerTransport(), business_id=BUSINESS_ID, booking=booking,
                      clock=clock, windows=windows)
    return ReceptionistTurnEngine(deps), deps
