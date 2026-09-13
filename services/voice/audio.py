"""μ-law ⇄ PCM16, 8 kHz ⇄ 16 kHz, framing (vr_plan.md §6.4).

Implemented with a 256-entry numpy lookup table and scipy.signal.resample_poly so it behaves
identically on Python 3.11 (repo venv), 3.12 (Vercel) and 3.13+ (where `audioop` is gone).
The encoder/decoder follow the Sun/ITU G.711 reference that CPython's audioop used, so
tests/test_audio.py can cross-check against audioop where it still exists.
"""
from __future__ import annotations

import base64
import math

import numpy as np
from scipy.signal import resample_poly

TWILIO_RATE = 8000
MODEL_RATE = 16000
FRAME_MS = 20
FRAME_BYTES_8K = TWILIO_RATE * FRAME_MS // 1000  # 160 μ-law bytes per 20 ms
_BIAS = 0x84
_CLIP = 8159
_SEG_END = np.array([0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF], dtype=np.int32)


def _build_decode_table() -> np.ndarray:
    table = np.zeros(256, dtype=np.int16)
    for byte in range(256):
        u = ~byte & 0xFF
        t = ((u & 0x0F) << 3) + _BIAS
        t <<= (u & 0x70) >> 4
        table[byte] = (_BIAS - t) if (u & 0x80) else (t - _BIAS)
    return table


MULAW_DECODE_TABLE: np.ndarray = _build_decode_table()


def mulaw_to_pcm16(data: bytes) -> np.ndarray:
    """μ-law bytes → int16 PCM samples at the same rate."""
    return MULAW_DECODE_TABLE[np.frombuffer(data, dtype=np.uint8)]


def pcm16_to_mulaw(samples: np.ndarray) -> bytes:
    """int16 PCM → μ-law bytes (Sun 14-bit reference encoder, vectorised)."""
    pcm = (np.asarray(samples, dtype=np.int32) >> 2)
    mask = np.where(pcm < 0, 0x7F, 0xFF).astype(np.int32)
    pcm = np.abs(pcm)
    pcm = np.minimum(pcm, _CLIP) + (_BIAS >> 2)  # bias is applied in the 14-bit domain
    seg = np.searchsorted(_SEG_END, pcm, side="left")
    out = np.where(
        seg >= 8,
        0x7F ^ mask,
        ((seg << 4) | ((pcm >> (seg + 1)) & 0x0F)) ^ mask,
    )
    return out.astype(np.uint8).tobytes()


def resample(samples: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """int16 → int16 polyphase resample. Exact when from/to share a small gcd (8k↔16k = 1:2)."""
    if from_rate == to_rate:
        return np.asarray(samples, dtype=np.int16)
    g = math.gcd(from_rate, to_rate)
    up, down = to_rate // g, from_rate // g
    out = resample_poly(np.asarray(samples, dtype=np.float32), up, down)
    return np.clip(np.rint(out), -32768, 32767).astype(np.int16)


def decode_inbound_frame(payload_b64: str) -> np.ndarray:
    """Twilio media.payload (base64 μ-law 8 kHz) → int16 PCM at MODEL_RATE (16 kHz)."""
    pcm8 = mulaw_to_pcm16(base64.b64decode(payload_b64))
    return resample(pcm8, TWILIO_RATE, MODEL_RATE)


def encode_outbound_pcm(samples_16k: np.ndarray) -> bytes:
    """int16 PCM at 16 kHz → μ-law 8 kHz bytes ready for Twilio. Only needed for locally
    generated audio; ElevenLabs already returns ulaw_8000."""
    return pcm16_to_mulaw(resample(samples_16k, MODEL_RATE, TWILIO_RATE))


def frames(mulaw: bytes, frame_bytes: int = FRAME_BYTES_8K) -> list[bytes]:
    """Chunk μ-law bytes at ~20 ms so Twilio can clear buffered audio on barge-in (§6.4)."""
    return [mulaw[i:i + frame_bytes] for i in range(0, len(mulaw), frame_bytes)]


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def pcm16_bytes(samples: np.ndarray) -> bytes:
    return np.asarray(samples, dtype=np.int16).tobytes()


def silence_mulaw(ms: int) -> bytes:
    return b"\xff" * (TWILIO_RATE * ms // 1000)
