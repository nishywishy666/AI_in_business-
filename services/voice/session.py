"""Per-call state (vr_plan.md §6.6, §11). Phase 1 scope: identity + stream bookkeeping.

Load-once business context, resume and the 280 s cutover are added in later phases.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from contracts.voice import SlotState

GREETING_MARK = "greeting"


@dataclass
class CallSession:
    call_id: str
    stream_sid: str
    business_id: str
    from_number: str | None = None
    resume: bool = False
    started_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    disclosure_done: bool = False
    resume_count: int = 0
    turn_index: int = 0
    slot_state: SlotState = field(default_factory=SlotState)
    pending_marks: set[str] = field(default_factory=set)
    agent_speaking: bool = False
    interrupted: bool = False
    history: list[dict] = field(default_factory=list)  # [{role: caller|agent, text}]
    router_parse_failures: int = 0
    pending_callback: dict | None = None  # {reason, question, name} awaiting a phone number
    email_candidate: dict | None = None
    ended: bool = False

    def remember(self, role: str, text: str, keep: int = 12) -> None:
        if text:
            self.history.append({"role": role, "text": text})
            del self.history[:-keep]

    # ---- §6.6 state that must survive a cutover ------------------------------------------
    def history_summary(self) -> str:
        """One paragraph, regenerated at cutover. Built in Python from the recent turns.
        TODO(spec): the spec does not say which component writes historySummary; no model call here."""
        return " ".join(f"{h['role']}: {h['text']}" for h in self.history[-8:])[:1500]

    def to_resume_doc(self) -> dict:
        return {
            "historySummary": self.history_summary(),
            "slotState": self.slot_state.model_dump(mode="json"),
            "emailCandidate": self.email_candidate,
            "resumeCount": self.resume_count,
            "disclosureDone": self.disclosure_done,
            "turnIndex": self.turn_index,
            "pendingCallback": self.pending_callback,
            "history": self.history[-8:],
        }

    def restore(self, doc: dict) -> None:
        if doc.get("slotState"):
            self.slot_state = SlotState.model_validate(doc["slotState"])
        self.email_candidate = doc.get("emailCandidate")
        self.resume_count = int(doc.get("resumeCount") or 0)
        self.disclosure_done = bool(doc.get("disclosureDone", False))
        self.turn_index = int(doc.get("turnIndex") or 0)
        self.pending_callback = doc.get("pendingCallback")
        self.history = list(doc.get("history") or [])

    def resume_line(self, speak_time) -> str | None:
        """The one acknowledging line after a cutover (§6.6), from slot state only."""
        from . import templates

        slot = self.slot_state
        if slot.stage in ("idle", "done"):
            return templates.RESUME_ACK_PLAIN
        if slot.party_size and slot.time and slot.name:
            return templates.RESUME_ACK_BOOKING.format(party_size=slot.party_size, time=speak_time(slot.time), name=slot.name)
        have = [f"a table for {slot.party_size}" if slot.party_size else "a table",
                f"on {slot.date.strftime('%A')}" if slot.date else None,
                f"at {speak_time(slot.time)}" if slot.time else None,
                f"under {slot.name}" if slot.name else None]
        return templates.RESUME_ACK_PARTIAL.format(summary=" ".join(h for h in have if h))

    def elapsed_ms(self, now: dt.datetime | None = None) -> int:
        now = now or dt.datetime.now(dt.timezone.utc)
        return int((now - self.started_at).total_seconds() * 1000)
