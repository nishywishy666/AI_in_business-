import base64

import numpy as np
import pytest

from services.voice import audio

audioop = pytest.importorskip("audioop", reason="audioop cross-check needs Python < 3.13")


def test_decode_table_matches_reference_for_all_256_bytes():
    ours = audio.mulaw_to_pcm16(bytes(range(256)))
    reference = np.frombuffer(audioop.ulaw2lin(bytes(range(256)), 2), dtype=np.int16)
    assert np.array_equal(ours, reference)


def test_encoder_matches_reference_across_full_int16_range():
    samples = np.arange(-32768, 32768, 7, dtype=np.int16)
    ours = audio.pcm16_to_mulaw(samples)
    reference = audioop.lin2ulaw(samples.tobytes(), 2)
    assert ours == reference


def test_round_trip_8k_16k_8k_keeps_length_and_shape():
    t = np.arange(0, audio.TWILIO_RATE) / audio.TWILIO_RATE  # 1 s
    sine8k = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)
    fixture = audio.pcm16_to_mulaw(sine8k)
    assert len(fixture) == audio.TWILIO_RATE

    pcm8 = audio.mulaw_to_pcm16(fixture)
    pcm16 = audio.resample(pcm8, audio.TWILIO_RATE, audio.MODEL_RATE)
    assert len(pcm16) == 2 * len(pcm8) and pcm16.dtype == np.int16
    back = audio.resample(pcm16, audio.MODEL_RATE, audio.TWILIO_RATE)
    assert len(back) == len(pcm8)
    corr = np.corrcoef(back[200:-200].astype(float), pcm8[200:-200].astype(float))[0, 1]
    assert corr > 0.99
    assert len(audio.pcm16_to_mulaw(back)) == len(fixture)


def test_inbound_frame_decodes_to_16k_and_outbound_encodes_to_8k():
    frame = b"\xff" * audio.FRAME_BYTES_8K
    pcm16 = audio.decode_inbound_frame(base64.b64encode(frame).decode())
    assert len(pcm16) == 320 and np.abs(pcm16).max() <= 8
    assert len(audio.encode_outbound_pcm(pcm16)) == audio.FRAME_BYTES_8K


def test_frames_chunk_at_20ms():
    data = bytes(range(256)) * 3  # 768 bytes
    chunks = audio.frames(data)
    assert [len(c) for c in chunks] == [160, 160, 160, 160, 128]
    assert b"".join(chunks) == data
    assert len(audio.silence_mulaw(20)) == audio.FRAME_BYTES_8K
