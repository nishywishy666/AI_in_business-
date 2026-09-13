"""Groq intent routing + one-field extraction (vr_plan.md §7.1, Rule 6).

One schema-constrained call per caller turn. Parse → RouterOutput. On parse failure retry once
with a repair instruction; on a second failure return CALLBACK with confidence 0.0 and flag it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from config import threshold
from contracts.voice import RouterOutput, SlotState

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
FIELDS = ("date", "time", "party_size", "name", "phone", "email_raw")


class RouterTransport(Protocol):
    async def complete(self, *, system: str, user: str, timeout_s: float) -> str: ...


class GroqTransport:
    """groq SDK, JSON mode. Constructed lazily so importing this module never needs a key."""

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key, self.model = api_key, model
        self._client = None

    def _get(self):
        if self._client is None:
            from groq import AsyncGroq

            self._client = AsyncGroq(api_key=self.api_key)
        return self._client

    async def complete(self, *, system: str, user: str, timeout_s: float) -> str:
        response = await self._get().chat.completions.create(
            model=self.model, temperature=0, max_tokens=200, timeout=timeout_s,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return response.choices[0].message.content or ""


class FakeRouterTransport:
    """Scripted responses (str | Exception) in order; falls back to `default`."""

    def __init__(self, scripts: list[Any] | None = None, default: str | None = None) -> None:
        self.scripts = list(scripts or [])
        self.default = default
        self.calls: list[dict] = []

    async def complete(self, *, system: str, user: str, timeout_s: float) -> str:
        self.calls.append({"system": system, "user": user, "timeout_s": timeout_s})
        if self.scripts:
            item = self.scripts.pop(0)
            if isinstance(item, Exception):
                raise item
            return item if isinstance(item, str) else json.dumps(item)
        if self.default is None:
            return json.dumps({"intent": "CHITCHAT", "confidence": 0.5, "question": None, "field_name": None,
                               "field_value": None, "wants_human": False})
        return self.default


SYSTEM_PROMPT = """You are the intent router for a restaurant phone receptionist. Classify the caller's LAST turn into exactly one intent and extract at most one field. Do NOT answer the caller. Reply with JSON only.

Intents: ANSWER_QUESTION (asks about menu, prices, allergens, hours, address, anything factual), BOOK (wants to reserve a table or is supplying a booking detail), CALLBACK (complaint, group larger than {large_group}, catering, or explicitly asks for a person), CHITCHAT (greeting, thanks, small talk), END (goodbye, done).

Fields: extract ONE only if the caller just supplied it: date, time, party_size, name, phone, email_raw. Emit field_value EXACTLY as heard — never normalise, correct, expand or reformat. If nothing was supplied, field_name and field_value are null.

confidence (0.0-1.0) is your confidence in the INTENT, not the field. wants_human is true only when the caller explicitly asks for a person.

Now in {tz}: {now}. Booking in progress: {booking}.

Return exactly: {{"intent": "...", "confidence": 0.0, "question": "verbatim question or null", "field_name": null, "field_value": null, "wants_human": false}}"""

REPAIR_SUFFIX = ("\n\nYour previous reply was not valid JSON matching the schema. Reply again with ONLY the JSON "
                 "object, no prose, no code fences.")


def build_system_prompt(slot_state: SlotState, now: dt.datetime, tz: str) -> str:
    local = now.astimezone(ZoneInfo(tz))
    if slot_state.stage in ("idle", "done"):
        booking = "none"
    else:
        have = {k: str(v) for k, v in slot_state.model_dump(mode="json").items()
                if k in ("date", "time", "party_size", "name") and v is not None}
        booking = f"yes, currently asking for '{slot_state.stage}'; have {have or 'nothing yet'}"
    return SYSTEM_PROMPT.format(large_group=threshold("LARGE_GROUP_THRESHOLD"), tz=tz,
                                now=local.strftime("%A %d %B %Y, %H:%M"), booking=booking)


def parse_router_output(text: str) -> RouterOutput:
    cleaned = _FENCE.sub("", text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in router reply")
    data = json.loads(cleaned[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("router reply is not an object")
    if data.get("field_name") in ("", "none", "null"):
        data["field_name"] = None
    if data.get("field_value") is not None:
        data["field_value"] = str(data["field_value"])
    if data.get("field_name") is not None and data["field_name"] not in FIELDS:
        raise ValueError(f"unknown field_name {data['field_name']!r}")
    return RouterOutput.model_validate(data)


@dataclass
class RouteResult:
    output: RouterOutput
    parse_failures: int
    fallback: bool
    ms: int


async def route(text: str, slot_state: SlotState, transport: RouterTransport, *, now: dt.datetime,
                tz: str) -> RouteResult:
    import time

    started = time.perf_counter()
    system = build_system_prompt(slot_state, now, tz)
    timeout_s = threshold("GROQ_TIMEOUT_MS") / 1000
    failures = 0
    user = f"Caller said: {text}"
    for attempt in range(2):
        try:
            raw = await asyncio.wait_for(transport.complete(system=system, user=user, timeout_s=timeout_s), timeout_s + 0.5)
            output = parse_router_output(raw)
            return RouteResult(output, failures, False, int((time.perf_counter() - started) * 1000))
        except (ValueError, ValidationError, json.JSONDecodeError, asyncio.TimeoutError, Exception):
            failures += 1
            user = f"Caller said: {text}" + REPAIR_SUFFIX
    fallback = RouterOutput(intent="CALLBACK", confidence=0.0, question=None, field_name=None, field_value=None,
                            wants_human=False)
    return RouteResult(fallback, failures, True, int((time.perf_counter() - started) * 1000))


def apply_low_confidence(output: RouterOutput, slot_state: SlotState) -> RouterOutput:
    """§7.1: below ROUTER_MIN_CONFIDENCE do not guess and do not clarify every turn."""
    if output.confidence >= threshold("ROUTER_MIN_CONFIDENCE") or output.intent == "END":
        return output
    if slot_state.stage not in ("idle", "done"):
        return output.model_copy(update={"intent": "BOOK"})
    return output.model_copy(update={"intent": "ANSWER_QUESTION"})
