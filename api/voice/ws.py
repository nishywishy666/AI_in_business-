"""WSS /api/voice/ws → the media stream loop (vr_plan.md §4, §6.3–§6.5).

Contains no simulator-specific branch (R7): a browser client that sends the same envelopes with a
valid token is indistinguishable from Twilio.
"""
from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.common.config import VoiceConfig
from services.common.logging import call_logger
from services.voice.pipeline import Outbound, PipelineFactory, default_pipeline_factory
from services.voice.session import CallSession
from services.voice.signature import UsedTokenCache, verify_ws_token
from services.voice.stream import Connected, Dtmf, Mark, Media, Start, Stop, StreamProtocolError, parse_event
from services.voice.twiml import WS_PATH

log = logging.getLogger(__name__)
POLICY_VIOLATION = 1008


def build_router(config: VoiceConfig, pipeline_factory: PipelineFactory | None = None,
                 used_tokens: UsedTokenCache | None = None) -> APIRouter:
    router = APIRouter()
    factory = pipeline_factory or default_pipeline_factory
    tokens = used_tokens or UsedTokenCache()

    @router.websocket(WS_PATH)
    async def media_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        session: CallSession | None = None
        handler = None
        out: Outbound | None = None
        clog = call_logger(__name__, "unknown")
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    event = parse_event(raw)
                except (StreamProtocolError, ValueError) as exc:
                    clog.warning("bad stream message", extra={"error": str(exc)})
                    continue

                if isinstance(event, Connected):
                    continue

                if isinstance(event, Start):
                    params = event.custom_parameters
                    token = params.get("token")
                    param_sid = params.get("callSid")
                    if (param_sid != event.call_sid
                            or not verify_ws_token(config.ws_token_secret, token, event.call_sid)
                            or not tokens.consume(token or "")):
                        log.warning("ws start rejected: bad or replayed token",
                                    extra={"call_id": event.call_sid, "stream_sid": event.stream_sid})
                        await websocket.close(code=POLICY_VIOLATION)
                        return
                    session = CallSession(
                        call_id=event.call_sid, stream_sid=event.stream_sid,
                        business_id=params.get("businessId") or config.business_id,
                        from_number=params.get("from"), resume=params.get("resume") == "1",
                    )
                    clog = call_logger(__name__, session.call_id)
                    out = Outbound(websocket.send_text, event.stream_sid, session, close=websocket.close)
                    handler = factory(session)
                    clog.info("stream start", extra={"stream_sid": event.stream_sid, "resume": session.resume,
                                                     "media_format": event.media_format})
                    await handler.on_start(session, out)
                    continue

                if session is None or handler is None:
                    log.warning("message before start; closing", extra={"call_id": "unknown"})
                    await websocket.close(code=POLICY_VIOLATION)
                    return

                if isinstance(event, Media):
                    if event.track != "inbound":
                        continue
                    await handler.on_media(base64.b64decode(event.payload_b64))
                elif isinstance(event, Mark):
                    await handler.on_mark(event.name)
                elif isinstance(event, Dtmf):
                    clog.info("dtmf", extra={"digit": event.digit})
                elif isinstance(event, Stop):
                    clog.info("stream stop")
                    await handler.on_stop()
                    break
        except WebSocketDisconnect:
            clog.info("websocket disconnected")
            if handler is not None:
                await handler.on_stop()
        except RuntimeError as exc:
            # A server-side close (cutover / end of call) makes the next receive raise; that is expected.
            if "close" not in str(exc).lower():
                clog.exception("media stream loop failed")
            elif handler is not None:
                await handler.on_stop()
        except Exception:
            clog.exception("media stream loop failed")
        finally:
            if out is None or not out.closed:
                try:
                    await websocket.close()
                except Exception:
                    pass

    return router
