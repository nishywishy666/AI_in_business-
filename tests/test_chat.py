import json

import pytest

from marketing_radar.agent import FakeGeminiTransport, RateLimited
from marketing_radar.agent.chat import ChatSession
from marketing_radar.jobs.scan import run_paid_scan
from marketing_radar.scrapers import SCAN_ENDPOINTS
from tests.conftest import make_scan_deps, seed_context


@pytest.fixture
def scanned(backend, store, settings, clock, sample_context_map):
    seed_context(backend, sample_context_map)
    deps, transport = make_scan_deps(store, settings, clock)
    brief = run_paid_scan(deps).brief
    return deps, transport, brief


def _sc_calls(transport):
    paths = {e.path for e in SCAN_ENDPOINTS.values()}
    return [c for c in transport.calls if c.path in paths]


def test_recommend_tomorrow_runs_tool_and_persists_thread(scanned, store):
    deps, transport, brief = scanned
    before = len(_sc_calls(transport))
    chat = ChatSession(deps, "t1")
    reply = chat.send("what should I post tomorrow?")
    assert reply.actions == ["recommend_tomorrow"] and brief.scan_id in reply.text
    assert reply.scan_id == brief.scan_id and reply.model_used == "gemini-3.8-flash"
    assert len(_sc_calls(transport)) == before, "chat never scrapes"

    roles = [m["role"] for m in chat.history()]
    assert roles == ["user", "tool", "assistant"]
    assert store.list(store.paths.chat_messages("t1"))
    assert store.list(store.paths.chat_messages("other")) == []


def test_captions_on_attached_post(scanned, store):
    deps, _, brief = scanned
    post_id = brief.film_this.post_id
    reply = ChatSession(deps, "t2").send("give me captions for this", attached_post_id=post_id)
    assert reply.actions == ["captions"]
    assert reply.text.count("\n") >= 2 and "#" in reply.text
    tool_msg = [m for m in ChatSession(deps, "t2").history() if m["role"] == "tool"][0]
    assert json.loads(tool_msg["content"])["args"]["post_id"] == post_id


def test_like_trend_tool_runs_like_flow(scanned, store):
    deps, _, brief = scanned
    post_id = brief.film_this.post_id
    reply = ChatSession(deps, "t3").send("like this trend and give me angles", attached_post_id=post_id)
    assert reply.actions == ["like_trend"]
    post = store.get(store.paths.post(post_id))
    assert post["liked"] is True and len(post["angles"]) == 3
    assert "Which one" in reply.text or "angles" in reply.text.lower()


def test_tool_without_post_id_returns_guidance(scanned):
    deps, _, _ = scanned
    reply = ChatSession(deps, "t4").send("rewrite the hook")
    assert reply.actions == ["rewrite_hook"] and reply.text


def test_unknown_or_repeated_tools_are_bounded(scanned):
    deps, _, _ = scanned
    deps.gemini.transport = FakeGeminiTransport(default=json.dumps({"reply": "", "tool": {"name": "get_brief", "args": {}}}))
    reply = ChatSession(deps, "t5").send("loop forever")
    assert reply.actions == ["get_brief", "get_brief"] and len(deps.gemini.transport.calls) == 3
    assert reply.text  # falls back to a non-empty message

    deps.gemini.transport = FakeGeminiTransport(default=json.dumps({"reply": "", "tool": {"name": "scrape_now", "args": {}}}))
    reply = ChatSession(deps, "t6").send("scrape more please")
    assert reply.actions == [] and "could not produce" in reply.text


def test_prose_reply_without_json_is_accepted(scanned):
    deps, _, _ = scanned
    deps.gemini.transport = FakeGeminiTransport(default="Just film the kettle one.")
    assert ChatSession(deps, "t7").send("hey").text == "Just film the kettle one."


def test_gemini_exhausted_gives_reset_time(scanned):
    deps, _, brief = scanned
    models = ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-2.0-flash-lite"]
    deps.gemini.transport = FakeGeminiTransport(scripts={m: [RateLimited("429")] for m in models})
    reply = ChatSession(deps, "t8").send("what's working?")
    assert "2026-09-12T07:00:00Z" in reply.text and brief.scan_id in reply.text
    assert reply.model_used is None


def test_chat_without_context_explains(store, settings, clock):
    deps, _ = make_scan_deps(store, settings, clock)
    reply = ChatSession(deps).send("hello")
    assert "questionnaire" in reply.text and reply.actions == []
