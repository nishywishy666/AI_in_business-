"""Gemini free-tier ladder (spec §12.1). Best rung first, step down once on 429/cap, never Pro."""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..clock import Clock, next_pacific_midnight_utc, pacific_date
from ..config import LadderRung, Settings
from ..db import RadarStore
from ..packets import GeminiDaily, UsageEvent

Purpose = str  # expand | synthesize | recap | chat | angles | script


class RateLimited(RuntimeError):
    pass


class ModelUnavailable(RuntimeError):
    pass


class GeminiExhausted(RuntimeError):
    def __init__(self, resets_at: dt.datetime) -> None:
        super().__init__(f"Gemini free-tier ladder exhausted until {resets_at.isoformat()}")
        self.resets_at = resets_at


@dataclass
class GeminiRaw:
    text: str
    tokens: int | None = None


class GeminiTransport(Protocol):
    def generate(self, model_id: str, *, system: str, prompt: str, json_mode: bool = True) -> GeminiRaw: ...


class GenAiTransport:
    """google-genai adapter. Imported lazily so offline runs never need the SDK or a key."""

    def __init__(self, api_key: str) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)

    def generate(self, model_id: str, *, system: str, prompt: str, json_mode: bool = True) -> GeminiRaw:
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json" if json_mode else "text/plain",
            temperature=0.7,
        )
        try:
            response = self._client.models.generate_content(model=model_id, contents=prompt, config=config)
        except errors.APIError as exc:
            code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            if code == 429:
                raise RateLimited(str(exc)) from exc
            if code in (403, 404):
                raise ModelUnavailable(str(exc)) from exc
            raise
        usage = getattr(response, "usage_metadata", None)
        tokens = getattr(usage, "total_token_count", None) if usage else None
        return GeminiRaw(text=response.text or "", tokens=tokens)


@dataclass
class FakeCall:
    model_id: str
    system: str
    prompt: str
    json_mode: bool


class FakeGeminiTransport:
    """Scripted transport for tests: per-model queues of str | Exception; a default for the rest."""

    def __init__(self, default: str = "{}", scripts: dict[str, list[Any]] | None = None) -> None:
        self.default = default
        self.scripts: dict[str, list[Any]] = {k: list(v) for k, v in (scripts or {}).items()}
        self.calls: list[FakeCall] = []
        self.responder = None

    def generate(self, model_id: str, *, system: str, prompt: str, json_mode: bool = True) -> GeminiRaw:
        self.calls.append(FakeCall(model_id, system, prompt, json_mode))
        queue = self.scripts.get(model_id)
        if queue:
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return GeminiRaw(text=item, tokens=100)
        if self.responder is not None:
            return GeminiRaw(text=self.responder(prompt), tokens=100)
        return GeminiRaw(text=self.default, tokens=100)


@dataclass
class GeminiResult:
    text: str
    model_used: str
    quality: str
    rung_index: int
    quality_warning: str | None = None
    tokens: int | None = None
    purpose: str = ""


@dataclass
class RungStatus:
    rung: LadderRung
    model_id: str
    used: int
    remaining: int
    exhausted: bool
    unavailable: bool = False

    @property
    def status(self) -> str:
        if self.unavailable:
            return "unavailable"
        return "exhausted" if (self.exhausted or self.remaining <= 0) else "ok"


class GeminiLadder:
    def __init__(self, store: RadarStore, settings: Settings, transport: GeminiTransport, clock: Clock) -> None:
        self.store = store
        self.settings = settings
        self.transport = transport
        self.clock = clock

    # ---- counters ---------------------------------------------------------------------
    def daily(self, now: dt.datetime | None = None) -> GeminiDaily:
        date = pacific_date(now or self.clock())
        doc = self.store.get(self.store.paths.gemini_daily(date))
        return GeminiDaily.model_validate(doc) if doc else GeminiDaily(pacific_date=date)

    def _save_daily(self, daily: GeminiDaily) -> None:
        self.store.set(self.store.paths.gemini_daily(daily.pacific_date), daily.to_doc())

    def rung_statuses(self, now: dt.datetime | None = None) -> list[RungStatus]:
        daily = self.daily(now)
        out: list[RungStatus] = []
        for rung in self.settings.ladder:
            model_id = _pick_model(rung, daily)
            if model_id is None:
                out.append(RungStatus(rung, rung.ids[0], 0, 0, True, unavailable=True))
                continue
            used = daily.used.get(model_id, 0)
            out.append(RungStatus(rung, model_id, used, max(0, rung.daily_cap - used), model_id in daily.exhausted))
        return out

    def active(self, now: dt.datetime | None = None) -> tuple[int, RungStatus] | None:
        for index, status in enumerate(self.rung_statuses(now)):
            if status.status == "ok":
                return index, status
        return None

    def resets_at(self, now: dt.datetime | None = None) -> dt.datetime:
        return next_pacific_midnight_utc(now or self.clock())

    # ---- generate ---------------------------------------------------------------------
    def generate(self, purpose: Purpose, *, system: str, prompt: str, json_mode: bool = True,
                 min_rung: int = 0) -> GeminiResult:
        now = self.clock()
        daily = self.daily(now)
        for index, rung in enumerate(self.settings.ladder):
            if index < min_rung:
                continue
            for model_id in rung.ids:
                if model_id in daily.unavailable:
                    continue
                if model_id in daily.exhausted or daily.used.get(model_id, 0) >= rung.daily_cap:
                    break  # this rung is spent for today; step down once
                try:
                    raw = self.transport.generate(model_id, system=system, prompt=prompt, json_mode=json_mode)
                except RateLimited:
                    daily.exhausted.append(model_id)
                    self._save_daily(daily)
                    break
                except ModelUnavailable:
                    daily.unavailable.append(model_id)
                    self._save_daily(daily)
                    continue
                daily.used[model_id] = daily.used.get(model_id, 0) + 1
                self._save_daily(daily)
                self._record_event(purpose, model_id, raw.tokens, now)
                return GeminiResult(
                    text=raw.text, model_used=model_id, quality=rung.quality, rung_index=index,
                    quality_warning=self.quality_warning(index, rung), tokens=raw.tokens, purpose=purpose,
                )
        raise GeminiExhausted(self.resets_at(now))

    def quality_warning(self, index: int, rung: LadderRung) -> str | None:
        if rung.quality == "high" and index == 0:
            return None
        best = self.settings.ladder[0].label
        note = rung.note or f"Using {rung.label}."
        return f"{note} Generation quality is lower than {best}."

    def _record_event(self, purpose: str, model_id: str, tokens: int | None, now: dt.datetime) -> None:
        event = UsageEvent(event_id=f"gm_{now.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}",
                           provider="gemini", purpose=purpose, at=now, pacific_date=pacific_date(now),
                           model=model_id, tokens=tokens)
        self.store.set(self.store.paths.usage_event(event.event_id), event.to_doc())


def _pick_model(rung: LadderRung, daily: GeminiDaily) -> str | None:
    for model_id in rung.ids:
        if model_id not in daily.unavailable:
            return model_id
    return None


def rung_statuses_from_daily(settings: Settings, daily: GeminiDaily) -> list[RungStatus]:
    """Same view as GeminiLadder.rung_statuses but computed from a stored counter doc,
    so the usage module never has to import a transport."""
    out: list[RungStatus] = []
    for rung in settings.ladder:
        model_id = _pick_model(rung, daily)
        if model_id is None:
            out.append(RungStatus(rung, rung.ids[0], 0, 0, True, unavailable=True))
            continue
        used = daily.used.get(model_id, 0)
        out.append(RungStatus(rung, model_id, used, max(0, rung.daily_cap - used), model_id in daily.exhausted))
    return out


__all__ = ["GeminiLadder", "GeminiTransport", "GenAiTransport", "FakeGeminiTransport", "GeminiRaw", "GeminiResult",
           "GeminiExhausted", "RateLimited", "ModelUnavailable", "RungStatus", "rung_statuses_from_daily", "field"]
