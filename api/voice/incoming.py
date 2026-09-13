"""POST /api/voice/incoming → TwiML (vr_plan.md §6.1–§6.3). Form-encoded in, text/xml out."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response

from services.common.config import VoiceConfig
from services.voice.signature import mint_ws_token, public_url, validate_twilio_signature
from services.voice.twiml import INCOMING_PATH, connect_stream_twiml

log = logging.getLogger(__name__)


def build_router(config: VoiceConfig) -> APIRouter:
    router = APIRouter()

    @router.post(INCOMING_PATH)
    async def incoming(request: Request) -> Response:
        form = await request.form()
        params = {k: str(v) for k, v in form.items()}
        url = public_url(config.public_base_url, INCOMING_PATH, request.url.query)
        signature = request.headers.get("X-Twilio-Signature")
        call_sid = params.get("CallSid", "")
        if not validate_twilio_signature(config.twilio_auth_token, url, params, signature):
            log.warning("rejected unsigned or mis-signed webhook", extra={"call_id": call_sid, "url": url})
            return Response(status_code=403, content="forbidden")
        resume = request.query_params.get("resume") == "1"
        token = mint_ws_token(config.ws_token_secret, call_sid)
        twiml = connect_stream_twiml(config.public_base_url, token=token, call_sid=call_sid,
                                     business_id=config.business_id, resume=resume,
                                     from_number=params.get("From") or None)
        log.info("incoming call", extra={"call_id": call_sid, "resume": resume, "from": params.get("From"),
                                         "call_status": params.get("CallStatus")})
        return Response(content=twiml, media_type="text/xml")

    return router
