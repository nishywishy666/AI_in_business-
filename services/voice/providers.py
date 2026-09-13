"""Audio intelligence providers behind protocols (vr_plan.md §4, V5, §3).

Real implementations load lazily: Silero VAD + Smart Turn v3.2 on onnxruntime from ./models
(never torch), ElevenLabs Scribe for STT and Flash v2.5 (`ulaw_8000`) for TTS. Every protocol has
a deterministic fake so the whole audio path runs in tests without model files or keys.

Not one of §5's files — the media loop in pipeline.py needs somewhere to keep these (plan 0004).
TODO(spec): the spec names the models but not where to fetch the ONNX files; see MODELS_DIR.
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Protocol

import numpy as np

from .audio import MODEL_RATE, pcm16_bytes

log = logging.getLogger(__name__)
MODELS_DIR = Path(__file__).resolve().parents[2] / "models"
SILERO_FILE = MODELS_DIR / "silero_vad.onnx"
SMART_TURN_FILE = MODELS_DIR / "smart-turn-v3.2.onnx"


# ---- VAD -----------------------------------------------------------------------------------------

class VadProvider(Protocol):
    def speech_probability(self, pcm16_16k: np.ndarray) -> float: ...


class EnergyVad:
    """Deterministic RMS gate for tests and as the fallback when the ONNX file is absent."""

    def __init__(self, threshold: float = 0.02) -> None:
        self.threshold = threshold

    def speech_probability(self, pcm16_16k: np.ndarray) -> float:
        if len(pcm16_16k) == 0:
            return 0.0
        rms = float(np.sqrt(np.mean((pcm16_16k.astype(np.float32) / 32768.0) ** 2)))
        return 1.0 if rms >= self.threshold else 0.0


class SileroVad:
    """Silero VAD on onnxruntime, loaded at module import by `load_default_providers()` (§3 cold start)."""

    def __init__(self, path: Path = SILERO_FILE) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
        names = {i.name for i in self.session.get_inputs()}
        self._v5 = "state" in names
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)

    def speech_probability(self, pcm16_16k: np.ndarray) -> float:
        chunk = pcm16_16k.astype(np.float32) / 32768.0
        if len(chunk) < 512:
            chunk = np.pad(chunk, (0, 512 - len(chunk)))
        x = chunk[:512][None, :]
        sr = np.array(MODEL_RATE, dtype=np.int64)
        if self._v5:
            out, self._state = self.session.run(None, {"input": x, "state": self._state, "sr": sr})
        else:
            out, self._h, self._c = self.session.run(None, {"input": x, "sr": sr, "h": self._h, "c": self._c})
        return float(np.asarray(out).reshape(-1)[0])


# ---- end-of-turn ------------------------------------------------------------------------------------

class TurnDetector(Protocol):
    def finished(self, pcm16_16k_window: np.ndarray, silence_ms: int) -> bool: ...


class SilenceTurnDetector:
    """Deterministic fallback: the turn is over after `min_silence_ms` of non-speech."""

    def __init__(self, min_silence_ms: int = 700) -> None:
        self.min_silence_ms = min_silence_ms

    def finished(self, pcm16_16k_window: np.ndarray, silence_ms: int) -> bool:
        return silence_ms >= self.min_silence_ms


class SmartTurnOnnx:
    """Smart Turn v3.2 (8 MB int8 ONNX). Runs only once VAD has seen some silence, on the last ≤ 8 s.
    TODO(spec): the model's exact input features are not stated; this feeds raw 16 kHz float audio and
    falls back to the silence rule if the model rejects the input shape."""

    def __init__(self, path: Path = SMART_TURN_FILE, *, threshold: float = 0.5, min_silence_ms: int = 200,
                 fallback_silence_ms: int = 700) -> None:
        import onnxruntime as ort

        self.session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.threshold, self.min_silence_ms = threshold, min_silence_ms
        self._fallback = SilenceTurnDetector(fallback_silence_ms)

    def finished(self, pcm16_16k_window: np.ndarray, silence_ms: int) -> bool:
        if silence_ms < self.min_silence_ms:
            return False
        try:
            audio = pcm16_16k_window[-8 * MODEL_RATE:].astype(np.float32) / 32768.0
            out = self.session.run(None, {self.input_name: audio[None, :]})
            return float(np.asarray(out[0]).reshape(-1)[0]) >= self.threshold
        except Exception as exc:
            log.warning("smart turn inference failed; using silence rule", extra={"error": str(exc)})
            return self._fallback.finished(pcm16_16k_window, silence_ms)


# ---- STT ------------------------------------------------------------------------------------------------

@dataclass
class SttResult:
    text: str
    ms: int
    word_confidences: list[float] | None = None
    seconds: float = 0.0


class SttProvider(Protocol):
    async def transcribe(self, pcm16_16k: np.ndarray, *, on_partial=None) -> SttResult: ...


class FakeStt:
    """Returns scripted transcripts in order (tests / offline)."""

    def __init__(self, transcripts: list[str] | None = None) -> None:
        self.transcripts = list(transcripts or [])
        self.calls: list[int] = []

    async def transcribe(self, pcm16_16k: np.ndarray, *, on_partial=None) -> SttResult:
        self.calls.append(len(pcm16_16k))
        text = self.transcripts.pop(0) if self.transcripts else ""
        if on_partial and text:
            on_partial(text.split()[0])
        return SttResult(text=text, ms=1, seconds=len(pcm16_16k) / MODEL_RATE)


class ElevenLabsStt:
    """ElevenLabs Scribe, one request per caller turn (after end-of-turn) so partials are the
    words as they arrive. TODO(spec)/unverified: the Scribe v2 *realtime* websocket contract was
    not verified offline; this uses the SDK's speech_to_text.convert with the configured model."""

    def __init__(self, api_key: str, model_id: str = "scribe_v2_realtime") -> None:
        self.api_key, self.model_id = api_key, model_id
        self._client = None

    def _get(self):
        if self._client is None:
            from elevenlabs.client import AsyncElevenLabs

            self._client = AsyncElevenLabs(api_key=self.api_key)
        return self._client

    async def transcribe(self, pcm16_16k: np.ndarray, *, on_partial=None) -> SttResult:
        started = time.perf_counter()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(MODEL_RATE)
            wav.writeframes(pcm16_bytes(pcm16_16k))
        buf.seek(0)
        response = await self._get().speech_to_text.convert(file=buf, model_id=self.model_id, language_code="en")
        text = getattr(response, "text", "") or ""
        words = getattr(response, "words", None) or []
        confidences = [float(getattr(w, "confidence", 1.0) or 1.0) for w in words if getattr(w, "type", "word") == "word"] or None
        return SttResult(text=text.strip(), ms=int((time.perf_counter() - started) * 1000),
                         word_confidences=confidences, seconds=len(pcm16_16k) / MODEL_RATE)


# ---- TTS ------------------------------------------------------------------------------------------------

class TtsProvider(Protocol):
    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...  # μ-law 8 kHz chunks


class FakeTts:
    """Deterministic μ-law: 20 ms of silence per word, so tests can count frames."""

    def __init__(self, ms_per_word: int = 20, delay_s: float = 0.0) -> None:
        self.ms_per_word, self.delay_s = ms_per_word, delay_s
        self.spoken: list[str] = []

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        self.spoken.append(text)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        words = max(1, len(text.split()))
        yield b"\xff" * (8 * self.ms_per_word * words)


class ElevenLabsTts:
    def __init__(self, api_key: str, voice_id: str, model_id: str = "eleven_flash_v2_5") -> None:
        self.api_key, self.voice_id, self.model_id = api_key, voice_id, model_id
        self._client = None

    def _get(self):
        if self._client is None:
            from elevenlabs.client import AsyncElevenLabs

            self._client = AsyncElevenLabs(api_key=self.api_key)
        return self._client

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        tts = self._get().text_to_speech
        stream = getattr(tts, "stream", None) or getattr(tts, "convert_as_stream")
        async for chunk in stream(voice_id=self.voice_id, text=text, model_id=self.model_id, output_format="ulaw_8000"):
            if chunk:
                yield chunk


# ---- module-scope load (§3 cold start) -----------------------------------------------------------------

@dataclass
class AudioProviders:
    vad: VadProvider
    turn: TurnDetector
    stt: SttProvider
    tts: TtsProvider


def load_default_providers(*, elevenlabs_api_key: str | None, voice_id: str | None,
                           tts_model: str = "eleven_flash_v2_5") -> AudioProviders:
    vad: VadProvider
    turn: TurnDetector
    if SILERO_FILE.exists():
        vad = SileroVad()
    else:
        log.error("silero_vad.onnx missing under ./models — using energy VAD", extra={"path": str(SILERO_FILE)})
        vad = EnergyVad()
    if SMART_TURN_FILE.exists():
        turn = SmartTurnOnnx()
    else:
        log.error("smart-turn-v3.2.onnx missing under ./models — using silence rule", extra={"path": str(SMART_TURN_FILE)})
        turn = SilenceTurnDetector()
    if elevenlabs_api_key and voice_id:
        return AudioProviders(vad, turn, ElevenLabsStt(elevenlabs_api_key), ElevenLabsTts(elevenlabs_api_key, voice_id, tts_model))
    log.error("ELEVENLABS keys missing — audio path will not transcribe or speak")
    return AudioProviders(vad, turn, FakeStt(), FakeTts())
