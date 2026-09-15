"""Deterministic slot-fill booking machine (vr_plan.md §8, V7, R4).

Python owns the sequence and every number. The router only hands over one raw field per turn;
this module parses it. Every utterance is a template. Gemini is never called on a booking turn.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from config import threshold
from contracts.voice import RouterOutput
from services.booking import capacity, commit
from services.booking.calendar import CalendarMirror, mirror_after_commit
from services.booking.email import Mailer, confirmation_email, send_once

from . import email_capture, templates
from .engine import BookingStep
from .session import CallSession
from .tools import BusinessContext, BusinessReader, take_callback

log = logging.getLogger(__name__)

_YES = {"yes", "yep", "yeah", "yup", "correct", "right", "sure", "ok", "okay", "that's right", "thats right",
        "perfect", "exactly", "please", "go ahead", "lock it in", "confirm"}
_NO = {"no", "nope", "nah", "wrong", "not quite", "incorrect", "that's wrong", "change", "different", "actually"}
_VAGUE_DATE = ("weekend", "sometime", "next week", "whenever", "soon", "later", "one day", "any day", "some day")
_VAGUE_TIME = ("evening", "morning", "afternoon", "lunch", "dinner", "breakfast", "night", "late", "early",
               "whenever", "anytime", "any time")
_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
             "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
             "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "a": 1, "an": 1, "couple": 2, "few": 3}
_WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2, "thursday": 3,
             "thu": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
_MONTHS = {m.lower(): i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July",
                                                 "August", "September", "October", "November", "December"], 1)}


@dataclass
class BookingDeps:
    reader: BusinessReader
    sink: Any
    committer: commit.BookingCommitter
    mailer: Mailer
    calendar: CalendarMirror
    windows: list[capacity.Window]
    tz: str = "Australia/Melbourne"
    owner_email: str | None = None


def _booking_note(slot, why: str) -> str:
    """The callback row's 'question' when the machine itself bails out of a booking. Previously the
    caller's last utterance ("Yes, that's right.", an email address, or nothing) was written there,
    which is what the owner then saw on the Callbacks screen (lessons/0008)."""
    bits = []
    if getattr(slot, "party_size", None):
        bits.append(f"{slot.party_size} people")
    if getattr(slot, "date", None):
        bits.append(str(slot.date))
    if getattr(slot, "time", None):
        bits.append(str(slot.time))
    wanted = "Wanted to book " + (" · ".join(bits) if bits else "a table")
    return f"{wanted} — {why}. Call back to finish the booking."


class BookingMachine:
    def __init__(self, deps: BookingDeps, clock=lambda: dt.datetime.now(dt.timezone.utc)) -> None:
        self.deps = deps
        self.clock = clock

    # ---- entry -------------------------------------------------------------------------------
    async def handle(self, session: CallSession, output: RouterOutput, text: str, ctx: BusinessContext) -> BookingStep:
        slot = session.slot_state
        now_local = self.clock().astimezone(ZoneInfo(self.deps.tz))
        if output.intent == "CALLBACK" or output.wants_human:
            return self._to_callback(session, ctx, "other", text)
        if slot.stage in ("idle", "done"):
            slot.stage = "date"
            slot.attempts = {}
            # a first turn may already carry a field ("table for four on saturday")
            if output.field_name in ("date", "party_size", "time") and output.field_value:
                return self._consume(session, output, text, ctx, now_local)
            return BookingStep(templates.ASK_DATE)
        return self._consume(session, output, text, ctx, now_local)

    # ---- per-stage --------------------------------------------------------------------------
    def _consume(self, session: CallSession, output: RouterOutput, text: str, ctx: BusinessContext,
                 now_local: dt.datetime) -> BookingStep:
        slot = session.slot_state
        stage = slot.stage
        value = output.field_value if output.field_name in (stage, _router_field(stage)) and output.field_value else text

        if stage == "date":
            parsed = parse_date(value, now_local)
            if parsed is None:
                return self._retry(session, ctx, "date", templates.REASK_DATE, text)
            slot.date = parsed
            slot.stage = "time"
            return BookingStep(templates.ASK_TIME)

        if stage == "time":
            parsed = parse_time(value)
            if parsed is None:
                times = capacity.slot_times(slot.date, self.deps.windows) if slot.date else []
                period = _period(value)
                options = [t for t, _ in times if _in_period(t, period)][:2] or [t for t, _ in times][:2]
                if options and not slot.offered_alternatives:
                    slot.offered_alternatives = [t.strftime("%H:%M") for t in options]
                    spoken = [capacity.speak_time(t) for t in options]
                    return self._retry(session, ctx, "time", templates.OFFER_TIMES.format(
                        first=spoken[0], second=spoken[-1]), text, count=True)
                return self._retry(session, ctx, "time", templates.ASK_TIME, text)
            if not capacity.in_windows(slot.date, parsed, self.deps.windows):
                times = capacity.slot_times(slot.date, self.deps.windows)
                if not times:
                    return self._retry(session, ctx, "time", templates.REASK_DATE, text)
                nearest = min((t for t, _ in times), key=lambda t: abs(_minutes(t) - _minutes(parsed)))
                parsed = nearest
            slot.time = parsed
            slot.offered_alternatives = []
            slot.stage = "party_size"
            return BookingStep(templates.ASK_PARTY_SIZE)

        if stage == "party_size":
            size = parse_party_size(value)
            if size is None:
                return self._retry(session, ctx, "party_size", templates.ASK_PARTY_SIZE, text)
            if size > threshold("MAX_PARTY"):
                return self._to_callback(session, ctx, "large_group", text, lead=templates.LARGE_GROUP_CALLBACK)
            slot.party_size = size
            return self._check_availability(session, ctx, now_local)

        if stage == "name":
            name = (output.field_value if output.field_name == "name" and output.field_value else text).strip()
            name = re.sub(r"^(it's|its|under|the name is|name is|my name is|i'm|im)\s+", "", name, flags=re.I).strip(" .")
            if not name:
                return self._retry(session, ctx, "name", templates.ASK_NAME, text)
            slot.name = name[:60]
            slot.stage = "phone"
            if session.from_number:
                return BookingStep(templates.CONFIRM_CALLER_ID)
            return BookingStep(templates.ASK_PHONE)

        if stage == "phone":
            if session.from_number and not slot.phone and slot.attempts.get("phone_asked") is None:
                if is_yes(text):
                    slot.phone = session.from_number
                    slot.stage = "email"
                    return BookingStep(templates.ASK_EMAIL)
                if is_no(text):
                    slot.attempts["phone_asked"] = 1
                    return BookingStep(templates.ASK_PHONE)
            phone = parse_phone(value)
            if phone is None:
                return self._retry(session, ctx, "phone", templates.ASK_PHONE, text)
            slot.phone = phone
            slot.stage = "email"
            return BookingStep(templates.ASK_EMAIL)

        if stage == "email":
            return self._email_turn(session, ctx, value, text)

        if stage == "confirm":
            if is_yes(text):
                return self._commit(session, ctx, now_local)
            if is_no(text):
                # TODO(spec): what to change on "no" is not specified; restart from the date.
                slot.stage, slot.attempts, slot.offered_alternatives = "date", {}, []
                return BookingStep("No problem — " + templates.ASK_DATE.lower())
            return self._retry(session, ctx, "confirm", self._confirm_line(session, now_local), text)

        if stage == "committing":
            return self._commit(session, ctx, now_local)
        return BookingStep(templates.DID_NOT_UNDERSTAND)

    # ---- availability ------------------------------------------------------------------------
    def _check_availability(self, session: CallSession, ctx: BusinessContext, now_local: dt.datetime) -> BookingStep:
        slot = session.slot_state
        from .tools import check_availability

        avail = check_availability(self.deps.reader, ctx.business_id, slot.date, slot.time, slot.party_size)
        args = {"date": slot.date.isoformat(), "time": slot.time.strftime("%H:%M"), "party_size": slot.party_size}
        if avail.open:
            slot.stage = "name"
            slot.offered_alternatives = []
            return BookingStep(templates.ASK_NAME, tool_called="check_availability", tool_args=args,
                               tool_result={"open": True, "slot_id": avail.slot_id})
        alternatives = capacity.nearby_options(self.deps.reader, ctx.business_id, slot.date, slot.time,
                                               slot.party_size, self.deps.windows)
        result = {"open": False, "slot_id": avail.slot_id, "exists": avail.exists,
                  "alternatives": [a.slot_id for a in alternatives]}
        if not alternatives:
            return self._to_callback(session, ctx, "other", _booking_note(slot, "that time was full with nothing nearby"),
                                     lead=templates.SLOT_FULL_NO_ALTERNATIVES,
                                     tool_called="check_availability", tool_args=args, tool_result=result)
        slot.offered_alternatives = [a.slot_id for a in alternatives]
        slot.stage = "time"
        spoken = capacity.speak_alternatives(alternatives, now_local.date(), slot.date)
        return BookingStep(templates.SLOT_FULL_ALTERNATIVES.format(alternatives=spoken),
                           tool_called="check_availability", tool_args=args, tool_result=result)

    # ---- email (§9) -----------------------------------------------------------------------------
    def _email_turn(self, session: CallSession, ctx: BusinessContext, value: str, text: str) -> BookingStep:
        slot = session.slot_state
        state = session.email_candidate or {}
        phase = state.get("phase")
        max_spell = threshold("MAX_EMAIL_SPELL_ATTEMPTS")

        if phase == "readback":
            if is_yes(text):
                slot.email = f"{state['local']}@{state['domain']}"
                session.email_candidate = {**state, "phase": "accepted"}
                slot.stage = "confirm"
                return BookingStep(self._confirm_line(session, self.clock().astimezone(ZoneInfo(self.deps.tz))))
            return self._ask_spelling(session, state)

        if phase == "spelling":
            letters = email_capture.letters_from_spelling(text)
            candidate = email_capture.rebuild(letters or state.get("local", ""), state.get("domain", ""))
            state = {**state, **candidate.to_doc(), "phase": "spelled", "spell_attempts": state.get("spell_attempts", 0)}
            checks = email_capture.phonetic_checks(candidate.local)
            session.email_candidate = state
            if checks:
                state["phase"] = "phonetic"
                # "B for bravo?" — one question covering every confusable letter that was spelled (§9.3)
                lines = [templates.EMAIL_PHONETIC_CHECK.format(letter=c.split(" for ")[0], word=c.split(" for ")[1])
                         for c in checks]
                return BookingStep(" ".join(lines))
            return self._readback(session, state)

        if phase == "phonetic":
            if is_yes(text):
                return self._readback(session, state)
            if state.get("spell_attempts", 0) >= max_spell:
                return self._give_up_email(session, state)
            return self._ask_spelling(session, state)

        if phase == "spelled_readback":
            if is_yes(text):
                slot.email = f"{state['local']}@{state['domain']}"
                session.email_candidate = {**state, "phase": "accepted"}
                slot.stage = "confirm"
                return BookingStep(self._confirm_line(session, self.clock().astimezone(ZoneInfo(self.deps.tz))))
            if state.get("spell_attempts", 0) >= max_spell:
                return self._give_up_email(session, state)
            return self._ask_spelling(session, state)

        candidate = email_capture.capture(value)
        state = {**candidate.to_doc(), "phase": "readback", "spell_attempts": 0}
        session.email_candidate = state
        if candidate.needs_spelling:
            return self._ask_spelling(session, state)
        return self._readback(session, state)

    def _ask_spelling(self, session: CallSession, state: dict) -> BookingStep:
        attempts = state.get("spell_attempts", 0)
        if attempts >= threshold("MAX_EMAIL_SPELL_ATTEMPTS"):
            return self._give_up_email(session, state)
        session.email_candidate = {**state, "phase": "spelling", "spell_attempts": attempts + 1}
        return BookingStep(templates.EMAIL_SPELL_REQUEST)

    def _readback(self, session: CallSession, state: dict) -> BookingStep:
        phase = "spelled_readback" if state.get("phase") in ("spelled", "phonetic") else "readback"
        session.email_candidate = {**state, "phase": phase}
        return BookingStep(templates.EMAIL_READBACK.format(spoken_email=email_capture.spoken(state["local"], state["domain"])))

    def _give_up_email(self, session: CallSession, state: dict) -> BookingStep:
        slot = session.slot_state
        slot.email = None
        session.email_candidate = {**state, "phase": "failed"}
        slot.stage = "confirm"
        return BookingStep(self._confirm_line(session, self.clock().astimezone(ZoneInfo(self.deps.tz))),
                           warnings=["email capture failed after max spelling attempts; booking continues"])

    # ---- confirm + commit (§8.3) ------------------------------------------------------------------
    def _confirm_line(self, session: CallSession, now_local: dt.datetime) -> str:
        slot = session.slot_state
        return templates.CONFIRM_BOOKING.format(party_size=slot.party_size, date=capacity.speak_date(slot.date, now_local.date()),
                                                time=capacity.speak_time(slot.time), name=slot.name)

    def _commit(self, session: CallSession, ctx: BusinessContext, now_local: dt.datetime) -> BookingStep:
        slot = session.slot_state
        slot.stage = "committing"
        slot_id = capacity.slot_id_for(slot.date, slot.time)
        key = commit.idempotency_key(session.call_id, slot_id, slot.name or "", slot.party_size or 0)
        starts_at = dt.datetime.combine(slot.date, slot.time).replace(tzinfo=ZoneInfo(self.deps.tz))
        email_failed = session.email_candidate is not None and session.email_candidate.get("phase") == "failed"
        booking = {
            "idempotencyKey": key, "businessId": ctx.business_id, "callId": session.call_id, "slotId": slot_id,
            "startsAt": starts_at.isoformat(), "partySize": slot.party_size, "name": slot.name, "phone": slot.phone,
            "email": slot.email, "emailCaptureFailed": email_failed, "status": "confirmed",
            "createdAt": self.clock().isoformat(), "calendarSyncedAt": None, "calendarEventId": None,
            "spokenWhen": f"{capacity.speak_date(slot.date, now_local.date())} at {capacity.speak_time(slot.time)}",
        }
        try:
            outcome = self.deps.committer.reserve(slot_id, slot.party_size, key, booking)
        except commit.SlotFull:
            slot.stage = "time"
            alternatives = capacity.nearby_options(self.deps.reader, ctx.business_id, slot.date, slot.time,
                                                   slot.party_size, self.deps.windows)
            if not alternatives:
                return self._to_callback(session, ctx, "other", _booking_note(slot, "that time filled up with nothing nearby"),
                                         lead=templates.SLOT_FULL_NO_ALTERNATIVES)
            slot.offered_alternatives = [a.slot_id for a in alternatives]
            return BookingStep(templates.SLOT_FULL_ALTERNATIVES.format(
                alternatives=capacity.speak_alternatives(alternatives, now_local.date(), slot.date)),
                tool_called="commit_booking", tool_result={"outcome": "slot_full"})
        except commit.SlotMissing:
            return self._to_callback(session, ctx, "other", _booking_note(slot, "that time is not on the booking sheet"),
                                     lead=templates.SLOT_FULL_NO_ALTERNATIVES,
                                     tool_called="commit_booking", tool_result={"outcome": "slot_missing"})
        slot.stage = "done"
        # AFTER the commit: email + Calendar, both idempotent, neither can fail the booking.
        now = self.clock()
        emailed = False
        if slot.email and not email_failed:
            subject, body = confirmation_email(booking, ctx.business_name)
            emailed = send_once(self.deps.mailer, self.deps.sink, key=key, to=slot.email, subject=subject, body=body,
                                call_id=session.call_id, kind="booking_confirmation", now=now)
        mirrored = mirror_after_commit(self.deps.calendar, self.deps.sink, booking, now=now)
        reply = templates.BOOKING_DONE if emailed else templates.BOOKING_DONE_NO_EMAIL
        return BookingStep(templates.BOOKING_FILLER + " " + reply, tool_called="commit_booking",
                           tool_args={"slot_id": slot_id, "party_size": slot.party_size, "key": key},
                           tool_result={"outcome": outcome, "emailed": emailed, "calendar_mirrored": mirrored,
                                        "email_capture_failed": email_failed})

    # ---- retries + callback --------------------------------------------------------------------
    def _retry(self, session: CallSession, ctx: BusinessContext, field: str, prompt: str, text: str,
               *, count: bool = True) -> BookingStep:
        slot = session.slot_state
        attempts = slot.attempts.get(field, 0) + (1 if count else 0)
        slot.attempts[field] = attempts
        if attempts > threshold("MAX_FIELD_ATTEMPTS") and field != "email":
            return self._to_callback(session, ctx, "other", _booking_note(slot, f"the {field} could not be captured"))
        return BookingStep(prompt)

    def _to_callback(self, session: CallSession, ctx: BusinessContext, reason: str, text: str, *,
                     lead: str | None = None, **step_extra) -> BookingStep:
        slot = session.slot_state
        phone = slot.phone or session.from_number
        slot.stage = "idle"
        if phone:
            callback_id = take_callback(self.deps.sink, call_id=session.call_id, business_id=ctx.business_id,
                                        name=slot.name, phone=phone, question=text, reason=reason, now=self.clock())  # type: ignore[arg-type]
            lead_text = (lead or "").split("What's the best number")[0].strip()
            return BookingStep(f"{lead_text} {templates.CALLBACK_TAKEN}".strip(), callback_reason=reason,
                               tool_called=step_extra.get("tool_called") or "take_callback",
                               tool_args=step_extra.get("tool_args") or {"reason": reason},
                               tool_result=step_extra.get("tool_result") or {"callback_id": callback_id})
        session.pending_callback = {"reason": reason, "question": text, "name": slot.name}
        return BookingStep(lead or templates.WILL_CHECK_AND_CALLBACK, callback_reason=reason, **step_extra)


# ---- parsers (Python owns every number, R4) ---------------------------------------------------------

def parse_date(text: str, now_local: dt.datetime) -> dt.date | None:
    t = (text or "").lower().strip()
    if not t or any(v in t for v in _VAGUE_DATE):
        return None
    today = now_local.date()
    if re.search(r"\b(today|tonight)\b", t):
        return today
    if "tomorrow" in t:
        return today + dt.timedelta(days=1)
    m = re.search(r"\d{4}-\d{2}-\d{2}", t)
    if m:
        try:
            return dt.date.fromisoformat(m.group(0))
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s*(?:of\s+)?([a-z]+)\b", t)
    if m and m.group(2)[:3] in {k[:3] for k in _MONTHS}:
        month = next(v for k, v in _MONTHS.items() if k.startswith(m.group(2)[:3]))
        year = today.year if (month, int(m.group(1))) >= (today.month, today.day) else today.year + 1
        try:
            return dt.date(year, month, int(m.group(1)))
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b", t)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        year = year + 2000 if year < 100 else year
        try:
            date = dt.date(year, month, day)
        except ValueError:
            return None
        return date if date >= today else None
    for name, weekday in _WEEKDAYS.items():
        if re.search(rf"\b{name}\b", t):
            ahead = (weekday - today.weekday()) % 7
            if ahead == 0 and "next" in t:
                ahead = 7
            elif ahead == 0:
                ahead = 0 if now_local.hour < 20 else 7
            elif "next" in t and ahead < 7:
                ahead += 7 if ahead <= 2 else 0
            return today + dt.timedelta(days=ahead)
    m = re.search(r"\bthe\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
    if m:
        day = int(m.group(1))
        month, year = today.month, today.year
        if day < today.day:
            month = month % 12 + 1
            year += 1 if month == 1 else 0
        try:
            return dt.date(year, month, day)
        except ValueError:
            return None
    return None


def parse_time(text: str) -> dt.time | None:
    t = (text or "").lower().strip()
    if not t or any(re.search(rf"\b{v}\b", t) for v in _VAGUE_TIME) and not re.search(r"\d|seven|six|eight|five|nine|ten|eleven|twelve|half|quarter", t):
        return None
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b", t)
    hour = minute = None
    pm = None
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3):
            pm = m.group(3).startswith("p")
    else:
        for word, value in _WORD_NUM.items():
            if value <= 12 and re.search(rf"\b{word}\b", t):
                hour, minute = value, 0
                break
        if hour is None:
            return None
        if "half past" in t:
            minute = 30
        elif "quarter past" in t:
            minute = 15
        elif "quarter to" in t:
            hour, minute = hour - 1, 45
        elif "thirty" in t:
            minute = 30
        elif "fifteen" in t:
            minute = 15
        elif "forty five" in t or "forty-five" in t:
            minute = 45
        if re.search(r"\bpm\b|evening|night|dinner|arvo|afternoon", t):
            pm = True
        elif re.search(r"\bam\b|morning|breakfast", t):
            pm = False
    if hour is None or hour > 24 or minute is None or minute > 59:
        return None
    if pm is True and hour < 12:
        hour += 12
    elif pm is False and hour == 12:
        hour = 0
    elif pm is None and 1 <= hour <= 8:
        hour += 12  # bare "seven" on a restaurant line reads as evening; 9–11 stay morning. TODO(spec): default meridiem not stated.
    if hour == 24:
        hour = 0
    return dt.time(hour, minute)


def parse_party_size(text: str) -> int | None:
    t = (text or "").lower()
    m = re.search(r"\b(\d{1,3})\b", t)
    if m:
        n = int(m.group(1))
        return n if n >= 1 else None
    for word, value in sorted(_WORD_NUM.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{word}\b", t) and word not in ("a", "an"):
            return value
    if re.search(r"\b(just me|myself|one person|solo)\b", t):
        return 1
    return None


def parse_phone(text: str) -> str | None:
    digits = re.sub(r"\D", "", text or "")
    if not digits:
        spoken_digits = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                         "six": "6", "seven": "7", "eight": "8", "nine": "9", "double": ""}
        words = re.findall(r"[a-z]+", (text or "").lower())
        out = []
        for i, w in enumerate(words):
            if w == "double" and i + 1 < len(words) and words[i + 1] in spoken_digits:
                out.append(spoken_digits[words[i + 1]])
            elif w in spoken_digits:
                out.append(spoken_digits[w])
        digits = "".join(out)
    if len(digits) < 8:
        return None
    if digits.startswith("61") and len(digits) == 11:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 10:
        return "+61" + digits[1:]
    return "+" + digits if not digits.startswith("+") else digits


def _router_field(stage: str) -> str:
    return {"email": "email_raw"}.get(stage, stage)


def is_yes(text: str) -> bool:
    t = re.sub(r"[^a-z' ]", " ", (text or "").lower()).strip()
    return any(re.search(rf"(^|\b){re.escape(y)}(\b|$)", t) for y in _YES) and not is_no(t)


def is_no(text: str) -> bool:
    t = re.sub(r"[^a-z' ]", " ", (text or "").lower()).strip()
    return any(re.search(rf"(^|\b){re.escape(n)}(\b|$)", t) for n in _NO)


def _period(text: str) -> str | None:
    t = (text or "").lower()
    if any(w in t for w in ("evening", "dinner", "night", "tonight")):
        return "evening"
    if any(w in t for w in ("lunch", "afternoon", "arvo", "midday")):
        return "afternoon"
    if any(w in t for w in ("morning", "breakfast", "brunch")):
        return "morning"
    return None


def _in_period(time: dt.time, period: str | None) -> bool:
    if period is None:
        return True
    return {"morning": time.hour < 12, "afternoon": 12 <= time.hour < 17, "evening": time.hour >= 17}[period]


def _minutes(time: dt.time) -> int:
    return time.hour * 60 + time.minute
