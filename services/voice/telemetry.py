"""Per-stage timings, per-turn write, and the in-process HUD bus (vr_plan.md §4.1, §11, §12.2).

The bus is how the simulator page sees partials, intents and timings without ws.py needing a
simulator branch: the pipeline publishes unconditionally; with no subscriber it is a no-op.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from contracts.voice import TurnLog
from services.common.logging import call_logger

from .sinks import CallSink


class StageTimer:
    """Wall-clock per stage: sttMs, routerMs, answerMs, ttsMs, plus anything else you name."""

    def __init__(self) -> None:
        self._starts: dict[str, float] = {}
        self.ms: dict[str, int] = {}

    def start(self, stage: str) -> None:
        self._starts[stage] = time.perf_counter()

    def stop(self, stage: str) -> int:
        started = self._starts.pop(stage, None)
        elapsed = int((time.perf_counter() - started) * 1000) if started is not None else 0
        self.ms[stage] = self.ms.get(stage, 0) + elapsed
        return elapsed

    def total(self) -> int:
        return sum(self.ms.values())


class HudBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, call_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs[call_id].append(queue)
        return queue

    def unsubscribe(self, call_id: str, queue: asyncio.Queue) -> None:
        try:
            self._subs[call_id].remove(queue)
        except ValueError:
            pass
        if not self._subs[call_id]:
            self._subs.pop(call_id, None)

    def publish(self, call_id: str, event: str, **data: Any) -> None:
        subs = self._subs.get(call_id)
        if not subs:
            return
        payload = {"event": event, "call_id": call_id, "ts": dt.datetime.now(dt.timezone.utc).isoformat(), **data}
        for queue in list(subs):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def has_subscribers(self, call_id: str) -> bool:
        return bool(self._subs.get(call_id))


_PRICING = None


def cost_cents(provider: str, units: float) -> float:
    """From config/pricing.yaml (TODO(spec): rates are 0 until the owner fills them in)."""
    global _PRICING
    if _PRICING is None:
        from config import load_yaml

        _PRICING = (load_yaml("pricing.yaml") or {}).get("rates") or {}
    rate = _PRICING.get(provider) or {}
    return round(float(units) * float(rate.get("cents_per_unit") or 0), 4)


def record_usage(sink: CallSink, *, call_id: str, provider: str, task: str, units: float, unit_kind: str,
                 model: str | None = None, now: dt.datetime | None = None) -> float:
    """usageEvents/{auto} for every provider call (§11). Returns the cost in cents."""
    from contracts.voice import UsageEvent

    cents = cost_cents(provider, units)
    event = UsageEvent(call_id=call_id, provider=provider, task=task, model=model, units=units, unit_kind=unit_kind,
                       cost_cents=cents, at=now or dt.datetime.now(dt.timezone.utc))
    sink.write_usage_event(event.to_doc())
    return cents


@dataclass
class TurnRecorder:
    """Writes one document per final turn (never partials) and mirrors it to the HUD."""

    sink: CallSink
    bus: HudBus
    call_id: str
    warnings: list[str] = field(default_factory=list)

    def record(self, log: TurnLog) -> None:
        doc = log.to_doc()
        self.sink.write_turn(self.call_id, log.turn_index, doc)
        self.bus.publish(self.call_id, "turn", turn=doc)
        call_logger(__name__, self.call_id, log.turn_index).info("turn", extra={"speaker": log.speaker,
                                                                             "intent": log.intent,
                                                                             "answer_source": log.answer_source,
                                                                             "used_fallback": log.used_fallback})

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        self.bus.publish(self.call_id, "warning", message=message)
        call_logger(__name__, self.call_id).warning(message)

    def partial(self, text: str) -> None:
        # Partials go to the HUD and the log stream only — never to Firestore (§11).
        self.bus.publish(self.call_id, "partial", text=text)
