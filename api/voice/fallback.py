"""POST /api/voice/fallback → apologise and hang up when the primary handler fails (§6.1)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response

from services.voice.templates import FALLBACK_APOLOGY
from services.voice.twiml import fallback_twiml

log = logging.getLogger(__name__)
FALLBACK_PATH = "/api/voice/fallback"


def build_router() -> APIRouter:
    router = APIRouter()

    @router.post(FALLBACK_PATH)
    async def fallback(request: Request) -> Response:
        form = await request.form()
        log.error("primary handler failed — fallback TwiML served",
                  extra={"call_id": str(form.get("CallSid", "")), "error_code": str(form.get("ErrorCode", ""))})
        return Response(content=fallback_twiml(FALLBACK_APOLOGY), media_type="text/xml")

    return router
