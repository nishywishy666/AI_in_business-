import base64
import json
import re
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from api.index import build_deps, create_app
from services.common.config import load_config
from services.voice import stream
from services.voice.audio import FRAME_BYTES_8K, pcm16_to_mulaw
from services.voice.pipeline import PlaceholderTurnEngine
from tests.conftest import VOICE_ENV

REPO_PAGE = __import__("pathlib").Path(__file__).resolve().parents[1] / "api" / "sim" / "sim.html"


@pytest.fixture
def sim_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config({**VOICE_ENV, "ENABLE_SIM": "1", "SESSION_SINK": "local"})
    deps = build_deps(config, engine=PlaceholderTurnEngine(), greeting=b"\x7f" * FRAME_BYTES_8K)
    app = create_app(config, deps=deps)
    return TestClient(app), deps


def test_mode_a_text_turn_shape_and_speed(sim_client):
    client, deps = sim_client
    started = time.perf_counter()
    first = client.post("/sim/text", json={"text": "do the toasties have nuts?"}).json()
    assert (time.perf_counter() - started) < 1.0
    for key in ("call_id", "turn_index", "intent", "confidence", "field", "tool_called", "tool_result",
                "slot_before", "slot_after", "reply_text", "timings_ms", "warnings", "elapsed_ms"):
        assert key in first, key
    assert first["call_id"].startswith("CAsim") and first["turn_index"] == 1 and first["sink"] == "local"
    assert first["slot_before"]["stage"] == "idle"
    second = client.post("/sim/text", json={"text": "table for four", "call_id": first["call_id"]}).json()
    assert second["turn_index"] == 2 and second["call_id"] == first["call_id"]
    records = client.get(f"/sim/runs/{first['call_id']}").json()["records"]
    assert [r["kind"] for r in records] == ["call", "turn", "turn"]
    assert records[1]["doc"]["speaker"] == "caller" and records[1]["doc"]["textFinal"] == "do the toasties have nuts?"


def test_token_and_page_and_budget(sim_client):
    client, _ = sim_client
    info = client.get("/sim/token").json()
    assert info["call_id"].startswith("CAsim") and info["stream_sid"].startswith("MZsim") and info["ws_path"] == "/api/voice/ws"
    assert client.get("/sim/token", params={"call_id": "CA_real_call"}).status_code == 400
    page = client.get("/sim").text
    assert "telephony fidelity" in page and "/sim/hud/" in page and "Mode A" in page
    budget = client.get("/sim/budget").json()
    assert budget["first_audio_qa_ms"] == [700, 1400] and budget["router_ms"] == [100, 250]
    assert client.get("/healthz").json()["sink"] == "local"


def test_mode_b_drives_production_ws_with_sim_token(sim_client):
    client, deps = sim_client
    info = client.get("/sim/token").json()
    call_id, stream_sid = info["call_id"], info["stream_sid"]
    with client.websocket_connect("/api/voice/ws") as ws:
        ws.send_text(stream.inbound_connected_message())
        ws.send_text(stream.inbound_start_message(stream_sid, call_id, {
            "token": info["token"], "callSid": call_id, "businessId": info["business_id"], "resume": "0",
        }))
        greeting = json.loads(ws.receive_text())
        assert greeting["event"] == "media" and greeting["streamSid"] == stream_sid
        assert json.loads(ws.receive_text())["event"] == "mark"
        ws.send_text(stream.inbound_stop_message(stream_sid, call_id, sequence_number=3))
    records = deps.sink.read(call_id)
    assert records and records[0]["kind"] == "call" and records[0]["doc"]["sessionType"] == "sim"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed; JS envelope check skipped")
def test_mode_b_javascript_envelopes_are_byte_identical_to_python():
    """The page's μ-law encoder and envelope builders must match services/voice/{audio,stream}.py."""
    html = REPO_PAGE.read_text(encoding="utf-8")
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    # Extract just the pure functions (no DOM) by evaluating the IIFE body up to the HUD section.
    head = script.split("// ---------- HUD ----------")[0]
    head = head.replace("(() => {", "").replace('const $ = (id) => document.getElementById(id);', "") \
               .replace('const status = (t) => { $("status").textContent = t; };', "") \
               .replace('fetch("/sim/budget").then(r => r.json()).then(b => budget = b);', "")
    samples = list(range(-32768, 32768, 997))
    js = head + f"""
    const samples = {json.dumps(samples)};
    const bytes = Uint8Array.from(samples.map(lin2ulaw));
    seq = 3; chunk = 1; tsMs = 20;
    process.stdout.write(JSON.stringify({{
      ulaw: Array.from(bytes),
      media: envMedia("MZsim1", bytes.slice(0, 160)),
      start: envStart("MZsim1", "CAsim1", {{token: "t", callSid: "CAsim1"}}),
      mark: envMark("MZsim1", "greeting"),
      stop: envStop("MZsim1", "CAsim1"),
      connected: envConnected(),
    }}));
    """
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    import numpy as np

    assert bytes(out["ulaw"]) == pcm16_to_mulaw(np.array(samples, dtype=np.int16))
    first160 = bytes(out["ulaw"][:160])
    assert out["media"] == stream.inbound_media_message("MZsim1", first160, sequence_number=3, chunk=1, timestamp_ms=20)
    assert out["start"] == stream.inbound_start_message("MZsim1", "CAsim1", {"token": "t", "callSid": "CAsim1"}, sequence_number=4)
    assert out["mark"] == json.dumps({"event": "mark", "sequenceNumber": "5", "streamSid": "MZsim1", "mark": {"name": "greeting"}}, separators=(",", ":"))
    assert out["stop"] == stream.inbound_stop_message("MZsim1", "CAsim1", sequence_number=6)
    assert out["connected"] == stream.inbound_connected_message()
