import pytest

from services.voice.signature import (
    UsedTokenCache,
    compute_twilio_signature,
    mint_ws_token,
    public_url,
    validate_twilio_signature,
    verify_ws_token,
)

TOKEN = "twilio-auth-token-for-tests"
PARAMS = {"CallSid": "CA123", "From": "+61400111222", "To": "+61400000000", "CallStatus": "ringing"}


def test_public_url_is_built_from_env_not_request():
    assert public_url("https://abc.trycloudflare.com/", "/api/voice/incoming") == "https://abc.trycloudflare.com/api/voice/incoming"
    assert public_url("https://abc.trycloudflare.com", "api/voice/incoming", "resume=1") == "https://abc.trycloudflare.com/api/voice/incoming?resume=1"


def test_signature_validates_against_public_url_and_fails_on_internal_host():
    url = public_url("https://voice.example.test", "/api/voice/incoming", "resume=1")
    signature = compute_twilio_signature(TOKEN, url, PARAMS)
    assert validate_twilio_signature(TOKEN, url, PARAMS, signature)
    # What request.url would give behind a tunnel/proxy: wrong scheme + internal host → must fail.
    assert not validate_twilio_signature(TOKEN, "http://localhost:8000/api/voice/incoming?resume=1", PARAMS, signature)
    # Query string is part of the signed URL.
    assert not validate_twilio_signature(TOKEN, public_url("https://voice.example.test", "/api/voice/incoming"), PARAMS, signature)
    assert not validate_twilio_signature(TOKEN, url, {**PARAMS, "From": "+61499999999"}, signature)
    assert not validate_twilio_signature(TOKEN, url, PARAMS, None)
    assert not validate_twilio_signature("", url, PARAMS, signature)


def test_ws_token_lifecycle():
    secret = "s3cret"
    now = 1_800_000_000.0
    token = mint_ws_token(secret, "CA123", now=now, ttl_s=60)
    assert verify_ws_token(secret, token, "CA123", now=now + 30)
    assert not verify_ws_token(secret, token, "CA123", now=now + 61), "expired after the 60 s TTL"
    assert not verify_ws_token(secret, token, "CA999", now=now), "CallSid must match"
    assert not verify_ws_token("other", token, "CA123", now=now), "wrong secret"
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert not verify_ws_token(secret, tampered, "CA123", now=now)
    assert not verify_ws_token(secret, None, "CA123", now=now)
    assert not verify_ws_token(secret, "garbage", "CA123", now=now)


def test_used_token_cache_blocks_replay():
    cache = UsedTokenCache()
    token = mint_ws_token("s", "CA1", now=1_800_000_000.0, ttl_s=60)
    assert cache.consume(token) is True
    assert cache.consume(token) is False


def test_default_ttl_comes_from_thresholds():
    token = mint_ws_token("s", "CA1", now=1_800_000_000.0)
    assert int(token.split(".")[0]) == 1_800_000_000 + 60
