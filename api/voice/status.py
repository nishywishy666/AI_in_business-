"""POST /api/voice/status → Twilio stream + call status callbacks (§6.7).

Phase 1: validate, log loudly on stream-error, acknowledge. Phase 5 sets outcome/endedAt.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

import datetime as dt
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Response

from services.common.config import VoiceConfig
from services.voice.signature import public_url, validate_twilio_signature
from services.voice.sinks import CallSink
from services.voice.twiml import STATUS_PATH

log = logging.getLogger(__name__)
StatusHook = Callable[[dict[str, str]], Awaitable[None]]


def apply_call_status(sink: CallSink, params: dict[str, str], *, tz: str,
                      now: dt.datetime | None = None) -> dict | None:
    """§6.7: endedAt/durationMs come from Twilio, and a hangup mid-turn is `abandoned` — not a guess."""
    call_id = params.get("CallSid", "")
    status = params.get("CallStatus")
    if not call_id or status not in ("completed", "busy", "failed", "no-answer", "canceled"):
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    existing = sink.read_call(call_id) or {}
    duration_s = params.get("CallDuration")
    doc: dict = {"twilioStatus": status, "endedAt": now.isoformat()}
    if duration_s and str(duration_s).isdigit():
        doc["durationMs"] = int(duration_s) * 1000
    if not existing.get("outcome") or existing.get("outcome") == "abandoned":
        slot = existing.get("slotState") or {}
        doc["outcome"] = "booked" if slot.get("stage") == "done" else "abandoned"
    local = now.astimezone(ZoneInfo(tz))
    doc.setdefault("hourLocal", local.hour)
    doc.setdefault("weekdayLocal", local.strftime("%a").lower())
    sink.write_call(call_id, doc, merge=True)
    return doc


def build_router(config: VoiceConfig, on_status: StatusHook | None = None, sink: CallSink | None = None) -> APIRouter:
    router = APIRouter()

    @router.post(STATUS_PATH)
    async def status(request: Request) -> Response:
        form = await request.form()
        params = {k: str(v) for k, v in form.items()}
        url = public_url(config.public_base_url, STATUS_PATH, request.url.query)
        if not validate_twilio_signature(config.twilio_auth_token, url, params,
                                         request.headers.get("X-Twilio-Signature")):
            log.warning("rejected unsigned status callback", extra={"call_id": params.get("CallSid", "")})
            return Response(status_code=403, content="forbidden")
        call_id = params.get("CallSid", "")
        event = params.get("StreamEvent") or params.get("CallStatus") or "unknown"
        extra = {"call_id": call_id, "event": event, "stream_sid": params.get("StreamSid")}
        if event == "stream-error":
            log.error("twilio stream-error", extra={**extra, "stream_error": params.get("StreamError")})
        else:
            log.info("twilio status callback", extra=extra)
        if sink is not None and params.get("CallStatus"):
            try:
                apply_call_status(sink, params, tz=config.tz_business)
            except Exception:
                log.exception("status callback write failed", extra={"call_id": call_id})
        if on_status is not None:
            await on_status(params)
        return Response(status_code=204)

    return router
