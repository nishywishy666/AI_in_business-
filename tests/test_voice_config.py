import base64
import json
import logging

import pytest

from config import threshold, thresholds
from services.common.config import MissingConfig, decode_sa_json, load_config
from services.common.firestore import BusinessPaths
from services.common.logging import JsonFormatter, call_logger, configure_logging
from tests.conftest import VOICE_ENV


def test_missing_required_var_raises_with_names():
    env = {k: v for k, v in VOICE_ENV.items() if k not in ("TWILIO_AUTH_TOKEN", "GROQ_API_KEY")}
    with pytest.raises(MissingConfig) as exc:
        load_config(env)
    assert "TWILIO_AUTH_TOKEN" in str(exc.value) and "GROQ_API_KEY" in str(exc.value)


def test_provider_conditionals_and_defaults():
    config = load_config(VOICE_ENV)
    assert config.groq_router_model == "openai/gpt-oss-20b" and config.elevenlabs_tts_model == "eleven_flash_v2_5"
    assert config.tz_business == "Australia/Melbourne" and config.session_sink == "local" and not config.enable_sim
    with pytest.raises(MissingConfig):
        load_config({**VOICE_ENV, "EMAIL_PROVIDER": "resend"})
    assert load_config({**VOICE_ENV, "EMAIL_PROVIDER": "resend", "RESEND_API_KEY": "re"}).email_provider == "resend"
    with pytest.raises(MissingConfig):
        load_config({**VOICE_ENV, "SESSION_SINK": "prod"})


def test_service_account_json_accepts_base64_or_raw():
    raw = {"type": "service_account", "project_id": "p"}
    assert decode_sa_json(json.dumps(raw)) == raw
    assert decode_sa_json(base64.b64encode(json.dumps(raw).encode()).decode()) == raw


def test_every_spec_threshold_is_named():
    expected = {"ROUTER_MIN_CONFIDENCE": 0.6, "EMAIL_CONFIDENCE_THRESHOLD": 0.80, "MAX_EMAIL_SPELL_ATTEMPTS": 2,
                "MAX_FIELD_ATTEMPTS": 2, "MAX_PARTY": 12, "LARGE_GROUP_THRESHOLD": 10, "MAX_REPLY_WORDS": 40,
                "GEMINI_TIMEOUT_MS": 1200, "GROQ_TIMEOUT_MS": 800, "STREAM_CUTOVER_MS": 280000,
                "CUTOVER_WARN_MS": 240000, "MAX_RESUMES": 3}
    for name, value in expected.items():
        assert threshold(name) == value, name
    assert thresholds()["GEMINI_MODEL_PREFERENCE"][0] == "gemini-3.8-flash"
    assert not any("preview" in m or "pro" in m for m in thresholds()["GEMINI_MODEL_PREFERENCE"])
    with pytest.raises(KeyError):
        threshold("NOT_A_THRESHOLD")


def test_business_paths_follow_spec_tree():
    p = BusinessPaths("biz1")
    assert p.menu_items == "businesses/biz1/menuItems"
    assert p.fact("hours") == "businesses/biz1/facts/hours"
    assert p.turn("CA1", 3) == "businesses/biz1/calls/CA1/turns/3"
    assert p.booking("abc") == "businesses/biz1/bookings/abc"
    assert p.rollup("2026-09-13") == "businesses/biz1/rollups/2026-09-13"


def test_json_logs_carry_call_id_and_turn_index():
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    base = logging.getLogger("voice.test.json")
    base.handlers = [handler]
    base.setLevel(logging.INFO)
    base.propagate = False

    logger = call_logger("voice.test.json", "CA42")
    logger.info("hello", extra={"stage": "router"})
    logger.for_turn(3).warning("turn thing")
    first, second = (json.loads(line) for line in stream.getvalue().strip().splitlines())
    assert first["call_id"] == "CA42" and first["stage"] == "router" and "turn_index" not in first
    assert second["call_id"] == "CA42" and second["turn_index"] == 3 and second["level"] == "WARNING"

    configure_logging()
    assert isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)
