import base64
import json

import pytest
from fastapi.testclient import TestClient

from api.index import create_app
from services.voice import stream
from services.voice.audio import FRAME_BYTES_8K
from services.voice.pipeline import EchoPipeline
from services.voice.signature import compute_twilio_signature, mint_ws_token, public_url
from tests.conftest import VOICE_ENV

CALL_SID = "CA0123456789abcdef"
STREAM_SID = "MZ0123456789abcdef"
GREETING = b"\x7f" * (FRAME_BYTES_8K * 3)


@pytest.fixture
def client(voice_config):
    from api.index import build_deps

    deps = build_deps(voice_config, placeholder_engine=True, greeting=GREETING)
    app = create_app(voice_config, deps=deps, pipeline_factory=lambda session: EchoPipeline(GREETING, buffer_frames=2))
    return TestClient(app)


def _signed_post(client, path, params, *, query=""):
    url = public_url(VOICE_ENV["PUBLIC_BASE_URL"], path, query)
    signature = compute_twilio_signature(VOICE_ENV["TWILIO_AUTH_TOKEN"], url, params)
    return client.post(path + (f"?{query}" if query else ""), data=params, headers={"X-Twilio-Signature": signature})


# ---- envelopes ---------------------------------------------------------------------------

def test_parse_start_media_mark_stop():
    start = stream.parse_event(stream.inbound_start_message(STREAM_SID, CALL_SID, {"token": "t", "callSid": CALL_SID}))
    assert isinstance(start, stream.Start) and start.call_sid == CALL_SID and start.custom_parameters["token"] == "t"
    assert start.media_format["encoding"] == "audio/x-mulaw"
    media = stream.parse_event(stream.inbound_media_message(STREAM_SID, b"\xff" * 160, sequence_number=3, chunk=1, timestamp_ms=20))
    assert isinstance(media, stream.Media) and media.timestamp_ms == 20 and base64.b64decode(media.payload_b64) == b"\xff" * 160
    mark = stream.parse_event('{"event":"mark","streamSid":"MZ1","mark":{"name":"greeting"}}')
    assert isinstance(mark, stream.Mark) and mark.name == "greeting"
    stop = stream.parse_event(stream.inbound_stop_message(STREAM_SID, CALL_SID, sequence_number=9))
    assert isinstance(stop, stream.Stop) and stop.call_sid == CALL_SID
    assert isinstance(stream.parse_event(stream.inbound_connected_message()), stream.Connected)
    with pytest.raises(stream.StreamProtocolError):
        stream.parse_event('{"event":"bogus"}')


def test_outbound_envelopes_are_exact_and_deterministic():
    assert stream.media_message("MZ1", b"\xff\xff") == '{"event":"media","streamSid":"MZ1","media":{"payload":"//8="}}'
    assert stream.mark_message("MZ1", "greeting") == '{"event":"mark","streamSid":"MZ1","mark":{"name":"greeting"}}'
    assert stream.clear_message("MZ1") == '{"event":"clear","streamSid":"MZ1"}'
    assert len(stream.media_messages("MZ1", b"\xff" * 400)) == 3, "chunked at 160 bytes"
    inbound = json.loads(stream.inbound_media_message("MZsim1", b"\x00" * 160, sequence_number=3, chunk=1, timestamp_ms=20))
    assert list(inbound) == ["event", "sequenceNumber", "streamSid", "media"]
    assert list(inbound["media"]) == ["track", "chunk", "timestamp", "payload"]


# ---- webhook -----------------------------------------------------------------------------

def test_unsigned_incoming_is_403(client):
    assert client.post("/api/voice/incoming", data={"CallSid": CALL_SID}).status_code == 403
    assert client.post("/api/voice/incoming", data={"CallSid": CALL_SID}, headers={"X-Twilio-Signature": "nope"}).status_code == 403


def test_signed_incoming_returns_connect_stream_twiml(client):
    params = {"CallSid": CALL_SID, "From": "+61400111222", "To": VOICE_ENV["TWILIO_PHONE_NUMBER"],
              "CallStatus": "ringing", "AccountSid": "ACtest", "Direction": "inbound"}
    response = _signed_post(client, "/api/voice/incoming", params)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/xml")
    xml = response.text
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<Connect><Stream" in xml and 'url="wss://voice.example.test/api/voice/ws"' in xml
    assert 'statusCallback="https://voice.example.test/api/voice/status"' in xml
    assert '<Parameter name="callSid" value="CA0123456789abcdef" />' in xml
    assert '<Parameter name="businessId" value="biz_test" />' in xml
    assert '<Parameter name="resume" value="0" />' in xml
    assert '<Parameter name="from" value="+61400111222" />' in xml
    assert '<Redirect method="POST">/api/voice/incoming?resume=1</Redirect>' in xml
    assert "<Start>" not in xml, "must be bidirectional <Connect>, not a one-way <Start> fork"

    resumed = _signed_post(client, "/api/voice/incoming", params, query="resume=1")
    assert '<Parameter name="resume" value="1" />' in resumed.text


def test_fallback_and_status_and_health(client):
    fallback = client.post("/api/voice/fallback", data={"CallSid": CALL_SID, "ErrorCode": "11200"})
    assert fallback.status_code == 200 and "<Say>" in fallback.text and "<Hangup />" in fallback.text
    assert client.post("/api/voice/status", data={"CallSid": CALL_SID, "StreamEvent": "stream-error"}).status_code == 403
    signed = _signed_post(client, "/api/voice/status", {"CallSid": CALL_SID, "StreamEvent": "stream-started", "StreamSid": STREAM_SID})
    assert signed.status_code == 204
    assert client.get("/healthz").json() == {"ok": True, "business_id": "biz_test", "sink": "local"}


# ---- websocket ---------------------------------------------------------------------------

def _token(call_sid=CALL_SID):
    return mint_ws_token(VOICE_ENV["WS_TOKEN_SECRET"], call_sid)


def _start(ws, token, call_sid=CALL_SID, param_sid=None):
    ws.send_text(stream.inbound_connected_message())
    ws.send_text(stream.inbound_start_message(STREAM_SID, call_sid, {
        "token": token, "callSid": param_sid or call_sid, "businessId": "biz_test", "resume": "0", "from": "+61400111222",
    }))


def _closed_without_data(ws):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_text()
    return exc.value.code


def test_ws_without_valid_token_is_closed(client):
    with client.websocket_connect("/api/voice/ws") as ws:
        _start(ws, "bad.token")
        assert _closed_without_data(ws) == 1008
    with client.websocket_connect("/api/voice/ws") as ws:
        _start(ws, _token("CAother"))  # token minted for a different CallSid
        assert _closed_without_data(ws) == 1008
    with client.websocket_connect("/api/voice/ws") as ws:
        _start(ws, _token(), param_sid="CAmismatch")
        assert _closed_without_data(ws) == 1008
    with client.websocket_connect("/api/voice/ws") as ws:  # media before start
        ws.send_text(stream.inbound_media_message(STREAM_SID, b"\xff" * 160, sequence_number=1, chunk=1, timestamp_ms=20))
        assert _closed_without_data(ws) == 1008


def test_ws_plays_greeting_then_echoes_and_blocks_replay(client):
    token = _token()
    with client.websocket_connect("/api/voice/ws") as ws:
        _start(ws, token)
        greeting_frames = [json.loads(ws.receive_text()) for _ in range(3)]
        assert all(m["event"] == "media" and m["streamSid"] == STREAM_SID for m in greeting_frames)
        assert b"".join(base64.b64decode(m["media"]["payload"]) for m in greeting_frames) == GREETING
        mark = json.loads(ws.receive_text())
        assert mark == {"event": "mark", "streamSid": STREAM_SID, "mark": {"name": "greeting"}}
        ws.send_text('{"event":"mark","streamSid":"%s","mark":{"name":"greeting"}}' % STREAM_SID)

        caller = bytes(range(160)) + bytes(range(160))[::-1]
        for i, frame in enumerate((caller[:160], caller[160:])):
            ws.send_text(stream.inbound_media_message(STREAM_SID, frame, sequence_number=i + 2, chunk=i + 1, timestamp_ms=20 * i))
        echoed = [json.loads(ws.receive_text()) for _ in range(2)]
        assert b"".join(base64.b64decode(m["media"]["payload"]) for m in echoed) == caller
        assert json.loads(ws.receive_text())["event"] == "mark"
        ws.send_text(stream.inbound_stop_message(STREAM_SID, CALL_SID, sequence_number=10))

    with client.websocket_connect("/api/voice/ws") as ws:
        _start(ws, token)  # replay of a consumed token
        assert _closed_without_data(ws) == 1008


def test_ws_ignores_outbound_track_and_bad_json(client):
    with client.websocket_connect("/api/voice/ws") as ws:
        _start(ws, _token())
        for _ in range(4):
            ws.receive_text()  # greeting + mark
        ws.send_text("not json")
        ws.send_text(stream.inbound_media_message(STREAM_SID, b"\xff" * 160, sequence_number=2, chunk=1, timestamp_ms=0, track="outbound"))
        ws.send_text(stream.inbound_media_message(STREAM_SID, b"\x00" * 160, sequence_number=3, chunk=2, timestamp_ms=20))
        ws.send_text(stream.inbound_media_message(STREAM_SID, b"\x00" * 160, sequence_number=4, chunk=3, timestamp_ms=40))
        echoed = json.loads(ws.receive_text())
        assert base64.b64decode(echoed["media"]["payload"]) == b"\x00" * 160
