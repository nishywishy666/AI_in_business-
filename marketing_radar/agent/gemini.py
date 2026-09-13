"""Gemini free-tier ladder (spec §12.1). Best rung first, step down once on 429/cap, never Pro."""
from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..clock import Clock, next_pacific_midnight_utc, pacific_date
from ..config import LadderRung, Settings
from ..db import RadarStore
from ..packets import GeminiDaily, UsageEvent

Purpose = str  # expand | synthesize | recap | chat | angles | script


class RateLimited(RuntimeError):
    """429. `daily` distinguishes "today's quota is gone" from the per-minute rate limit, which is
    only a short wait — treating the two the same is what used to burn the whole ladder in seconds."""

    def __init__(self, message: str = "", *, daily: bool = True, retry_after: float = 60.0) -> None:
        """`daily=False` marks a short-window (per-minute) limit: a cooldown, not the day's quota."""
        super().__init__(message)
        self.daily = daily
        self.retry_after = retry_after


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
                raise RateLimited(str(exc), daily=_is_daily_quota(exc), retry_after=_retry_after(exc)) from exc
            if code in (403, 404):
                raise ModelUnavailable(str(exc)) from exc
            raise
        usage = getattr(response, "usage_metadata", None)
        tokens = getattr(usage, "total_token_count", None) if usage else None
        return GeminiRaw(text=response.text or "", tokens=tokens)


def _is_daily_quota(exc: Exception) -> bool:
    """Is this 429 really today's quota, or just the per-minute rate limit?

    Only a body that names a per-day quota (`GenerateRequestsPerDayPerProjectPerModel`,
    "daily limit") retires the model until the Pacific reset. Everything else — including a 429 that
    names no quota at all, which is most of them — is treated as a short wait, because the cost of
    guessing wrong the other way is the whole free ladder going dark for the rest of the day."""
    text = str(exc).lower().replace("_", "").replace(" ", "")
    return any(marker in text for marker in ("perday", "dailylimit", "perdayperproject"))


def _retry_after(exc: Exception) -> float:
    match = re.search(r"retrydelay[\"':\s]+(\d+(?:\.\d+)?)s", str(exc).lower().replace(" ", ""))
    if match:
        return min(300.0, max(5.0, float(match.group(1))))
    return 60.0


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
        at = now or self.clock()
        daily = self.daily(at)
        out: list[RungStatus] = []
        for rung in self.settings.ladder:
            model_id = _pick_model(rung, daily)
            if model_id is None:
                out.append(RungStatus(rung, rung.ids[0], 0, 0, True, unavailable=True))
                continue
            used = daily.used.get(model_id, 0)
            spent = _cooling(daily, model_id, at) or used >= rung.daily_cap
            out.append(RungStatus(rung, model_id, used, max(0, rung.daily_cap - used), spent))
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
                if daily.used.get(model_id, 0) >= rung.daily_cap:
                    break  # this rung's own budget is spent for today; step down once
                if _cooling(daily, model_id, now):
                    break  # rate-limited or out of quota, with time still on the clock; step down
                # A model left in `exhausted` with no cooldown beside it is state from the build that
                # retired models for the whole Pacific day with no way back. Probe it once rather than
                # inherit a dead ladder: if it really is spent, the 429 writes a proper wait this time.
                try:
                    raw = self.transport.generate(model_id, system=system, prompt=prompt, json_mode=json_mode)
                except RateLimited as exc:
                    # a per-minute 429 only parks the model for a moment; only the daily quota retires
                    # it. Either way the wait is written as a cooldown, so nothing is ever blocked
                    # without a recorded reason to be (see the re-probe in the skip rules above).
                    if getattr(exc, "daily", False):
                        daily.exhausted.append(model_id)
                        until = self.resets_at(now)
                    else:
                        until = now + dt.timedelta(seconds=getattr(exc, "retry_after", 60.0))
                    daily.cooldown_until[model_id] = until.isoformat()
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
        raise GeminiExhausted(self._next_free_at(daily, now))

    def _next_free_at(self, daily: GeminiDaily, now: dt.datetime) -> dt.datetime:
        """When something on the ladder can be tried again: the soonest cooldown if every rung is
        merely cooling, otherwise the Pacific reset."""
        reset = self.resets_at(now)
        waits = []
        for value in daily.cooldown_until.values():
            try:
                at = dt.datetime.fromisoformat(value)
            except ValueError:
                continue
            if at > now:
                waits.append(at)
        return min(min(waits), reset) if waits else reset

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


def _cooling(daily: GeminiDaily, model_id: str, now: dt.datetime) -> bool:
    raw = daily.cooldown_until.get(model_id)
    if not raw:
        return False
    try:
        return dt.datetime.fromisoformat(raw) > now
    except ValueError:
        return False


def _pick_model(rung: LadderRung, daily: GeminiDaily) -> str | None:
    for model_id in rung.ids:
        if model_id not in daily.unavailable:
            return model_id
    return None


def rung_statuses_from_daily(settings: Settings, daily: GeminiDaily, *, now: dt.datetime | None = None) -> list[RungStatus]:
    """Same view as GeminiLadder.rung_statuses but computed from a stored counter doc,
    so the usage module never has to import a transport."""
    out: list[RungStatus] = []
    for rung in settings.ladder:
        model_id = _pick_model(rung, daily)
        if model_id is None:
            out.append(RungStatus(rung, rung.ids[0], 0, 0, True, unavailable=True))
            continue
        used = daily.used.get(model_id, 0)
        spent = (_cooling(daily, model_id, now) if now is not None else model_id in daily.exhausted)             or used >= rung.daily_cap
        out.append(RungStatus(rung, model_id, used, max(0, rung.daily_cap - used), spent))
    return out


__all__ = ["GeminiLadder", "GeminiTransport", "GenAiTransport", "FakeGeminiTransport", "GeminiRaw", "GeminiResult",
           "GeminiExhausted", "RateLimited", "ModelUnavailable", "RungStatus", "rung_statuses_from_daily", "field"]
