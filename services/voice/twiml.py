"""TwiML builders, one function per response shape (vr_plan.md §6.1)."""
from __future__ import annotations

from twilio.twiml.voice_response import Connect, VoiceResponse

INCOMING_PATH = "/api/voice/incoming"
WS_PATH = "/api/voice/ws"
STATUS_PATH = "/api/voice/status"


def ws_url(public_base_url: str) -> str:
    base = public_base_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return base + WS_PATH


def connect_stream_twiml(public_base_url: str, *, token: str, call_sid: str, business_id: str,
                         resume: bool, from_number: str | None = None) -> str:
    """<Connect><Stream> (bidirectional) + <Redirect> so a server-side close at 280 s re-enters
    the webhook on the same CallSid (§6.6)."""
    response = VoiceResponse()
    connect = Connect()
    stream = connect.stream(url=ws_url(public_base_url), status_callback=f"{public_base_url.rstrip('/')}{STATUS_PATH}")
    stream.parameter(name="token", value=token)
    stream.parameter(name="callSid", value=call_sid)
    stream.parameter(name="businessId", value=business_id)
    stream.parameter(name="resume", value="1" if resume else "0")
    if from_number:
        # Caller ID reaches the stream this way so the booking machine can offer it (§8.2)
        # without an extra Firestore round trip. Not one of §6.1's four parameters — see plan 0004.
        stream.parameter(name="from", value=from_number)
    response.append(connect)
    response.redirect(f"{INCOMING_PATH}?resume=1", method="POST")
    return str(response)


def fallback_twiml(message: str) -> str:
    """PRIMARY HANDLER FAILS → apologise and hang up, never dead air (§6.1)."""
    response = VoiceResponse()
    response.say(message)
    response.hangup()
    return str(response)


def hangup_twiml(message: str | None = None) -> str:
    response = VoiceResponse()
    if message:
        response.say(message)
    response.hangup()
    return str(response)
