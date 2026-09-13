#!/usr/bin/env python3
"""Pre-render the disclosure greeting once, at build time (vr_plan.md §7.4).

    uv run python scripts/render_greeting.py --business-name "Toastie Club"

Writes assets/voice/greeting.ulaw (Twilio-ready μ-law 8 kHz, streamed as-is on every call) and
assets/voice/greeting.wav (for listening). Needs ELEVENLABS_API_KEY + ELEVENLABS_VOICE_ID.
"""
from __future__ import annotations

import argparse
import os
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.disclaimers import GREETING_DISCLOSURE  # noqa: E402
from services.voice.audio import TWILIO_RATE, mulaw_to_pcm16, pcm16_bytes  # noqa: E402
from services.voice.pipeline import ASSETS_DIR, GREETING_FILE  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--business-name", required=True,
                        help="substituted into GREETING_DISCLOSURE. TODO(spec): runtime source of the name")
    parser.add_argument("--out", default=str(GREETING_FILE))
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()
    api_key, voice_id = os.environ.get("ELEVENLABS_API_KEY"), os.environ.get("ELEVENLABS_VOICE_ID")
    if not api_key or not voice_id:
        sys.exit("ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID are required")
    model_id = os.environ.get("ELEVENLABS_TTS_MODEL") or "eleven_flash_v2_5"
    text = GREETING_DISCLOSURE.format(business_name=args.business_name)

    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=api_key)
    chunks = client.text_to_speech.convert(voice_id=voice_id, text=text, model_id=model_id, output_format="ulaw_8000")
    mulaw = b"".join(chunks) if not isinstance(chunks, (bytes, bytearray)) else bytes(chunks)
    if not mulaw:
        sys.exit("ElevenLabs returned no audio")

    out = Path(args.out)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    out.write_bytes(mulaw)
    with wave.open(str(out.with_suffix(".wav")), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(TWILIO_RATE)
        wav.writeframes(pcm16_bytes(mulaw_to_pcm16(mulaw)))
    print(f"wrote {out} ({len(mulaw)} bytes, {len(mulaw) / TWILIO_RATE:.1f}s) and {out.with_suffix('.wav')}")
    print(f"text: {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
