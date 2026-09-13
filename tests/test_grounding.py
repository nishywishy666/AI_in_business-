"""Runs tests/conversations/*.yaml through Mode A (POST /sim/text), the production turn engine
with fake Groq/Gemini transports and the TEST-ONLY business documents (vr_plan.md §13 Phase 3)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from api.index import build_deps, create_app
from config.disclaimers import CROSS_CONTACT
from services.common.config import load_config
from services.voice.answerer import FakeAnswerTransport
from services.voice.router import FakeRouterTransport
from tests.conftest import VOICE_ENV
from tests.voice_helpers import make_engine

SUITES = sorted((Path(__file__).parent / "conversations").glob("*.yaml"))


def _router_script(turn: dict) -> list:
    if "router_raw" in turn:
        return list(turn["router_raw"])
    out = {"intent": "CHITCHAT", "confidence": 0.9, "question": None, "field_name": None, "field_value": None,
           "wants_human": False}
    out.update(turn.get("router") or {})
    return [json.dumps(out)]


def _answerer(suite: dict) -> FakeAnswerTransport:
    if suite.get("answerer") == "timeout":
        return FakeAnswerTransport(delay_s=5.0)
    if suite.get("answerer") == "error":
        return FakeAnswerTransport(error=RuntimeError("429"))
    return FakeAnswerTransport(reply=suite.get("answerer_reply"))


@pytest.mark.parametrize("suite_path", SUITES, ids=[p.stem for p in SUITES])
def test_conversation_suite(suite_path, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(__import__("config").thresholds(), "GEMINI_TIMEOUT_MS", 200)
    suite = yaml.safe_load(suite_path.read_text())
    router = FakeRouterTransport()
    answerer = _answerer(suite)
    engine, deps = make_engine(tmp_path, router=router, answerer=answerer)
    config = load_config({**VOICE_ENV, "ENABLE_SIM": "1", "SESSION_SINK": "local"})
    app = create_app(config, deps=build_deps(config, sink=deps.sink, engine=engine, greeting=b""))
    client = TestClient(app)

    call_id = None
    for index, turn in enumerate(suite["turns"]):
        router.scripts.extend(_router_script(turn))
        gemini_calls_before = len(answerer.calls)
        body = client.post("/sim/text", json={"text": turn["caller"], "call_id": call_id,
                                              "from_number": "+61400111222"}).json()
        call_id = body["call_id"]
        expect = turn.get("expect") or {}
        context = f"{suite['name']} turn {index}: {json.dumps(body, default=str)[:800]}"

        if "greeting_contains" in expect:
            assert body["greeting_text"], context
            for needle in expect["greeting_contains"]:
                assert needle in body["greeting_text"], (needle, context)
        for key in ("intent", "tool_called", "answer_source", "used_fallback", "confidence"):
            if key in expect:
                assert body[key] == expect[key], (key, context)
        for needle in expect.get("reply_contains", []):
            assert needle in body["reply_text"], (needle, context)
        for needle in expect.get("reply_not_contains", []):
            assert needle.lower() not in body["reply_text"].lower(), (needle, context)
        if expect.get("reply_nonempty"):
            assert body["reply_text"].strip(), context
        if "reply_max_words" in expect:
            assert len(body["reply_text"].split()) <= expect["reply_max_words"], context
        if expect.get("reply_ends_with_cross_contact"):
            assert body["reply_text"].endswith(CROSS_CONTACT), context
            assert body["reply_text"].count(CROSS_CONTACT) == 1, context
        if "callback_reason" in expect:
            records = deps.sink.read(call_id)
            reasons = [r["doc"]["reason"] for r in records if r["kind"] == "callback"]
            assert expect["callback_reason"] in reasons, (reasons, context)
        if "tool_result_has" in expect:
            for key, value in expect["tool_result_has"].items():
                assert _deep_get(body["tool_result"], key) == value, (key, value, context)
        if "max_items_in_tool_result" in expect:
            assert len(body["tool_result"]["items"]) <= expect["max_items_in_tool_result"], context
        if "reply_mentions_at_most" in expect:
            names = [i["name"] for i in body["tool_result"]["items"]]
            assert sum(1 for n in names if n.lower() in body["reply_text"].lower()) <= expect["reply_mentions_at_most"], context
        for needle in expect.get("warnings_contain", []):
            assert any(needle in w for w in body["warnings"]), (needle, context)
        if expect.get("no_gemini_call"):
            assert len(answerer.calls) == gemini_calls_before, context
        for needle in expect.get("gemini_prompt_not_contains", []):
            for call in answerer.calls[gemini_calls_before:]:
                assert needle not in call["prompt"] and needle not in call["system"], (needle, context)
        assert body["elapsed_ms"] < 1000 or suite.get("answerer") == "timeout", context

    records = deps.sink.read(call_id)
    caller_turns = [r for r in records if r["kind"] == "turn" and r["doc"]["speaker"] == "caller"]
    assert len(caller_turns) == len(suite["turns"]), "one turn document per final caller turn"
    assert all("textFinal" in r["doc"] for r in caller_turns)


def _deep_get(obj, dotted: str):
    current = obj
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def test_every_spec_suite_exists():
    names = {p.stem for p in SUITES}
    assert names >= {"disclosure", "menu_question", "menu_recitation", "price_unknown", "allergen_confirmed",
                     "allergen_unknown", "fact_missing", "router_garbage", "gemini_timeout", "chitchat"}


def test_unconfirmed_item_is_invisible(tmp_path):
    engine, deps = make_engine(tmp_path)
    from services.voice.session import CallSession

    ctx = engine.context_for(CallSession(call_id="CAsimx", stream_sid="MZsimx", business_id="biz_test"))
    assert "Secret Special" not in [i.name for i in ctx.items]
    assert ctx.find_item("secret special") is None
    assert ctx.business_name == "Toastie Test Kitchen"
    assert re.fullmatch(r"\$\d+\.\d{2}", ctx.find_item("flat white").price_spoken)
