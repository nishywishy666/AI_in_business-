"""Structured JSON logging. Every line carries call_id, plus turn_index where applicable (R6)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from typing import Any

_RESERVED = {"name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module", "exc_info",
             "exc_text", "stack_info", "lineno", "funcName", "created", "msecs", "relativeCreated", "thread",
             "threadName", "processName", "process", "message", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": dt.datetime.fromtimestamp(record.created, tz=dt.timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(level)


class CallLogger(logging.LoggerAdapter):
    """Binds call_id (and optionally turn_index) to every line."""

    def __init__(self, logger: logging.Logger, call_id: str, turn_index: int | None = None) -> None:
        super().__init__(logger, {"call_id": call_id, "turn_index": turn_index})

    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}) or {})
        if extra.get("turn_index") is None:
            extra.pop("turn_index", None)
        kwargs["extra"] = extra
        return msg, kwargs

    def for_turn(self, turn_index: int) -> "CallLogger":
        return CallLogger(self.logger, self.extra["call_id"], turn_index)


def call_logger(name: str, call_id: str, turn_index: int | None = None) -> CallLogger:
    return CallLogger(logging.getLogger(name), call_id, turn_index)
