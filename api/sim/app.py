"""Local test simulator (vr_plan.md §12). Refuses to mount unless ENABLE_SIM=1, and refuses to
mount in a process whose sink could write to production Firestore (V12 — structural isolation).

Mode A — POST /sim/text: drives TurnEngine.run_turn directly (no VAD/STT/TTS).
Mode B — the /sim page connects to the PRODUCTION /api/voice/ws with Twilio envelopes and a
valid token minted by GET /sim/token; the HUD subscribes to /sim/hud/{callId}.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from contracts.voice import TurnLog
from services.common.config import VoiceConfig
from services.voice.pipeline import PipelineDeps
from services.voice.session import CallSession
from services.voice.signature import mint_ws_token
from services.voice.sinks import LocalJsonlSink
from services.voice.telemetry import TurnRecorder

PAGE = Path(__file__).with_name("sim.html")
LATENCY_BUDGET = {
    "twilio_network_ms": [40, 90], "vad_ms": [0, 0], "smart_turn_ms": [10, 100], "stt_ms": [150, 300],
    "router_ms": [100, 250], "answer_ms": [300, 600], "tts_first_byte_ms": [75, 150],
    "first_audio_booking_ms": [400, 800], "first_audio_qa_ms": [700, 1400], "booking_write_ms": [100, 300],
}


class SimIsolationError(RuntimeError):
    pass


class TextTurn(BaseModel):
    text: str
    call_id: str | None = None
    from_number: str | None = None


def new_call_id() -> str:
    return f"CAsim{secrets.token_hex(8)}"


def new_stream_id() -> str:
    return f"MZsim{secrets.token_hex(8)}"


class SimSessions:
    """Mode A sessions kept in-process so slot state persists across POSTs."""

    def __init__(self, deps: PipelineDeps, business_id: str) -> None:
        self.deps = deps
        self.business_id = business_id
        self.sessions: dict[str, CallSession] = {}
        self.recorders: dict[str, TurnRecorder] = {}
        self.greetings: dict[str, str] = {}

    def get_or_create(self, call_id: str | None, from_number: str | None) -> tuple[CallSession, TurnRecorder]:
        call_id = call_id or new_call_id()
        if call_id not in self.sessions:
            session = CallSession(call_id=call_id, stream_sid=new_stream_id(), business_id=self.business_id,
                                  from_number=from_number, disclosure_done=True)
            from services.voice.pipeline import _call_start_doc

            self.deps.sink.write_call(call_id, _call_start_doc(session))
            self.sessions[call_id] = session
            recorder = TurnRecorder(self.deps.sink, self.deps.bus, call_id)
            self.recorders[call_id] = recorder
            greeting = getattr(self.deps.engine, "greeting_text", None)
            if greeting is not None:
                # Mode A mirrors the phone path: the disclosure is the first agent utterance (Rule 3).
                text = greeting(session)
                session.remember("agent", text)
                recorder.record(TurnLog(call_id=call_id, turn_index=0, speaker="agent", text_final=text,
                                        answer_source="template", at=dt.datetime.now(dt.timezone.utc)))
                self.greetings[call_id] = text
        return self.sessions[call_id], self.recorders[call_id]


def mount_sim(app: FastAPI, config: VoiceConfig, deps: PipelineDeps) -> APIRouter:
    if not config.enable_sim:
        raise SimIsolationError("simulator not enabled (ENABLE_SIM=1 required)")
    if deps.sink.kind == "firestore":
        raise SimIsolationError("simulator refuses to mount with SESSION_SINK=firestore; use local or emulator")
    if deps.sink.kind == "local" and not isinstance(deps.sink, LocalJsonlSink):
        raise SimIsolationError("local sink must be LocalJsonlSink")

    router = APIRouter(prefix="/sim")
    sessions = SimSessions(deps, config.business_id)

    @router.get("", response_class=HTMLResponse)
    @router.get("/", response_class=HTMLResponse)
    async def page() -> HTMLResponse:
        return HTMLResponse(PAGE.read_text(encoding="utf-8"))

    @router.get("/budget")
    async def budget() -> dict:
        return LATENCY_BUDGET

    @router.get("/token")
    async def token(call_id: str | None = None) -> dict:
        call_id = call_id or new_call_id()
        if not call_id.startswith("CAsim"):
            raise HTTPException(400, "simulator call ids must start with CAsim")
        return {
            "call_id": call_id, "stream_sid": new_stream_id(), "business_id": config.business_id,
            "token": mint_ws_token(config.ws_token_secret, call_id), "ws_path": "/api/voice/ws",
            "sink": deps.sink.kind,
        }

    @router.post("/text")
    async def text_turn(turn: TextTurn) -> JSONResponse:
        started = time.perf_counter()
        is_new = turn.call_id not in sessions.sessions
        session, recorder = sessions.get_or_create(turn.call_id, turn.from_number)
        result = await deps.engine.run_turn(session, turn.text, recorder=recorder)
        body = result.to_dict()
        body["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        body["sink"] = deps.sink.kind
        body["greeting_text"] = sessions.greetings.get(session.call_id) if is_new else None
        return JSONResponse(body)

    @router.get("/runs/{call_id}")
    async def run(call_id: str) -> dict:
        if not isinstance(deps.sink, LocalJsonlSink):
            raise HTTPException(404, "run files only exist with the local sink")
        return {"call_id": call_id, "records": deps.sink.read(call_id)}

    @router.websocket("/hud/{call_id}")
    async def hud(websocket: WebSocket, call_id: str) -> None:
        await websocket.accept()
        queue = deps.bus.subscribe(call_id)
        try:
            await websocket.send_json({"event": "hud_ready", "call_id": call_id,
                                       "ts": dt.datetime.now(dt.timezone.utc).isoformat()})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    await websocket.send_json({"event": "ping"})
                    continue
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            deps.bus.unsubscribe(call_id, queue)

    app.include_router(router)
    app.state.sim_sessions = sessions
    return router
