"""Twilio Media Streams WebSocket envelopes (vr_plan.md §6.4, §6.5, §12.1).

Inbound: connected, start, media, mark, stop, dtmf. Outbound: media, mark, clear.
Serialisation is deterministic (fixed key order, compact separators) so the browser simulator can
be checked byte-for-byte against a captured real call.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .audio import b64, frames


class StreamProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Connected:
    protocol: str | None = None
    version: str | None = None


@dataclass(frozen=True)
class Start:
    stream_sid: str
    call_sid: str
    account_sid: str | None
    custom_parameters: dict[str, str] = field(default_factory=dict)
    media_format: dict[str, Any] = field(default_factory=dict)
    tracks: tuple[str, ...] = ()


@dataclass(frozen=True)
class Media:
    stream_sid: str
    payload_b64: str
    track: str = "inbound"
    chunk: str | None = None
    timestamp_ms: int | None = None
    sequence_number: str | None = None


@dataclass(frozen=True)
class Mark:
    stream_sid: str
    name: str


@dataclass(frozen=True)
class Stop:
    stream_sid: str
    call_sid: str | None = None


@dataclass(frozen=True)
class Dtmf:
    stream_sid: str
    digit: str


InboundEvent = Connected | Start | Media | Mark | Stop | Dtmf


def parse_event(raw: str | bytes | dict) -> InboundEvent:
    data = raw if isinstance(raw, dict) else json.loads(raw)
    event = data.get("event")
    if event == "connected":
        return Connected(data.get("protocol"), data.get("version"))
    if event == "start":
        start = data.get("start") or {}
        return Start(
            stream_sid=data.get("streamSid") or start.get("streamSid") or "",
            call_sid=start.get("callSid") or "",
            account_sid=start.get("accountSid"),
            custom_parameters={str(k): str(v) for k, v in (start.get("customParameters") or {}).items()},
            media_format=start.get("mediaFormat") or {},
            tracks=tuple(start.get("tracks") or ()),
        )
    if event == "media":
        media = data.get("media") or {}
        ts = media.get("timestamp")
        return Media(
            stream_sid=data.get("streamSid") or "",
            payload_b64=media.get("payload") or "",
            track=media.get("track") or "inbound",
            chunk=media.get("chunk"),
            timestamp_ms=int(ts) if ts not in (None, "") else None,
            sequence_number=data.get("sequenceNumber"),
        )
    if event == "mark":
        return Mark(stream_sid=data.get("streamSid") or "", name=(data.get("mark") or {}).get("name") or "")
    if event == "stop":
        stop = data.get("stop") or {}
        return Stop(stream_sid=data.get("streamSid") or "", call_sid=stop.get("callSid"))
    if event == "dtmf":
        return Dtmf(stream_sid=data.get("streamSid") or "", digit=(data.get("dtmf") or {}).get("digit") or "")
    raise StreamProtocolError(f"unknown Twilio stream event: {event!r}")


def _dumps(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"))


def media_message(stream_sid: str, mulaw: bytes) -> str:
    return _dumps({"event": "media", "streamSid": stream_sid, "media": {"payload": b64(mulaw)}})


def media_messages(stream_sid: str, mulaw: bytes) -> list[str]:
    return [media_message(stream_sid, chunk) for chunk in frames(mulaw)]


def mark_message(stream_sid: str, name: str) -> str:
    return _dumps({"event": "mark", "streamSid": stream_sid, "mark": {"name": name}})


def clear_message(stream_sid: str) -> str:
    return _dumps({"event": "clear", "streamSid": stream_sid})


def inbound_media_message(stream_sid: str, mulaw: bytes, *, sequence_number: int, chunk: int,
                          timestamp_ms: int, track: str = "inbound") -> str:
    """The exact envelope Twilio sends us — used by the browser simulator (§12.1) so the server
    cannot tell it from a real call."""
    return _dumps({
        "event": "media",
        "sequenceNumber": str(sequence_number),
        "streamSid": stream_sid,
        "media": {"track": track, "chunk": str(chunk), "timestamp": str(timestamp_ms), "payload": b64(mulaw)},
    })


def inbound_start_message(stream_sid: str, call_sid: str, custom_parameters: dict[str, str],
                          *, account_sid: str = "ACsim", sequence_number: int = 1) -> str:
    return _dumps({
        "event": "start",
        "sequenceNumber": str(sequence_number),
        "start": {
            "streamSid": stream_sid,
            "accountSid": account_sid,
            "callSid": call_sid,
            "tracks": ["inbound"],
            "customParameters": custom_parameters,
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        },
        "streamSid": stream_sid,
    })


def inbound_connected_message() -> str:
    return _dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"})


def inbound_stop_message(stream_sid: str, call_sid: str, *, sequence_number: int) -> str:
    return _dumps({"event": "stop", "sequenceNumber": str(sequence_number),
                   "stop": {"accountSid": "ACsim", "callSid": call_sid}, "streamSid": stream_sid})
