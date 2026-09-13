"""Phase 5 (vr_plan.md §13): the real audio pipeline with fake providers, barge-in, the 280 s
cutover + resume, resumeCount > MAX_RESUMES, finalisation + rollups, and Twilio status callbacks."""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.index import build_deps, create_app
from api.voice.status import apply_call_status
from config import threshold, thresholds
from services.common.config import load_config
from services.voice import stream
from services.voice.audio import FRAME_BYTES_8K, pcm16_to_mulaw
from services.voice.call_pipeline import CallPipelineConfig, ReceptionistPipeline
from services.voice.pipeline import Outbound, PipelineDeps
from services.voice.providers import AudioProviders, EnergyVad, FakeStt, FakeTts, SilenceTurnDetector
from services.voice.session import CallSession
from services.voice.signature import compute_twilio_signature, mint_ws_token, public_url
from tests.conftest import VOICE_ENV
from tests.voice_helpers import BUSINESS_ID, FIXED_NOW, make_engine

GREETING = b"\x7f" * (FRAME_BYTES_8K * 2)
LOUD = pcm16_to_mulaw((np.sin(np.arange(160) / 8000 * 2 * np.pi * 440) * 12000).astype(np.int16))
QUIET = b"\xff" * FRAME_BYTES_8K


def _router(intent, field=None, value=None):
    return json.dumps({"intent": intent, "confidence": 0.9, "question": None, "field_name": field,
                       "field_value": value, "wants_human": False})


class FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self) -> None:
        self.closed = True

    def events(self, kind: str) -> list[dict]:
        return [m for m in self.sent if m["event"] == kind]


def _build(tmp_path, *, transcripts, router_scripts, cutover=True, greeting=GREETING):
    engine, edeps = make_engine(tmp_path, with_booking=True)
    engine.deps.router.scripts.extend(router_scripts)
    providers = AudioProviders(vad=EnergyVad(), turn=SilenceTurnDetector(min_silence_ms=200),
                               stt=FakeStt(transcripts), tts=FakeTts())
    deps = PipelineDeps(sink=edeps.sink, bus=edeps.bus, engine=engine, greeting=greeting, providers=providers,
                        business_id=BUSINESS_ID, cutover_enabled=cutover, clock=lambda: FIXED_NOW)
    return deps, providers


async def _feed(pipeline, loud_frames=15, quiet_frames=15):
    for _ in range(loud_frames):
        await pipeline.on_media(LOUD)
    for _ in range(quiet_frames):
        await pipeline.on_media(QUIET)
    if pipeline._turn_task:
        await pipeline._turn_task


def _session(call_id="CAsim_p", resume=False, from_number="+61400111222"):
    return CallSession(call_id=call_id, stream_sid="MZsim_p", business_id=BUSINESS_ID, from_number=from_number,
                       resume=resume, started_at=FIXED_NOW)


# ---- audio turn end to end -------------------------------------------------------------------------

def test_full_audio_turn_drives_the_same_engine_and_speaks_reply(tmp_path):
    async def run():
        deps, providers = _build(tmp_path, transcripts=["can I book a table"], router_scripts=[_router("BOOK")], cutover=False)
        pipeline = ReceptionistPipeline(deps, providers, CallPipelineConfig(business_id=BUSINESS_ID, cutover_enabled=False),
                                        clock=lambda: FIXED_NOW)
        session, sock = _session(), FakeSocket()
        out = Outbound(sock.send, session.stream_sid, session, close=sock.close)
        await pipeline.on_start(session, out)
        assert len(sock.events("media")) == 2 and sock.events("mark")[0]["mark"]["name"] == "greeting"
        await pipeline.on_mark("greeting")
        await _feed(pipeline)
        assert providers.stt.calls and providers.tts.spoken == ["What day would you like?"]
        assert session.slot_state.stage == "date"
        assert sock.events("mark")[-1]["mark"]["name"] == "turn-2"
        records = deps.sink.read(session.call_id)
        turns = [r["doc"] for r in records if r["kind"] == "turn"]
        assert [t["speaker"] for t in turns] == ["caller", "agent"]
        assert turns[0]["textFinal"] == "can I book a table" and turns[0]["sttMs"] is not None and turns[0]["routerMs"] is not None
        usage = [r["doc"] for r in records if r["kind"] == "usageEvent"]
        assert {u["provider"] for u in usage} >= {"elevenlabs_stt", "elevenlabs_tts"}
        assert all(u["costCents"] == 0 for u in usage), "pricing.yaml is TODO(spec): units recorded, cents 0"
        await pipeline.on_stop()  # socket stopped mid-booking, no END turn → hung up → abandoned (§6.7)
        call = deps.sink.read_call(session.call_id)
        assert call["endedAt"] and call["durationMs"] == 0 and call["outcome"] == "abandoned"
        assert call["hourLocal"] == 11 and call["weekdayLocal"] == "mon"
        rollups = deps.sink.read("_rollups")
        assert rollups[-1]["counters"]["calls"] == 1 and rollups[-1]["counters"]["outcome_abandoned"] == 1

    asyncio.run(run())


def test_barge_in_sends_clear_and_records_interruption(tmp_path):
    async def run():
        deps, providers = _build(tmp_path, transcripts=["what are your hours?"], router_scripts=[
            json.dumps({"intent": "ANSWER_QUESTION", "confidence": 0.9, "question": "what are your hours?",
                        "field_name": None, "field_value": None, "wants_human": False})], cutover=False)
        providers.tts = FakeTts(delay_s=0.05)
        pipeline = ReceptionistPipeline(deps, providers, CallPipelineConfig(business_id=BUSINESS_ID, cutover_enabled=False, barge_in_guard_ms=0),
                                        clock=lambda: FIXED_NOW)
        session, sock = _session(), FakeSocket()
        await pipeline.on_start(session, Outbound(sock.send, session.stream_sid, session, close=sock.close))
        await pipeline.on_mark("greeting")
        for _ in range(15):
            await pipeline.on_media(LOUD)
        for _ in range(12):
            await pipeline.on_media(QUIET)
        await asyncio.sleep(0.01)  # TTS is now in flight (delayed fake)
        assert session.agent_speaking or pipeline._tts_task is not None
        await pipeline.on_media(LOUD)  # caller talks over the agent
        assert sock.events("clear") and session.interrupted is True
        if pipeline._turn_task:
            try:
                await pipeline._turn_task
            except asyncio.CancelledError:
                pass
        interrupted = [r for r in deps.sink.read(session.call_id) if r["kind"] == "turn" and r["doc"].get("interrupted")]
        assert interrupted

    asyncio.run(run())


# ---- cutover + resume (§6.6) -----------------------------------------------------------------------

def test_cutover_persists_state_and_resume_continues_without_second_greeting(tmp_path):
    async def run():
        deps, providers = _build(tmp_path, transcripts=["can I book a table", "saturday", "seven", "four"],
                                 router_scripts=[_router("BOOK"), _router("BOOK", "date", "saturday"),
                                                 _router("BOOK", "time", "seven"), _router("BOOK", "party_size", "four")], cutover=False)
        pipeline = ReceptionistPipeline(deps, providers, CallPipelineConfig(business_id=BUSINESS_ID, cutover_enabled=False),
                                        clock=lambda: FIXED_NOW)
        session, sock = _session(), FakeSocket()
        await pipeline.on_start(session, Outbound(sock.send, session.stream_sid, session, close=sock.close))
        await pipeline.on_mark("greeting")
        for _ in range(3):
            await _feed(pipeline)
        assert session.slot_state.stage == "party_size"
        await pipeline.cutover_now()
        assert sock.closed and pipeline.cutover_triggered
        saved = deps.sink.read_call(session.call_id)
        assert saved["slotState"]["stage"] == "party_size" and saved["slotState"]["date"] == "2026-09-19"
        assert saved["historySummary"] and saved["disclosureDone"] is True and saved["resumeCount"] == 0
        await pipeline.on_stop()
        assert "endedAt" not in (deps.sink.read_call(session.call_id) or {}), "cutover is not the end of the call"

        # Twilio re-enters the webhook → new stream, same CallSid, resume=1
        resumed = _session(resume=True)
        sock2 = FakeSocket()
        pipeline2 = ReceptionistPipeline(deps, providers, CallPipelineConfig(business_id=BUSINESS_ID, cutover_enabled=False),
                                         clock=lambda: FIXED_NOW)
        await pipeline2.on_start(resumed, Outbound(sock2.send, resumed.stream_sid, resumed, close=sock2.close))
        assert resumed.slot_state.stage == "party_size" and resumed.resume_count == 1 and resumed.disclosure_done
        marks = [m["mark"]["name"] for m in sock2.events("mark")]
        assert "greeting" not in marks, "the disclosure plays exactly once across a cutover"
        spoken = " ".join(providers.tts.spoken)  # the fake records clause by clause, as streamed
        assert spoken.endswith("Right — where were we. I had a table on Saturday at seven. How many people?")
        await pipeline2.on_mark("resume")
        await _feed(pipeline2)
        assert resumed.slot_state.party_size == 4 and resumed.slot_state.stage == "name"
        assert deps.sink.read_call(resumed.call_id)["resumeCount"] == 1

    asyncio.run(run())


def test_resume_limit_ends_gracefully_with_callback(tmp_path):
    async def run():
        deps, providers = _build(tmp_path, transcripts=[], router_scripts=[], cutover=False)
        session = _session(resume=True)
        deps.sink.write_call(session.call_id, {"resumeCount": threshold("MAX_RESUMES"), "disclosureDone": True,
                                               "slotState": {"stage": "name", "party_size": 4}}, merge=False)
        sock = FakeSocket()
        pipeline = ReceptionistPipeline(deps, providers, CallPipelineConfig(business_id=BUSINESS_ID, cutover_enabled=False),
                                        clock=lambda: FIXED_NOW)
        await pipeline.on_start(session, Outbound(sock.send, session.stream_sid, session, close=sock.close))
        assert session.resume_count == threshold("MAX_RESUMES") + 1 and sock.closed
        from services.voice import templates

        assert " ".join(providers.tts.spoken) == templates.RESUME_LIMIT_GOODBYE
        records = deps.sink.read(session.call_id)
        assert any(r["kind"] == "callback" for r in records)
        assert deps.sink.read_call(session.call_id)["outcome"] == "callback"

    asyncio.run(run())


def test_cutover_timers_fire_from_thresholds(tmp_path, monkeypatch):
    monkeypatch.setitem(thresholds(), "CUTOVER_WARN_MS", 30)
    monkeypatch.setitem(thresholds(), "STREAM_CUTOVER_MS", 120)

    async def run():
        deps, providers = _build(tmp_path, transcripts=[], router_scripts=[], cutover=True)
        pipeline = ReceptionistPipeline(deps, providers, CallPipelineConfig(business_id=BUSINESS_ID, cutover_enabled=True),
                                        clock=lambda: FIXED_NOW)
        session, sock = _session(), FakeSocket()
        await pipeline.on_start(session, Outbound(sock.send, session.stream_sid, session, close=sock.close))
        await asyncio.sleep(0.3)
        assert " ".join(providers.tts.spoken).startswith("I might need to put you on hold for a second, but I've got everything so far.")
        assert sock.closed and pipeline.cutover_triggered
        await pipeline.on_stop()

    asyncio.run(run())


# ---- status callbacks (§6.7) ----------------------------------------------------------------------

def test_status_callback_sets_abandoned_and_duration_from_twilio(tmp_path):
    from services.voice.sinks import LocalJsonlSink

    sink = LocalJsonlSink(tmp_path / "runs", business_id=BUSINESS_ID)
    sink.write_call("CA1", {"callId": "CA1", "startedAt": FIXED_NOW.isoformat(), "slotState": {"stage": "time"}})
    doc = apply_call_status(sink, {"CallSid": "CA1", "CallStatus": "completed", "CallDuration": "47"},
                            tz="Australia/Melbourne", now=FIXED_NOW)
    assert doc["outcome"] == "abandoned" and doc["durationMs"] == 47000 and doc["twilioStatus"] == "completed"
    merged = sink.read_call("CA1")
    assert merged["outcome"] == "abandoned" and merged["hourLocal"] == 11 and merged["weekdayLocal"] == "mon"

    sink.write_call("CA2", {"callId": "CA2", "outcome": "booked", "slotState": {"stage": "done"}})
    assert apply_call_status(sink, {"CallSid": "CA2", "CallStatus": "completed", "CallDuration": "90"},
                             tz="Australia/Melbourne", now=FIXED_NOW).get("outcome") is None
    assert sink.read_call("CA2")["outcome"] == "booked"
    assert apply_call_status(sink, {"CallSid": "CA2", "CallStatus": "ringing"}, tz="Australia/Melbourne") is None


def test_status_route_writes_through_the_sink(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config({**VOICE_ENV, "SESSION_SINK": "local"})
    deps = build_deps(config, placeholder_engine=True, greeting=b"")
    client = TestClient(create_app(config, deps=deps))
    deps.sink.write_call("CA9", {"callId": "CA9", "slotState": {"stage": "email"}})
    params = {"CallSid": "CA9", "CallStatus": "completed", "CallDuration": "12"}
    url = public_url(VOICE_ENV["PUBLIC_BASE_URL"], "/api/voice/status")
    signature = compute_twilio_signature(VOICE_ENV["TWILIO_AUTH_TOKEN"], url, params)
    assert client.post("/api/voice/status", data=params, headers={"X-Twilio-Signature": signature}).status_code == 204
    assert deps.sink.read_call("CA9")["outcome"] == "abandoned" and deps.sink.read_call("CA9")["durationMs"] == 12000


# ---- the production factory through the real websocket ---------------------------------------------

def test_websocket_runs_receptionist_pipeline_with_fake_providers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine, edeps = make_engine(tmp_path, with_booking=True)
    engine.deps.router.scripts.append(_router("END"))
    providers = AudioProviders(vad=EnergyVad(), turn=SilenceTurnDetector(min_silence_ms=200), stt=FakeStt(["bye"]), tts=FakeTts())
    config = load_config({**VOICE_ENV, "SESSION_SINK": "local"})
    deps = PipelineDeps(sink=edeps.sink, bus=edeps.bus, engine=engine, greeting=GREETING, providers=providers,
                        business_id=BUSINESS_ID, cutover_enabled=False, clock=lambda: FIXED_NOW)
    client = TestClient(create_app(config, deps=deps))
    call_id, stream_sid = "CAsimws1", "MZsimws1"
    with client.websocket_connect("/api/voice/ws") as ws:
        ws.send_text(stream.inbound_connected_message())
        ws.send_text(stream.inbound_start_message(stream_sid, call_id, {
            "token": mint_ws_token(VOICE_ENV["WS_TOKEN_SECRET"], call_id), "callSid": call_id,
            "businessId": BUSINESS_ID, "resume": "0", "from": "+61400111222"}))
        for _ in range(2):
            assert json.loads(ws.receive_text())["event"] == "media"
        assert json.loads(ws.receive_text())["mark"]["name"] == "greeting"
        ws.send_text(stream.inbound_media_message(stream_sid, b"", sequence_number=2, chunk=1, timestamp_ms=0))  # harmless empty
        seq = 3
        for frame in [LOUD] * 15 + [QUIET] * 15:
            ws.send_text(stream.inbound_media_message(stream_sid, frame, sequence_number=seq, chunk=seq, timestamp_ms=20 * seq))
            seq += 1
        got = []
        while True:
            msg = json.loads(ws.receive_text())
            got.append(msg)
            if msg["event"] == "mark" and msg["mark"]["name"] == "end":
                break
        assert any(m["event"] == "media" for m in got)
        assert " ".join(providers.tts.spoken) == "Thanks for calling. Bye for now."
        ws.send_text(stream.inbound_media_message(stream_sid, QUIET, sequence_number=seq, chunk=seq, timestamp_ms=0))
        ws.send_text('{"event":"mark","streamSid":"%s","mark":{"name":"end"}}' % stream_sid)
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect):
            for _ in range(5):
                ws.receive_text()
    call = deps.sink.read_call(call_id)
    assert call["outcome"] == "answered" and call["endedAt"]
