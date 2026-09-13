"""Environment variables for the voice receptionist (vr_plan.md §14).

`load_config()` raises on a missing required variable. `api/index.py` calls it at import so a
misconfigured deploy fails at boot, not at 2am on a live call. Library modules call `get_config()`
lazily so unit tests can run without a full environment.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal, Mapping

SessionSink = Literal["firestore", "local", "emulator"]
EmailProvider = Literal["gmail", "resend"]

SERVER_ONLY_SECRETS = ("FIREBASE_SA_JSON", "TWILIO_AUTH_TOKEN", "GROQ_API_KEY", "GEMINI_API_KEY",
                       "ELEVENLABS_API_KEY", "GOOGLE_SA_JSON", "WS_TOKEN_SECRET", "GMAIL_APP_PASSWORD",
                       "RESEND_API_KEY")

_REQUIRED = (
    "PUBLIC_BASE_URL", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "WS_TOKEN_SECRET",
    "GROQ_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "BUSINESS_ID",
    "FIREBASE_PROJECT_ID", "FIREBASE_SA_JSON", "GOOGLE_CALENDAR_ID", "GOOGLE_SA_JSON",
)


class MissingConfig(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceConfig:
    public_base_url: str
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str
    ws_token_secret: str
    groq_api_key: str
    groq_router_model: str
    gemini_api_key: str
    elevenlabs_api_key: str
    elevenlabs_voice_id: str
    elevenlabs_tts_model: str
    business_id: str
    firebase_project_id: str
    firebase_sa_json: str
    firestore_emulator_host: str | None
    google_calendar_id: str
    google_sa_json: str
    email_provider: EmailProvider
    gmail_user: str | None
    gmail_app_password: str | None
    resend_api_key: str | None
    session_sink: SessionSink
    enable_sim: bool
    tz_business: str
    extras: dict[str, str] = field(default_factory=dict)

    @property
    def firebase_service_account(self) -> dict:
        return decode_sa_json(self.firebase_sa_json)

    @property
    def google_service_account(self) -> dict:
        return decode_sa_json(self.google_sa_json)


def decode_sa_json(value: str) -> dict:
    """Service-account JSON arrives base64-encoded (§14); accept raw JSON too for local dev."""
    text = value.strip()
    if text.startswith("{"):
        return json.loads(text)
    return json.loads(base64.b64decode(text).decode("utf-8"))


def load_config(env: Mapping[str, str] | None = None, *, required: tuple[str, ...] = _REQUIRED) -> VoiceConfig:
    env = os.environ if env is None else env
    missing = [name for name in required if not env.get(name)]
    if missing:
        raise MissingConfig(f"missing required environment variables: {', '.join(missing)}")
    provider = (env.get("EMAIL_PROVIDER") or "gmail").lower()
    if provider not in ("gmail", "resend"):
        raise MissingConfig(f"EMAIL_PROVIDER must be gmail or resend, got {provider!r}")
    if provider == "gmail" and required and not (env.get("GMAIL_USER") and env.get("GMAIL_APP_PASSWORD")):
        raise MissingConfig("EMAIL_PROVIDER=gmail requires GMAIL_USER and GMAIL_APP_PASSWORD")
    if provider == "resend" and required and not env.get("RESEND_API_KEY"):
        raise MissingConfig("EMAIL_PROVIDER=resend requires RESEND_API_KEY")
    sink = (env.get("SESSION_SINK") or "firestore").lower()
    if sink not in ("firestore", "local", "emulator"):
        raise MissingConfig(f"SESSION_SINK must be firestore, local or emulator, got {sink!r}")
    return VoiceConfig(
        public_base_url=(env.get("PUBLIC_BASE_URL") or "").rstrip("/"),
        twilio_account_sid=env.get("TWILIO_ACCOUNT_SID") or "",
        twilio_auth_token=env.get("TWILIO_AUTH_TOKEN") or "",
        twilio_phone_number=env.get("TWILIO_PHONE_NUMBER") or "",
        ws_token_secret=env.get("WS_TOKEN_SECRET") or "",
        groq_api_key=env.get("GROQ_API_KEY") or "",
        groq_router_model=env.get("GROQ_ROUTER_MODEL") or "llama-3.1-8b-instant",
        gemini_api_key=env.get("GEMINI_API_KEY") or "",
        elevenlabs_api_key=env.get("ELEVENLABS_API_KEY") or "",
        elevenlabs_voice_id=env.get("ELEVENLABS_VOICE_ID") or "",
        elevenlabs_tts_model=env.get("ELEVENLABS_TTS_MODEL") or "eleven_flash_v2_5",
        business_id=env.get("BUSINESS_ID") or "",
        firebase_project_id=env.get("FIREBASE_PROJECT_ID") or "",
        firebase_sa_json=env.get("FIREBASE_SA_JSON") or "",
        firestore_emulator_host=env.get("FIRESTORE_EMULATOR_HOST") or None,
        google_calendar_id=env.get("GOOGLE_CALENDAR_ID") or "",
        google_sa_json=env.get("GOOGLE_SA_JSON") or "",
        email_provider=provider,  # type: ignore[arg-type]
        gmail_user=env.get("GMAIL_USER") or None,
        gmail_app_password=env.get("GMAIL_APP_PASSWORD") or None,
        resend_api_key=env.get("RESEND_API_KEY") or None,
        session_sink=sink,  # type: ignore[arg-type]
        enable_sim=(env.get("ENABLE_SIM") or "0").strip() == "1",
        tz_business=env.get("TZ_BUSINESS") or "Australia/Melbourne",
    )


# Defaults that make `SESSION_SINK=local` boot with zero keys (dashboard + simulator, plan 0005).
# Nothing here is a secret; the local sink cannot reach Firestore and Twilio never calls a laptop.
_LOCAL_DEFAULTS = {"BUSINESS_ID": "uncle_tony", "PUBLIC_BASE_URL": "http://localhost:8000"}


def local_config(env: Mapping[str, str] | None = None) -> VoiceConfig:
    """`load_config` with no required variables and local defaults. Only meaningful when the sink is
    local; `get_config` routes there so a missing key never blocks the offline dashboard."""
    import secrets

    env = dict(os.environ if env is None else env)
    for key, value in _LOCAL_DEFAULTS.items():
        env.setdefault(key, value)
    env.setdefault("WS_TOKEN_SECRET", secrets.token_hex(16))
    env["SESSION_SINK"] = "local"
    return load_config(env, required=())


@lru_cache(maxsize=1)
def get_config() -> VoiceConfig:
    from dotenv import load_dotenv

    load_dotenv()
    if (os.environ.get("SESSION_SINK") or "firestore").lower() == "local":
        return local_config()
    return load_config()
