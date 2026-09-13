"""Typed boundaries for the voice receptionist (vr_plan.md R5, §7.1, §8.1, §9.2, §11).

Every model output is parsed into one of these before use. Unparseable output is a handled
error path (Rule 6), never an exception that reaches the call loop.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Intent = Literal["ANSWER_QUESTION", "BOOK", "CALLBACK", "CHITCHAT", "END"]
FieldName = Literal["date", "time", "party_size", "name", "phone", "email_raw"]
Stage = Literal["idle", "date", "time", "party_size", "name", "phone", "email", "confirm", "committing", "done"]
AnswerSource = Literal["menu", "fact", "allergen", "not_found", "template"]
CallbackReason = Literal["allergen_unknown", "no_data", "complaint", "large_group", "catering", "other"]
Speaker = Literal["caller", "agent"]
Outcome = Literal["answered", "booked", "callback", "abandoned", "error"]


class VoiceModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    def to_doc(self) -> dict:
        return self.model_dump(mode="json")


class RouterOutput(VoiceModel):
    """§7.1 — the Groq router's schema-constrained reply. One intent, at most one field."""

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    question: str | None = None
    field_name: FieldName | None = None
    field_value: str | None = None
    wants_human: bool = False


class SlotState(VoiceModel):
    """§8.1 — persisted on calls/{callSid}.slotState after every change."""

    stage: Stage = "idle"
    date: dt.date | None = None
    time: dt.time | None = None
    party_size: int | None = None
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    offered_alternatives: list[str] = Field(default_factory=list)
    attempts: dict[str, int] = Field(default_factory=dict)


class EmailCandidate(VoiceModel):
    """§9.2 — confidence = min(domain_score, local_score)."""

    raw: str
    local: str
    domain: str
    domain_score: float = Field(ge=0.0, le=1.0)
    local_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    needs_spelling: bool


class TurnLog(VoiceModel):
    """§11 — one write per final turn to calls/{callSid}/turns/{turnIndex}. Never partials."""

    call_id: str
    turn_index: int
    speaker: Speaker
    text_final: str
    intent: Intent | None = None
    router_confidence: float | None = None
    tool_called: str | None = None
    answer_source: AnswerSource | None = None
    stt_ms: int | None = None
    router_ms: int | None = None
    answer_ms: int | None = None
    tts_ms: int | None = None
    interrupted: bool = False
    used_fallback: bool = False
    at: dt.datetime | None = None

    def to_doc(self) -> dict:
        doc = self.model_dump(mode="json")
        # Firestore field names in §11 are camelCase.
        return {
            "callId": doc["call_id"], "turnIndex": doc["turn_index"], "speaker": doc["speaker"],
            "textFinal": doc["text_final"], "intent": doc["intent"], "routerConfidence": doc["router_confidence"],
            "toolCalled": doc["tool_called"], "answerSource": doc["answer_source"], "sttMs": doc["stt_ms"],
            "routerMs": doc["router_ms"], "answerMs": doc["answer_ms"], "ttsMs": doc["tts_ms"],
            "interrupted": doc["interrupted"], "usedFallback": doc["used_fallback"], "at": doc["at"],
        }


class CallStart(VoiceModel):
    """§11 — calls/{callSid} on `start` (new call)."""

    call_id: str
    business_id: str
    from_number: str | None = None
    session_type: Literal["phone", "sim"] = "phone"
    started_at: dt.datetime
    disclosure_done: bool = False
    resume_count: int = 0

    def to_doc(self) -> dict:
        return {
            "callId": self.call_id, "businessId": self.business_id, "from": self.from_number,
            "sessionType": self.session_type, "startedAt": self.started_at.isoformat(),
            "disclosureDone": self.disclosure_done, "resumeCount": self.resume_count,
        }


class UsageEvent(VoiceModel):
    """§11 — usageEvents/{auto}, one per provider call."""

    call_id: str
    provider: Literal["groq", "gemini", "elevenlabs_stt", "elevenlabs_tts", "twilio"]
    task: str
    model: str | None = None
    units: float = 0
    unit_kind: str = ""
    cost_cents: float = 0
    at: dt.datetime | None = None

    def to_doc(self) -> dict:
        return {
            "callId": self.call_id, "provider": self.provider, "task": self.task, "model": self.model,
            "units": self.units, "unitKind": self.unit_kind, "costCents": self.cost_cents,
            "at": self.at.isoformat() if self.at else None,
        }
