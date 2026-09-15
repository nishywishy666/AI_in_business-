import asyncio
import datetime as dt
import json

import pytest

from config import threshold
from services.booking import capacity
from services.booking.calendar import FailingCalendar, NullCalendar
from services.booking.email import LogMailer
from services.voice.booking_machine import parse_date, parse_party_size, parse_phone, parse_time
from services.voice.router import FakeRouterTransport
from services.voice.session import CallSession
from services.voice.telemetry import TurnRecorder
from tests.voice_helpers import BUSINESS_ID, FIXED_NOW, TEST_WINDOWS, make_engine

MELB = dt.timezone(dt.timedelta(hours=10))
NOW_LOCAL = FIXED_NOW.astimezone(MELB)  # Monday 2026-09-14 11:00


def _router(intent="BOOK", field=None, value=None, confidence=0.9):
    return json.dumps({"intent": intent, "confidence": confidence, "question": None, "field_name": field,
                       "field_value": value, "wants_human": False})


class Conversation:
    def __init__(self, tmp_path, *, from_number="+61400111222", mailer=None, calendar=None):
        self.router = FakeRouterTransport()
        self.mailer = mailer or LogMailer()
        self.calendar = calendar or NullCalendar()
        self.engine, self.deps = make_engine(tmp_path, router=self.router, with_booking=True,
                                             mailer=self.mailer, calendar=self.calendar)
        self.session = CallSession(call_id="CAsimbook", stream_sid="MZsimbook", business_id=BUSINESS_ID, from_number=from_number)
        self.recorder = TurnRecorder(self.deps.sink, self.deps.bus, self.session.call_id)
        self.loop = asyncio.new_event_loop()

    def say(self, text, *, intent="BOOK", field=None, value=None, confidence=0.9):
        self.router.scripts.append(_router(intent, field, value, confidence))
        return self.loop.run_until_complete(self.engine.run_turn(self.session, text, recorder=self.recorder))

    @property
    def slot(self):
        return self.session.slot_state

    def callbacks(self):
        return [r["doc"] for r in self.deps.sink.read(self.session.call_id) if r["kind"] == "callback"]

    def bookings(self):
        return [r["doc"] for r in self.deps.sink.read(self.session.call_id) if r["kind"] == "booking"]


# ---- parsers -----------------------------------------------------------------------------------

def test_parse_date_handles_relative_absolute_and_vague():
    assert parse_date("tomorrow", NOW_LOCAL) == dt.date(2026, 9, 15)
    assert parse_date("this saturday", NOW_LOCAL) == dt.date(2026, 9, 19)
    assert parse_date("next monday", NOW_LOCAL) == dt.date(2026, 9, 21)
    assert parse_date("the 19th", NOW_LOCAL) == dt.date(2026, 9, 19)
    assert parse_date("19 september", NOW_LOCAL) == dt.date(2026, 9, 19)
    assert parse_date("19/9", NOW_LOCAL) == dt.date(2026, 9, 19)
    assert parse_date("this weekend", NOW_LOCAL) is None
    assert parse_date("sometime next week", NOW_LOCAL) is None
    assert parse_date("1/9", NOW_LOCAL) is None, "past dates are rejected"


def test_parse_time_and_party_and_phone():
    assert parse_time("7pm") == dt.time(19, 0)
    assert parse_time("seven") == dt.time(19, 0)
    assert parse_time("half past seven") == dt.time(19, 30)
    assert parse_time("quarter to eight in the evening") == dt.time(19, 45)
    assert parse_time("12:30") == dt.time(12, 30)
    assert parse_time("ten in the morning") == dt.time(10, 0)
    assert parse_time("evening") is None and parse_time("sometime") is None
    assert parse_party_size("table for four") == 4 and parse_party_size("there'll be 6 of us") == 6
    assert parse_party_size("just me") == 1 and parse_party_size("hmm") is None
    assert parse_phone("0400 111 222") == "+61400111222" and parse_phone("+61 400 111 222") == "+61400111222"
    assert parse_phone("zero four zero zero one one one two two two") == "+61400111222"
    assert parse_phone("123") is None


# ---- machine -----------------------------------------------------------------------------------

def test_happy_path_offers_caller_id_and_books_with_email_and_calendar(tmp_path):
    c = Conversation(tmp_path)
    assert c.say("can I book a table?").reply_text == "What day would you like?"
    assert c.say("this saturday", field="date", value="this saturday").reply_text == "And what time?"
    assert c.slot.date == dt.date(2026, 9, 19)
    assert c.say("seven thirty", field="time", value="seven thirty").reply_text == "How many people?"
    r = c.say("four of us", field="party_size", value="four of us")
    assert r.tool_called == "check_availability" and r.tool_result["open"] is True
    assert r.reply_text == "What name is it under?"
    r = c.say("Sarah", field="name", value="Sarah")
    assert r.reply_text == "Is the number you're calling from the best one?", "caller ID is offered, never asked for"
    assert c.say("yep").reply_text == "And what's the best email for you?"
    assert c.slot.phone == "+61400111222"
    r = c.say("sarah dot chen at gmail dot con", field="email_raw", value="sarah dot chen at gmail dot con")
    assert r.reply_text == "Let me check I've got that — sarah dot chen at gmail dot com?"
    assert r.email_candidate["domain_score"] == 0.85
    r = c.say("yes")
    assert r.reply_text.startswith("So that's a table for 4 on Saturday at half past seven, under Sarah.")
    assert c.slot.stage == "confirm"
    r = c.say("yes please")
    assert c.slot.stage == "done" and r.tool_called == "commit_booking"
    assert r.reply_text == "One moment while I lock that in. You're booked. A confirmation email is on its way to you."
    assert r.tool_result == {"outcome": "created", "emailed": True, "calendar_mirrored": True, "email_capture_failed": False}
    booking = c.bookings()[0]
    assert booking["partySize"] == 4 and booking["email"] == "sarah.chen@gmail.com" and booking["slotId"] == "2026-09-19T1930"
    assert c.mailer.sent[0]["to"] == "sarah.chen@gmail.com" and "Toastie Test Kitchen" in c.mailer.sent[0]["subject"]
    assert c.calendar.calls[0]["idempotencyKey"] == booking["idempotencyKey"]
    sent = [r for r in c.deps.sink.read(c.session.call_id) if r["kind"] == "emailSent"]
    assert len(sent) == 1 and sent[0]["idempotencyKey"] == booking["idempotencyKey"]
    assert not any(call["system"] for call in c.deps.answerer.calls), "booking turns never touch Gemini"


def test_vague_date_and_vague_time_reask_once_then_callback(tmp_path):
    c = Conversation(tmp_path)
    c.say("book a table")
    r = c.say("this weekend", field="date", value="this weekend")
    assert r.reply_text.startswith("Sorry, which day exactly") and c.slot.attempts["date"] == 1
    assert c.say("saturday", field="date", value="saturday").reply_text == "And what time?"
    r = c.say("in the evening", field="time", value="in the evening")
    assert r.reply_text.startswith("We could do") and "or" in r.reply_text
    assert c.slot.offered_alternatives == ["17:30", "18:00"]
    assert c.say("six", field="time", value="six").reply_text == "How many people?"
    assert c.slot.time == dt.time(18, 0)

    c2 = Conversation(tmp_path)
    c2.say("book a table")
    c2.say("whenever", field="date", value="whenever")
    c2.say("sometime", field="date", value="sometime")
    r = c2.say("any day", field="date", value="any day")
    assert r.tool_called == "take_callback" and c2.callbacks()[0]["reason"] == "other"
    assert c2.slot.stage == "idle"


def test_over_max_party_routes_to_large_group_callback(tmp_path):
    c = Conversation(tmp_path)
    c.say("book a table")
    c.say("saturday", field="date", value="saturday")
    c.say("7pm", field="time", value="7pm")
    r = c.say(f"{threshold('MAX_PARTY') + 1} people", field="party_size", value=f"{threshold('MAX_PARTY') + 1} people")
    assert c.callbacks()[0]["reason"] == "large_group" and "call you back" in r.reply_text
    assert c.slot.stage == "idle"


def test_full_slot_offers_real_alternatives_with_seats(tmp_path):
    c = Conversation(tmp_path)
    c.say("book a table")
    c.say("saturday", field="date", value="saturday")
    c.say("half past six", field="time", value="half past six")  # 18:30 is 40/40
    r = c.say("2", field="party_size", value="2")
    assert r.tool_result["open"] is False
    alts = r.tool_result["alternatives"]
    assert 2 <= len(alts) <= 3 and alts[0] == "2026-09-19T1900" and "2026-09-19T1800" in alts
    for slot_id in alts:
        doc = c.deps.reader.get_doc(f"businesses/{BUSINESS_ID}/capacitySlots/{slot_id}")
        assert doc["seatsBooked"] + 2 <= doc["seatsTotal"]
    assert r.reply_text.startswith("That time's full. I could do seven") and "or" in r.reply_text
    assert c.slot.stage == "time"
    assert c.say("seven", field="time", value="seven").reply_text == "How many people?"


def test_four_seats_left_rejects_party_of_five_but_takes_four(tmp_path):
    c = Conversation(tmp_path)
    c.say("book a table"); c.say("saturday", field="date", value="saturday"); c.say("7pm", field="time", value="7pm")
    r = c.say("five", field="party_size", value="five")
    assert r.tool_result["open"] is False and "2026-09-19T1900" not in r.tool_result["alternatives"]
    c2 = Conversation(tmp_path)
    c2.say("book a table"); c2.say("saturday", field="date", value="saturday"); c2.say("7pm", field="time", value="7pm")
    assert c2.say("four", field="party_size", value="four").tool_result["open"] is True


def test_email_spelling_flow_and_two_failures_still_book(tmp_path):
    c = Conversation(tmp_path)
    c.say("book a table"); c.say("saturday", field="date", value="saturday"); c.say("7pm", field="time", value="7pm")
    c.say("two", field="party_size", value="two"); c.say("Jo", field="name", value="Jo"); c.say("yes")
    r = c.say("jo at gmail dot com", field="email_raw", value="jo at gmail dot com")
    assert r.reply_text.startswith("I want to get this right — can you spell the part before the at symbol")
    assert c.session.email_candidate["spell_attempts"] == 1
    r = c.say("j o")
    assert r.reply_text == "Let me check I've got that — jo at gmail dot com?"  # no confusable letters
    r = c.say("no that's wrong")
    assert r.reply_text.startswith("I want to get this right") and c.session.email_candidate["spell_attempts"] == 2
    r = c.say("b o b")
    assert r.reply_text == "B for bravo?"
    r = c.say("no")
    assert c.slot.stage == "confirm" and c.slot.email is None and "email capture failed" in r.warnings[0]
    r = c.say("yes")
    assert c.slot.stage == "done"
    assert r.reply_text.endswith("You're booked. I'll have the owner follow up to confirm by email.")
    assert "confirmation email is on its way" not in r.reply_text
    booking = c.bookings()[0]
    assert booking["email"] is None and booking["emailCaptureFailed"] is True
    assert c.mailer.sent == []


@pytest.mark.parametrize("yes", ["yes", "yes please", "Yes that's correct"])
def test_affirmative_at_confirm_books_even_when_router_says_answer_question(tmp_path, yes):
    """Lesson 0010: the live router labels a bare affirmative ANSWER_QUESTION, which used to strand
    the caller at confirm and file a junk callback instead of committing."""
    c = Conversation(tmp_path)
    c.say("book a table"); c.say("saturday", field="date", value="saturday"); c.say("7pm", field="time", value="7pm")
    c.say("two", field="party_size", value="two"); c.say("Jo", field="name", value="Jo"); c.say("yes")
    c.say("jo at gmail dot com", field="email_raw", value="jo at gmail dot com"); c.say("j o"); c.say("yes")
    assert c.slot.stage == "confirm"
    r = c.say(yes, intent="ANSWER_QUESTION")
    assert c.slot.stage == "done", f"{yes!r} did not commit the booking"
    assert r.tool_called == "commit_booking"


def test_question_at_confirm_still_reaches_the_answerer(tmp_path):
    """The yes/no shortcut must not swallow a genuine mid-booking question."""
    c = Conversation(tmp_path)
    c.say("book a table"); c.say("saturday", field="date", value="saturday"); c.say("7pm", field="time", value="7pm")
    c.say("two", field="party_size", value="two"); c.say("Jo", field="name", value="Jo"); c.say("yes")
    c.say("jo at gmail dot com", field="email_raw", value="jo at gmail dot com"); c.say("j o"); c.say("yes")
    assert c.slot.stage == "confirm"
    r = c.say("do you have parking", intent="ANSWER_QUESTION")
    assert c.slot.stage == "confirm" and r.tool_called != "commit_booking"


def test_calendar_failure_does_not_fail_booking(tmp_path):
    c = Conversation(tmp_path, calendar=FailingCalendar())
    c.say("book a table"); c.say("saturday", field="date", value="saturday"); c.say("7pm", field="time", value="7pm")
    c.say("two", field="party_size", value="two"); c.say("Sam", field="name", value="Sam"); c.say("yes")
    c.say("sam at gmail dot com", field="email_raw", value="sam at gmail dot com"); c.say("yes")
    r = c.say("yes")
    assert c.slot.stage == "done" and r.tool_result["outcome"] == "created" and r.tool_result["calendar_mirrored"] is False
    assert c.bookings()[0]["calendarSyncedAt"] is None


def test_no_caller_id_asks_for_phone_and_no_on_caller_id(tmp_path):
    c = Conversation(tmp_path, from_number=None)
    c.say("book a table"); c.say("saturday", field="date", value="saturday"); c.say("7pm", field="time", value="7pm")
    c.say("two", field="party_size", value="two")
    assert c.say("Alex", field="name", value="Alex").reply_text == "What's the best number for you?"
    assert c.say("0400 999 888", field="phone", value="0400 999 888").reply_text == "And what's the best email for you?"
    assert c.slot.phone == "+61400999888"

    c2 = Conversation(tmp_path)
    c2.say("book a table"); c2.say("saturday", field="date", value="saturday"); c2.say("7pm", field="time", value="7pm")
    c2.say("two", field="party_size", value="two"); c2.say("Alex", field="name", value="Alex")
    assert c2.say("no").reply_text == "What's the best number for you?"


def test_low_confidence_mid_booking_is_treated_as_the_current_field(tmp_path):
    c = Conversation(tmp_path)
    c.say("book a table")
    r = c.say("saturday", intent="CHITCHAT", confidence=0.3)
    assert r.intent == "BOOK" and c.slot.date == dt.date(2026, 9, 19)


def test_speak_helpers():
    assert capacity.speak_time(dt.time(19, 0)) == "seven"
    assert capacity.speak_time(dt.time(20, 15)) == "quarter past eight"
    assert capacity.speak_time(dt.time(12, 30)) == "half past twelve"
    assert capacity.speak_date(dt.date(2026, 9, 14), dt.date(2026, 9, 14)) == "today"
    assert capacity.speak_date(dt.date(2026, 9, 15), dt.date(2026, 9, 14)) == "tomorrow"
    assert capacity.speak_date(dt.date(2026, 9, 25), dt.date(2026, 9, 14)) == "Friday the 25th of September"
    windows = capacity.parse_windows(TEST_WINDOWS)
    assert [t.strftime("%H:%M") for t, _ in capacity.slot_times(dt.date(2026, 9, 19), windows)][:3] == ["12:00", "12:30", "13:00"]
    assert capacity.slot_times(dt.date(2026, 9, 14), windows) == []  # Monday closed
    docs = capacity.materialise_slots(windows, dt.date(2026, 9, 14), 7, "biz")
    assert len(docs) == 5 * 5 + 3 * 7 and docs["2026-09-19T1930"]["seatsBooked"] == 0
