import asyncio
import datetime as dt
import json

import pytest

from config import threshold
from contracts.voice import SlotState
from services.voice.answerer import FakeAnswerTransport, phrase, resolve_model
from services.voice.router import (
    FakeRouterTransport,
    apply_low_confidence,
    build_system_prompt,
    parse_router_output,
    route,
)

NOW = dt.datetime(2026, 9, 14, 1, 0, tzinfo=dt.timezone.utc)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_system_prompt_is_short_and_injects_only_time_and_slot_state():
    prompt = build_system_prompt(SlotState(), NOW, "Australia/Melbourne")
    assert len(prompt.split()) < 300, "must stay under ~400 tokens; it is sent every turn"
    assert "Monday 14 September 2026, 11:00" in prompt and "Booking in progress: none" in prompt
    assert str(threshold("LARGE_GROUP_THRESHOLD")) in prompt
    for forbidden in ("Toastie", "$", "menu item", "facts:"):
        assert forbidden not in prompt
    mid = build_system_prompt(SlotState(stage="time", date=dt.date(2026, 9, 20), party_size=4), NOW, "Australia/Melbourne")
    assert "asking for 'time'" in mid and "'party_size': '4'" in mid


def test_parse_keeps_field_value_verbatim_and_rejects_unknown_fields():
    out = parse_router_output('```json\n{"intent":"BOOK","confidence":0.8,"question":null,"field_name":"date","field_value":"this sat arvo","wants_human":false}\n```')
    assert out.field_value == "this sat arvo" and out.intent == "BOOK"
    with pytest.raises(ValueError):
        parse_router_output('{"intent":"BOOK","confidence":0.8,"field_name":"favourite_colour","field_value":"x"}')
    with pytest.raises(Exception):
        parse_router_output('{"intent":"ORDER_PIZZA","confidence":0.8}')
    with pytest.raises(ValueError):
        parse_router_output("just prose")


def test_route_repairs_once_then_falls_back_to_callback():
    ok = json.dumps({"intent": "ANSWER_QUESTION", "confidence": 0.9, "question": "hours?", "field_name": None,
                     "field_value": None, "wants_human": False})
    transport = FakeRouterTransport(["garbage", ok])
    result = _run(route("hours?", SlotState(), transport, now=NOW, tz="Australia/Melbourne"))
    assert result.output.intent == "ANSWER_QUESTION" and result.parse_failures == 1 and not result.fallback
    assert "not valid JSON" in transport.calls[1]["user"]

    transport = FakeRouterTransport(["garbage", "more garbage"])
    result = _run(route("hours?", SlotState(), transport, now=NOW, tz="Australia/Melbourne"))
    assert result.fallback and result.output.intent == "CALLBACK" and result.output.confidence == 0.0
    assert result.parse_failures == 2 and len(transport.calls) == 2


def test_route_timeout_counts_as_failure(monkeypatch):
    class Slow(FakeRouterTransport):
        async def complete(self, **kw):
            await asyncio.sleep(2)
            return "{}"

    monkeypatch.setitem(__import__("config").thresholds(), "GROQ_TIMEOUT_MS", 50)
    result = _run(route("x", SlotState(), Slow(), now=NOW, tz="Australia/Melbourne"))
    assert result.fallback and result.output.intent == "CALLBACK"


def test_low_confidence_policy():
    from contracts.voice import RouterOutput

    low = RouterOutput(intent="CHITCHAT", confidence=0.3)
    assert apply_low_confidence(low, SlotState()).intent == "ANSWER_QUESTION"
    assert apply_low_confidence(low, SlotState(stage="date")).intent == "BOOK"
    assert apply_low_confidence(RouterOutput(intent="CHITCHAT", confidence=0.8), SlotState()).intent == "CHITCHAT"
    assert apply_low_confidence(RouterOutput(intent="END", confidence=0.2), SlotState()).intent == "END"


def test_answerer_fallback_and_trim(monkeypatch):
    monkeypatch.setitem(__import__("config").thresholds(), "GEMINI_TIMEOUT_MS", 100)
    slow = FakeAnswerTransport(delay_s=1.0)
    answer = _run(phrase(slow, fact={"value": "7am to 3pm"}, question="hours?", history=[], business_name="T",
                         fallback="We're open 7am to 3pm."))
    assert answer.used_fallback and answer.text == "We're open 7am to 3pm." and "gemini fallback" in answer.warning

    long_reply = FakeAnswerTransport(reply="First sentence here. Second sentence here. " + "word " * 60)
    answer = _run(phrase(long_reply, fact={"value": "x"}, question="q", history=[], business_name="T", fallback="f"))
    assert not answer.used_fallback and answer.text.startswith("First sentence here. Second sentence here.")
    assert "trimmed" in answer.warning
    assert answer.text.count("word") == 0

    markdown = FakeAnswerTransport(reply="**We're open** `7am`.")
    assert _run(phrase(markdown, fact={}, question="q", history=[], business_name="T", fallback="f")).text == "We're open 7am."


def test_model_resolution_skips_preview_and_pro():
    available = {"gemini-3.8-flash-preview", "gemini-3.5-flash", "gemini-3.5-pro"}
    assert resolve_model(available, ["gemini-3.8-flash", "gemini-3.5-flash"]) == "gemini-3.5-flash"
    assert resolve_model(available, ["gemini-3.8-flash-preview", "gemini-3.5-flash"]) == "gemini-3.5-flash"
    with pytest.raises(RuntimeError):
        resolve_model({"gemini-2.0-flash"}, ["gemini-3.8-flash"])
