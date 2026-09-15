"""Transport-agnostic pipeline assembly (vr_plan.md §4, §5, §12).

`Outbound` is the only thing that knows how to talk back to Twilio (or the simulator — same
envelopes). `MediaHandler` is what ws.py drives. `TurnEngine` is the per-caller-turn brain shared
by the audio path and the simulator's text harness (R7): Mode A calls `run_turn` directly, the
audio pipeline calls it after STT. Phase 2 ships the interfaces plus a placeholder engine; Phase 3
replaces the placeholder with router → tools → answerer.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from contracts.voice import TurnLog

from .audio import FRAME_BYTES_8K
from .session import GREETING_MARK, CallSession
from .sinks import CallSink
from .stream import clear_message, mark_message, media_messages
from .telemetry import HudBus, StageTimer, TurnRecorder

SendText = Callable[[str], Awaitable[None]]
log = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets" / "voice"
GREETING_FILE = ASSETS_DIR / "greeting.ulaw"


class Outbound:
    """Sends media / mark / clear on a stream. Chunks audio at ~20 ms (§6.4)."""

    def __init__(self, send_text: SendText, stream_sid: str, session: CallSession,
                 close: Callable[[], Awaitable[None]] | None = None) -> None:
        self._send = send_text
        self._close = close
        self.stream_sid = stream_sid
        self.session = session
        self.sent_frames = 0
        self.closed = False

    async def close(self) -> None:
        """Server-side close — what makes Twilio move to the <Redirect> verb promptly (§6.6)."""
        if self.closed:
            return
        self.closed = True
        if self._close is not None:
            try:
                await self._close()
            except Exception:
                pass

    async def audio(self, mulaw: bytes, *, mark: str | None = None) -> None:
        if mulaw:
            self.session.agent_speaking = True
            for message in media_messages(self.stream_sid, mulaw):
                await self._send(message)
                self.sent_frames += 1
        if mark:
            self.session.pending_marks.add(mark)
            await self._send(mark_message(self.stream_sid, mark))

    async def mark(self, name: str) -> None:
        self.session.pending_marks.add(name)
        await self._send(mark_message(self.stream_sid, name))

    async def clear(self) -> None:
        await self._send(clear_message(self.stream_sid))
        self.session.agent_speaking = False

    def mark_played(self, name: str) -> None:
        self.session.pending_marks.discard(name)
        if not self.session.pending_marks:
            self.session.agent_speaking = False


class MediaHandler(Protocol):
    async def on_start(self, session: CallSession, out: Outbound) -> None: ...

    async def on_media(self, mulaw: bytes) -> None: ...

    async def on_mark(self, name: str) -> None: ...

    async def on_stop(self) -> None: ...


# ---- the per-turn brain shared by phone and simulator (R7) --------------------------------

@dataclass
class TurnResult:
    call_id: str
    turn_index: int
    text: str
    reply_text: str
    intent: str | None = None
    confidence: float | None = None
    field_name: str | None = None
    field_value: str | None = None
    tool_called: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_result: Any = None
    answer_source: str | None = None
    used_fallback: bool = False
    slot_before: dict = field(default_factory=dict)
    slot_after: dict = field(default_factory=dict)
    email_candidate: dict | None = None
    timings_ms: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    end_call: bool = False

    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id, "turn_index": self.turn_index, "text": self.text, "reply_text": self.reply_text,
            "intent": self.intent, "confidence": self.confidence,
            "field": {"name": self.field_name, "value": self.field_value} if self.field_name else None,
            "tool_called": self.tool_called, "tool_args": self.tool_args, "tool_result": self.tool_result,
            "answer_source": self.answer_source, "used_fallback": self.used_fallback,
            "slot_before": self.slot_before, "slot_after": self.slot_after, "email_candidate": self.email_candidate,
            "timings_ms": self.timings_ms, "warnings": self.warnings, "end_call": self.end_call,
        }


class TurnEngine(Protocol):
    async def run_turn(self, session: CallSession, text: str, *, recorder: TurnRecorder,
                       stt_ms: int | None = None) -> TurnResult: ...


class PlaceholderTurnEngine:
    """Phase 2 stand-in: records the turn, answers nothing, and says so. Replaced in Phase 3."""

    async def run_turn(self, session: CallSession, text: str, *, recorder: TurnRecorder,
                       stt_ms: int | None = None) -> TurnResult:
        timer = StageTimer()
        timer.start("router")
        before = session.slot_state.model_dump(mode="json")
        session.turn_index += 1
        caller_index = session.turn_index
        timer.stop("router")
        recorder.record(TurnLog(call_id=session.call_id, turn_index=caller_index, speaker="caller", text_final=text,
                                stt_ms=stt_ms, at=dt.datetime.now(dt.timezone.utc)))
        warning = "turn engine placeholder: router/answerer not built yet (Phase 3)"
        recorder.warn(warning)
        return TurnResult(call_id=session.call_id, turn_index=caller_index, text=text, reply_text="",
                          slot_before=before, slot_after=session.slot_state.model_dump(mode="json"),
                          timings_ms={"stt": stt_ms or 0, **timer.ms}, warnings=[warning])


# ---- greeting -------------------------------------------------------------------------------

def load_greeting(path: Path = GREETING_FILE) -> bytes | None:
    """Pre-rendered disclosure (§7.4). Missing means scripts/render_greeting.py has not run —
    logged loudly, never synthesised at call time."""
    if path.exists():
        return path.read_bytes()
    log.error("greeting asset missing — run scripts/render_greeting.py", extra={"path": str(path)})
    return None


async def play_greeting(session: CallSession, out: Outbound, greeting: bytes | None) -> None:
    """Disclosure plays once per call (tracked by disclosureDone), never once per stream."""
    if session.disclosure_done:
        return
    await out.audio(greeting or b"", mark=GREETING_MARK)
    session.disclosure_done = True


# ---- pipelines -----------------------------------------------------------------------------

class EchoPipeline:
    """Phase 1/2: play the greeting, then echo the caller's audio back (§13 Phase 1 gate)."""

    def __init__(self, greeting: bytes | None = None, *, buffer_frames: int = 10,
                 sink: CallSink | None = None, bus: HudBus | None = None) -> None:
        self.greeting = greeting
        self.buffer_frames = buffer_frames
        self.sink = sink
        self.bus = bus or HudBus()
        self._buffer = bytearray()
        self._out: Outbound | None = None
        self._session: CallSession | None = None
        self.frames_in = 0

    async def on_start(self, session: CallSession, out: Outbound) -> None:
        self._session, self._out = session, out
        if self.sink is not None:
            self.sink.write_call(session.call_id, _call_start_doc(session), merge=session.resume)
        self.bus.publish(session.call_id, "start", stream_sid=session.stream_sid, resume=session.resume)
        await play_greeting(session, out, self.greeting)
        self.bus.publish(session.call_id, "greeting", played=bool(self.greeting))

    async def on_media(self, mulaw: bytes) -> None:
        self.frames_in += 1
        self._buffer.extend(mulaw)
        if self._session and self.frames_in % 50 == 0:
            self.bus.publish(self._session.call_id, "media", frames_in=self.frames_in)
        if self._out and len(self._buffer) >= self.buffer_frames * FRAME_BYTES_8K:
            chunk, self._buffer = bytes(self._buffer), bytearray()
            await self._out.audio(chunk, mark=f"echo-{self._out.sent_frames}")

    async def on_mark(self, name: str) -> None:
        if self._out:
            self._out.mark_played(name)
        if self._session:
            self.bus.publish(self._session.call_id, "mark", name=name)

    async def on_stop(self) -> None:
        if self._out and self._buffer:
            chunk, self._buffer = bytes(self._buffer), bytearray()
            await self._out.audio(chunk, mark="echo-final")
        if self._session:
            self.bus.publish(self._session.call_id, "stop", frames_in=self.frames_in)


def _call_start_doc(session: CallSession) -> dict:
    from contracts.voice import CallStart

    return CallStart(call_id=session.call_id, business_id=session.business_id, from_number=session.from_number,
                     session_type="sim" if session.call_id.startswith("CAsim") else "phone",
                     started_at=session.started_at, disclosure_done=session.disclosure_done,
                     resume_count=session.resume_count).to_doc()


PipelineFactory = Callable[[CallSession], MediaHandler]


@dataclass
class PipelineDeps:
    sink: CallSink
    bus: HudBus
    engine: TurnEngine
    greeting: bytes | None = None
    providers: Any = None  # services.voice.providers.AudioProviders; None → echo pipeline (Phase 1 behaviour)
    tz: str = "Australia/Melbourne"
    business_id: str = ""
    cutover_enabled: bool = True
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc)
    side_effects: Any = None  # services.booking.side_effects.SideEffectSwitch, local sink only


def build_pipeline_factory(deps: PipelineDeps) -> PipelineFactory:
    def factory(session: CallSession) -> MediaHandler:
        if deps.providers is None:
            return EchoPipeline(deps.greeting, sink=deps.sink, bus=deps.bus)
        from .call_pipeline import CallPipelineConfig, ReceptionistPipeline

        return ReceptionistPipeline(deps, deps.providers,
                                    CallPipelineConfig(tz=deps.tz, business_id=deps.business_id,
                                                       cutover_enabled=deps.cutover_enabled), clock=deps.clock)

    return factory


def default_pipeline_factory(session: CallSession) -> MediaHandler:
    return EchoPipeline(load_greeting())
