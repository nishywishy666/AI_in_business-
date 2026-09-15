"""The per-turn brain (vr_plan.md §4): router → state machine or lookup → Gemini or template.

Shared by the phone path (after STT) and the simulator's Mode A (R7). Booking turns never call
Gemini. Every model failure ends in a deterministic utterance (Rule 6).
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

from config.disclaimers import CROSS_CONTACT, GREETING_DISCLOSURE
from contracts.voice import RouterOutput, TurnLog

from . import templates
from .answerer import AnswerTransport, chitchat, phrase
from .pipeline import TurnResult
from .router import RouterTransport, apply_low_confidence, route
from .session import CallSession
from .sinks import CallSink
from .telemetry import HudBus, StageTimer, TurnRecorder
from .tools import BusinessContext, BusinessReader, Lookup, answer_question, load_business_context, take_callback

log = logging.getLogger(__name__)


class BookingHandler(Protocol):
    """Phase 4 plugs the deterministic slot-fill machine in here."""

    async def handle(self, session: CallSession, output: RouterOutput, text: str, ctx: BusinessContext) -> "BookingStep": ...


@dataclass
class BookingStep:
    reply: str
    tool_called: str | None = None
    tool_args: dict = field(default_factory=dict)
    tool_result: object = None
    end_call: bool = False
    callback_reason: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class EngineDeps:
    reader: BusinessReader
    sink: CallSink
    bus: HudBus
    router: RouterTransport
    answerer: AnswerTransport
    business_id: str
    tz: str = "Australia/Melbourne"
    booking: BookingHandler | None = None
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc)
    windows: list[dict] = field(default_factory=list)
    context_loader: Callable[[BusinessReader, str], BusinessContext] | None = None


class ReceptionistTurnEngine:
    def __init__(self, deps: EngineDeps) -> None:
        self.deps = deps
        self._contexts: dict[str, BusinessContext] = {}

    # ---- load-once context (§10) -----------------------------------------------------------
    def context_for(self, session: CallSession) -> BusinessContext:
        ctx = self._contexts.get(session.call_id)
        if ctx is None:
            loader = self.deps.context_loader or (lambda r, b: load_business_context(r, b, windows=self.deps.windows))
            ctx = loader(self.deps.reader, session.business_id or self.deps.business_id)
            self._contexts[session.call_id] = ctx
        return ctx

    def forget(self, call_id: str) -> None:
        self._contexts.pop(call_id, None)

    def greeting_text(self, session: CallSession) -> str:
        return GREETING_DISCLOSURE.format(business_name=self.context_for(session).business_name)

    # ---- one caller turn ---------------------------------------------------------------------
    async def run_turn(self, session: CallSession, text: str, *, recorder: TurnRecorder,
                       stt_ms: int | None = None) -> TurnResult:
        deps = self.deps
        timer = StageTimer()
        now = deps.clock()
        ctx = self.context_for(session)
        before = session.slot_state.model_dump(mode="json")
        session.turn_index += 1
        caller_index = session.turn_index
        session.remember("caller", text)
        warnings: list[str] = []

        timer.start("router")
        routed = await route(text, session.slot_state, deps.router, now=now, tz=deps.tz)
        timer.stop("router")
        if routed.parse_failures:
            session.router_parse_failures += routed.parse_failures
            if session.router_parse_failures >= 2:
                recorder.warn(f"router returned unparseable JSON {session.router_parse_failures}x this call — prompt drift?")
                warnings.append("router_parse_failures>=2")
        output = routed.output if routed.fallback else apply_low_confidence(routed.output, session.slot_state)
        if routed.fallback:
            warnings.append("router_fallback_callback")

        recorder.record(TurnLog(call_id=session.call_id, turn_index=caller_index, speaker="caller", text_final=text,
                                intent=output.intent, router_confidence=output.confidence, stt_ms=stt_ms,
                                router_ms=timer.ms.get("router"), at=now))

        reply, tool, tool_args, tool_result, source, used_fallback, end_call = "", None, {}, None, None, False, False

        from .booking_machine import is_no, is_yes  # local: booking_machine imports BookingStep from here

        if session.pending_callback and output.field_name == "phone" and output.field_value:
            pc = session.pending_callback
            tool, tool_args = "take_callback", {"reason": pc["reason"], "phone": output.field_value}
            tool_result = take_callback(deps.sink, call_id=session.call_id, business_id=ctx.business_id,
                                        name=pc.get("name") or session.slot_state.name, phone=output.field_value,
                                        question=pc.get("question"), reason=pc["reason"], now=now)
            session.pending_callback = None
            reply, source = templates.CALLBACK_TAKEN, "template"

        elif output.intent == "END":
            reply, source, end_call = templates.CLOSING, "template", True
            session.ended = True

        elif (output.intent in ("BOOK",)
              or (deps.booking is not None and session.slot_state.stage not in ("idle", "done")
                  and (output.intent == "CALLBACK" or is_yes(text) or is_no(text)))):
            # A bare "yes"/"no" mid-booking is a control signal, not a question: the router routinely
            # labels it ANSWER_QUESTION, which would strand the caller at the confirm step (R4).
            if deps.booking is None:
                warnings.append("booking machine not built yet (Phase 4)")
                recorder.warn(warnings[-1])
                reply, source = "", "template"
            else:
                step = await deps.booking.handle(session, output, text, ctx)
                reply, tool, tool_args, tool_result = step.reply, step.tool_called, step.tool_args, step.tool_result
                source, end_call = "template", step.end_call
                warnings.extend(step.warnings)

        elif output.intent == "CALLBACK":
            reason = _callback_reason(text, output)
            reply, tool, tool_args, tool_result, source = self._start_callback(session, ctx, reason, text, now)

        elif output.intent == "ANSWER_QUESTION":
            question = output.question or text
            timer.start("lookup")
            lookup = answer_question(question, ctx, deps.reader)
            timer.stop("lookup")
            tool, tool_args, tool_result, source = lookup.tool, lookup.args, lookup.payload, lookup.source
            if lookup.source == "not_found":
                if lookup.reason == "allergen_unknown":
                    r, t, ta, tr, s = self._start_callback(session, ctx, "allergen_unknown", question, now,
                                                           lead=templates.ALLERGEN_UNKNOWN)
                else:
                    r, t, ta, tr, s = self._start_callback(session, ctx, "no_data", question, now,
                                                           lead=lookup.template or templates.WILL_CHECK_AND_CALLBACK)
                reply = r
                if t:
                    tool, tool_args, tool_result = t, {**tool_args, **ta}, tr
                source = "not_found"
            else:
                timer.start("answer")
                answer = await phrase(deps.answerer, fact=lookup.payload or {}, question=question,
                                      history=session.history[:-1], business_name=ctx.business_name,
                                      fallback=lookup.template or templates.FACT_GENERIC.format(value=""))
                timer.stop("answer")
                reply, used_fallback = answer.text, answer.used_fallback
                if answer.warning:
                    warnings.append(answer.warning)
                    recorder.warn(answer.warning)
                if lookup.source == "allergen":
                    reply = f"{reply.rstrip()} {CROSS_CONTACT}"  # Rule 2: appended by Python, always

        elif output.intent == "CHITCHAT":
            timer.start("answer")
            answer = await chitchat(deps.answerer, text=text, history=session.history[:-1],
                                    business_name=ctx.business_name, fallback=templates.CHITCHAT_FALLBACK)
            timer.stop("answer")
            reply, used_fallback, source = answer.text, answer.used_fallback, "template" if answer.used_fallback else None
            if answer.warning:
                warnings.append(answer.warning)

        if not reply and not end_call and source is None:
            reply, source = templates.DID_NOT_UNDERSTAND, "template"

        session.turn_index += 1
        agent_index = session.turn_index
        session.remember("agent", reply)
        recorder.record(TurnLog(call_id=session.call_id, turn_index=agent_index, speaker="agent", text_final=reply,
                                intent=output.intent, router_confidence=output.confidence, tool_called=tool,
                                answer_source=source, answer_ms=timer.ms.get("answer"), used_fallback=used_fallback,
                                at=deps.clock()))
        after = session.slot_state.model_dump(mode="json")
        if after != before:
            deps.bus.publish(session.call_id, "slot", slot=after)
        if session.email_candidate:
            deps.bus.publish(session.call_id, "email", candidate=session.email_candidate)
        return TurnResult(
            call_id=session.call_id, turn_index=agent_index, text=text, reply_text=reply, intent=output.intent,
            confidence=output.confidence, field_name=output.field_name, field_value=output.field_value,
            tool_called=tool, tool_args=tool_args, tool_result=tool_result, answer_source=source,
            used_fallback=used_fallback, slot_before=before, slot_after=after, email_candidate=session.email_candidate,
            timings_ms={"stt": stt_ms or 0, **timer.ms}, warnings=warnings, end_call=end_call,
        )

    # ---- callback machine (minimal, §10 take_callback) ---------------------------------------
    def _start_callback(self, session: CallSession, ctx: BusinessContext, reason: str, question: str,
                        now: dt.datetime, *, lead: str | None = None):
        phone = session.slot_state.phone or session.from_number
        if phone:
            callback_id = take_callback(self.deps.sink, call_id=session.call_id, business_id=ctx.business_id,
                                        name=session.slot_state.name, phone=phone, question=question,
                                        reason=reason, now=now)  # type: ignore[arg-type]
            reply = f"{lead.split('What')[0].strip()} {templates.CALLBACK_TAKEN}" if lead else templates.CALLBACK_TAKEN
            return reply.strip(), "take_callback", {"reason": reason, "phone": phone}, {"callback_id": callback_id}, "template"
        session.pending_callback = {"reason": reason, "question": question, "name": session.slot_state.name}
        return (lead or templates.WILL_CHECK_AND_CALLBACK), None, {}, None, "template"


def _callback_reason(text: str, output: RouterOutput) -> str:
    t = text.lower()
    if any(w in t for w in ("complain", "complaint", "terrible", "awful", "refund", "unhappy")):
        return "complaint"
    if any(w in t for w in ("cater", "catering", "function", "event")):
        return "catering"
    if any(w in t for w in ("group", "people", "guests")) and any(ch.isdigit() for ch in t):
        return "large_group"
    return "other"
