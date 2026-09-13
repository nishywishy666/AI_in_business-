"""The production media pipeline (vr_plan.md §4, §6.5, §6.6, §11): VAD → end-of-turn → STT →
TurnEngine → TTS, barge-in with `clear`, the 280 s cutover, and call-end finalisation.

Drives the same `TurnEngine` as the simulator's Mode A (R7). Transport-agnostic: only `Outbound`
touches the socket. Separate from pipeline.py to keep the interfaces small; plan 0004 records it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import numpy as np

from config import threshold
from contracts.voice import TurnLog
from services.booking.capacity import speak_time
from services.common.logging import call_logger

from . import templates
from .audio import MODEL_RATE, mulaw_to_pcm16, resample
from .pipeline import Outbound, PipelineDeps, TurnEngine, play_greeting
from .providers import AudioProviders
from .session import CallSession
from .telemetry import StageTimer, TurnRecorder, record_usage
from .tools import take_callback

log = logging.getLogger(__name__)
_CLAUSE = re.compile(r"(?<=[.!?;:])\s+|(?<=,)\s+(?=\S{4,})")


@dataclass
class CallPipelineConfig:
    tz: str = "Australia/Melbourne"
    business_id: str = ""
    frame_ms: int = 20
    barge_in_guard_ms: int = 150
    min_speech_ms: int = 200
    cutover_enabled: bool = True


@dataclass
class _TurnState:
    speech: list[np.ndarray] = field(default_factory=list)
    speech_ms: int = 0
    silence_ms: int = 0
    speaking: bool = False


class ReceptionistPipeline:
    def __init__(self, deps: PipelineDeps, providers: AudioProviders, config: CallPipelineConfig,
                 clock=lambda: dt.datetime.now(dt.timezone.utc)) -> None:
        self.deps, self.providers, self.config, self.clock = deps, providers, config, clock
        self.engine: TurnEngine = deps.engine
        self._session: CallSession | None = None
        self._out: Outbound | None = None
        self._recorder: TurnRecorder | None = None
        self._turn = _TurnState()
        self._speaking_since: float | None = None
        self._tts_task: asyncio.Task | None = None
        self._turn_task: asyncio.Task | None = None
        self._timers: list[asyncio.Task] = []
        self._finalized = False
        self.cutover_triggered = False
        self.frames_in = 0

    # ---- lifecycle -------------------------------------------------------------------------------
    async def on_start(self, session: CallSession, out: Outbound) -> None:
        self._session, self._out = session, out
        self._recorder = TurnRecorder(self.deps.sink, self.deps.bus, session.call_id)
        clog = call_logger(__name__, session.call_id)
        resumed = False
        if session.resume:
            doc = self.deps.sink.read_call(session.call_id)
            if doc:
                session.restore(doc)
                resumed = True
            session.resume_count += 1
            self.deps.sink.write_call(session.call_id, {"resumeCount": session.resume_count,
                                                        "resumedAt": self.clock().isoformat()}, merge=True)
            clog.info("stream resumed", extra={"resume_count": session.resume_count})
        else:
            from .pipeline import _call_start_doc

            self.deps.sink.write_call(session.call_id, _call_start_doc(session))
        self.deps.bus.publish(session.call_id, "start", stream_sid=session.stream_sid, resume=session.resume)

        if session.resume_count > threshold("MAX_RESUMES"):
            await self._speak(templates.RESUME_LIMIT_GOODBYE, mark="goodbye")
            phone = session.slot_state.phone or session.from_number
            if phone:
                take_callback(self.deps.sink, call_id=session.call_id, business_id=session.business_id,
                              name=session.slot_state.name, phone=phone, question="call dropped after repeated resumes",
                              reason="other", now=self.clock())
            await self.finalize(outcome="callback")
            await out.close()
            return

        await play_greeting(session, out, self.deps.greeting)  # no-op when disclosureDone survived the cutover
        self.deps.bus.publish(session.call_id, "greeting", played=not resumed)
        if resumed:
            line = session.resume_line(speak_time)
            if line:
                next_q = _next_question(session)
                await self._speak(f"{line} {next_q}".strip(), mark="resume")
        if self.config.cutover_enabled:
            self._timers = [asyncio.create_task(self._cutover_warn()), asyncio.create_task(self._cutover())]

    async def on_media(self, mulaw: bytes) -> None:
        if self._session is None or self._out is None:
            return
        self.frames_in += 1
        pcm = resample(mulaw_to_pcm16(mulaw), 8000, MODEL_RATE)
        prob = self.providers.vad.speech_probability(pcm)
        speaking = prob >= 0.5
        loop_ms = self.config.frame_ms

        tts_in_flight = self._tts_task is not None and not self._tts_task.done()
        if speaking and (self._session.agent_speaking or tts_in_flight) and self._guard_elapsed():
            await self._barge_in()

        if speaking:
            self._turn.speech.append(pcm)
            self._turn.speech_ms += loop_ms
            self._turn.silence_ms = 0
            self._turn.speaking = True
        elif self._turn.speaking:
            self._turn.speech.append(pcm)  # keep trailing context for the detector
            self._turn.silence_ms += loop_ms
            window = np.concatenate(self._turn.speech) if self._turn.speech else pcm
            if self._turn.speech_ms >= self.config.min_speech_ms and self.providers.turn.finished(window, self._turn.silence_ms):
                audio = np.concatenate(self._turn.speech)
                self._turn = _TurnState()
                if self._turn_task and not self._turn_task.done():
                    return
                self._turn_task = asyncio.create_task(self._run_turn(audio))

    async def on_mark(self, name: str) -> None:
        if self._out:
            self._out.mark_played(name)
        if self._session:
            self.deps.bus.publish(self._session.call_id, "mark", name=name)
            if name == "end" and self._session.ended and self._out:
                await self.finalize()
                await self._out.close()

    async def on_stop(self) -> None:
        for task in self._timers:
            task.cancel()
        if self._tts_task:
            self._tts_task.cancel()
        if self._turn_task and not self._turn_task.done():
            try:
                await asyncio.wait_for(self._turn_task, 5)
            except Exception:
                pass
        if not self.cutover_triggered:
            await self.finalize()

    # ---- a caller turn -----------------------------------------------------------------------------------
    async def _run_turn(self, audio: np.ndarray) -> None:
        session, out, recorder = self._session, self._out, self._recorder
        assert session and out and recorder
        timer = StageTimer()
        timer.start("stt")
        try:
            stt = await self.providers.stt.transcribe(audio, on_partial=recorder.partial)
        except Exception as exc:
            recorder.warn(f"stt failed: {exc}")
            return
        stt_ms = timer.stop("stt")
        record_usage(self.deps.sink, call_id=session.call_id, provider="elevenlabs_stt", task="transcribe",
                     units=round(stt.seconds, 2), unit_kind="seconds", now=self.clock())
        if not stt.text.strip():
            return
        result = await self.engine.run_turn(session, stt.text, recorder=recorder, stt_ms=stt_ms)
        if result.reply_text:
            self._tts_task = asyncio.create_task(self._speak(result.reply_text, mark="end" if result.end_call else f"turn-{result.turn_index}",
                                                             turn_index=result.turn_index))
            await self._tts_task
        elif result.end_call:
            await self.finalize()
            await out.close()

    async def _speak(self, text: str, *, mark: str, turn_index: int | None = None) -> None:
        session, out = self._session, self._out
        assert session and out
        timer = StageTimer()
        timer.start("tts")
        self._speaking_since = asyncio.get_event_loop().time()
        session.interrupted = False
        clauses = [c for c in _CLAUSE.split(text) if c.strip()] or [text]
        try:
            for i, clause in enumerate(clauses):
                async for chunk in self.providers.tts.synthesize(clause):
                    if session.interrupted:
                        return
                    await out.audio(chunk)
                await out.mark(f"{mark}-c{i}" if i < len(clauses) - 1 else mark)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._recorder.warn(f"tts failed: {exc}") if self._recorder else None
        finally:
            tts_ms = timer.stop("tts")
            record_usage(self.deps.sink, call_id=session.call_id, provider="elevenlabs_tts", task="speak",
                         units=len(text), unit_kind="chars", now=self.clock())
            if turn_index is not None:
                self.deps.bus.publish(session.call_id, "tts", turn_index=turn_index, tts_ms=tts_ms)

    # ---- barge-in (§6.5) ---------------------------------------------------------------------------------
    def _guard_elapsed(self) -> bool:
        if self._speaking_since is None:
            return True
        return (asyncio.get_event_loop().time() - self._speaking_since) * 1000 >= self.config.barge_in_guard_ms

    async def _barge_in(self) -> None:
        session, out = self._session, self._out
        assert session and out
        session.interrupted = True
        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()
        await out.clear()
        session.pending_marks.clear()
        self.deps.bus.publish(session.call_id, "barge_in", turn_index=session.turn_index)
        self.deps.sink.write_turn(session.call_id, session.turn_index,
                                  TurnLog(call_id=session.call_id, turn_index=session.turn_index, speaker="agent",
                                          text_final="", interrupted=True, at=self.clock()).to_doc() | {"interruptedOnly": True})

    # ---- 280 s cutover (§6.6) ------------------------------------------------------------------------------
    async def _cutover_warn(self) -> None:
        await asyncio.sleep(threshold("CUTOVER_WARN_MS") / 1000)
        if self._session and not self._session.ended:
            await self._speak(templates.CUTOVER_WARNING, mark="cutover-warn")

    async def _cutover(self) -> None:
        await asyncio.sleep(threshold("STREAM_CUTOVER_MS") / 1000)
        await self.cutover_now()

    async def cutover_now(self) -> None:
        session, out = self._session, self._out
        if session is None or out is None or session.ended:
            return
        self.cutover_triggered = True
        if self._tts_task and not self._tts_task.done():
            try:
                await asyncio.wait_for(self._tts_task, 3)
            except Exception:
                pass
        self.deps.sink.write_call(session.call_id, session.to_resume_doc(), merge=True)
        self.deps.bus.publish(session.call_id, "cutover", resume_count=session.resume_count)
        call_logger(__name__, session.call_id).info("cutover: state persisted, closing stream")
        await out.close()

    # ---- call end (§11) ------------------------------------------------------------------------------------
    async def finalize(self, *, outcome: str | None = None) -> None:
        session = self._session
        if session is None or self._finalized:
            return
        self._finalized = True
        now = self.clock()
        local = now.astimezone(ZoneInfo(self.config.tz))
        outcome = outcome or _outcome(session)
        duration = session.elapsed_ms(now)
        events = [r for r in _events(self.deps.sink, session.call_id)]
        cost = round(sum(float(e.get("costCents") or 0) for e in events), 4)
        doc = {"endedAt": now.isoformat(), "durationMs": duration, "outcome": outcome, "costCents": cost,
               "hourLocal": local.hour, "weekdayLocal": local.strftime("%a").lower(), "turns": session.turn_index,
               **session.to_resume_doc()}
        self.deps.sink.write_call(session.call_id, doc, merge=True)
        self.deps.sink.increment_rollup(local.date().isoformat(), {
            "calls": 1, f"outcome_{outcome}": 1, "durationMs": duration, "costCents": int(round(cost)),
            "bookings": 1 if session.slot_state.stage == "done" else 0,
        })
        self.deps.bus.publish(session.call_id, "end", outcome=outcome, duration_ms=duration)


def _events(sink, call_id: str) -> list[dict]:
    reader = getattr(sink, "read", None)
    if reader is None:
        return []
    return [r["doc"] for r in reader(call_id) if r.get("kind") == "usageEvent"]


def _outcome(session: CallSession) -> str:
    if session.slot_state.stage == "done":
        return "booked"
    if session.pending_callback is not None:
        return "callback"
    if session.ended:
        return "answered"
    return "abandoned" if session.turn_index == 0 or not session.ended else "answered"


def _next_question(session: CallSession) -> str:
    stage = session.slot_state.stage
    return {"date": templates.ASK_DATE, "time": templates.ASK_TIME, "party_size": templates.ASK_PARTY_SIZE,
            "name": templates.ASK_NAME, "phone": templates.ASK_PHONE, "email": templates.ASK_EMAIL,
            "confirm": "Shall I lock that in?"}.get(stage, "")
