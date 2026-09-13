"""ScrapeCreators client (spec §10.2): allowlist, 48h request cache, credit accounting."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from ..clock import Clock, iso, pacific_date, parse_iso
from ..config import Settings
from ..db import RadarStore
from ..packets import ScrapeCacheEntry, UsageEvent
from .endpoints import ALLOWED_PATHS, CREDIT_BALANCE_PATH, FORBIDDEN_PARAMS, Endpoint, endpoint_by_key
from .transport import HttpResponse, HttpTransport


class ScrapeCreatorsError(RuntimeError):
    def __init__(self, status: int, body: Any, endpoint: str) -> None:
        super().__init__(f"ScrapeCreators {endpoint} -> HTTP {status}")
        self.status = status
        self.body = body
        self.endpoint = endpoint


class NotAllowlisted(PermissionError):
    pass


class ForbiddenParameter(PermissionError):
    pass


class CreditsExhausted(ScrapeCreatorsError):
    pass


@dataclass
class ScrapeResult:
    endpoint: Endpoint
    body: Any
    cached: bool
    credits_charged: int
    credits_remaining: int | None
    request_hash: str


class ScrapeCreatorsClient:
    def __init__(self, store: RadarStore, transport: HttpTransport, settings: Settings, clock: Clock) -> None:
        self.store = store
        self.transport = transport
        self.settings = settings
        self.clock = clock
        self.live_calls_this_run = 0

    # ---- credits ----------------------------------------------------------------------
    def cached_credits_remaining(self) -> int | None:
        meta = self.store.get(self.store.paths.meta) or {}
        value = meta.get("sc_credits_remaining")
        return int(value) if value is not None else None

    def balance(self, *, about_to_scan: bool = False) -> int | None:
        """Credit balance. Only hits the (possibly paid) balance endpoint right before a scan
        when we have no cached figure at all."""
        cached = self.cached_credits_remaining()
        if cached is not None or not about_to_scan:
            return cached
        response = self._get(CREDIT_BALANCE_PATH, {})
        if not response.ok:
            return None
        remaining = _extract_int(response.body, "credits_remaining", "remaining", "balance", "credits")
        if remaining is not None:
            self._remember_credits(remaining, source="balance")
        return remaining

    def _remember_credits(self, remaining: int, *, source: str) -> None:
        self.store.update(self.store.paths.meta, {
            "initialized": True,
            "sc_credits_remaining": int(remaining),
            "sc_credits_updated_at": iso(self.clock()),
            "sc_credits_source": source,
        })

    # ---- fetch ------------------------------------------------------------------------
    def fetch(self, endpoint: Endpoint | str, params: dict[str, Any] | None = None) -> ScrapeResult:
        if isinstance(endpoint, str):
            endpoint = endpoint_by_key(endpoint)
        params = {k: v for k, v in (params or {}).items() if v is not None}
        self._validate(endpoint, params)
        url = self.settings.scrapecreators_base_url + endpoint.path
        request_hash = _request_hash("GET", url, params)
        cache_path = self.store.paths.scrape_cache_entry(request_hash)
        now = self.clock()

        cached = self.store.get(cache_path)
        if cached and (parse_iso(cached.get("expires_at")) or now) > now:
            return ScrapeResult(endpoint, cached.get("body"), True, 0, cached.get("credits_remaining"), request_hash)

        response = self._get(endpoint.path, params)
        if response.status == 402:
            self._remember_credits(0, source="402")
            raise CreditsExhausted(response.status, response.body, endpoint.key)
        if not response.ok:
            raise ScrapeCreatorsError(response.status, response.body, endpoint.key)

        charged = _extract_int(response.body, "credits_charged", "credits_used", "cost")
        if charged is None:
            charged = _header_int(response.headers, "x-credits-charged", "x-credits-used")
        if charged is None:
            charged = 1
        remaining = _extract_int(response.body, "credits_remaining", "remaining_credits")
        if remaining is None:
            remaining = _header_int(response.headers, "x-credits-remaining")
        if remaining is None:
            previous = self.cached_credits_remaining()
            remaining = max(0, previous - charged) if previous is not None else None

        entry = ScrapeCacheEntry(
            request_hash=request_hash, endpoint=endpoint.key, url=url, params=params, fetched_at=now,
            expires_at=now + dt.timedelta(hours=self.settings.scrape_cache_hours), status=response.status,
            credits_charged=charged, credits_remaining=remaining, body=response.body,
        )
        self.store.set(cache_path, entry.to_doc())
        self.live_calls_this_run += 1
        if remaining is not None:
            self._remember_credits(remaining, source="response")
        self._record_event(endpoint, charged, remaining, now)
        return ScrapeResult(endpoint, response.body, False, charged, remaining, request_hash)

    # ---- internals --------------------------------------------------------------------
    def _validate(self, endpoint: Endpoint, params: dict[str, Any]) -> None:
        if endpoint.path not in ALLOWED_PATHS:
            raise NotAllowlisted(endpoint.path)
        bad = FORBIDDEN_PARAMS.intersection(params)
        if bad:
            raise ForbiddenParameter(f"{endpoint.key}: {sorted(bad)}")
        missing = [p for p in endpoint.required_params if not params.get(p)]
        if missing:
            raise ValueError(f"{endpoint.key} requires params {missing}")

    def _get(self, path: str, params: dict[str, Any]) -> HttpResponse:
        if path not in ALLOWED_PATHS and path != CREDIT_BALANCE_PATH:
            raise NotAllowlisted(path)
        url = self.settings.scrapecreators_base_url + path
        headers = {"x-api-key": self.settings.scrapecreators_api_key or "", "accept": "application/json"}
        response = self.transport.request("GET", url, params=params, headers=headers,
                                          timeout=self.settings.http_timeout_seconds)
        if response.status in (502, 503):
            response = self.transport.request("GET", url, params=params, headers=headers,
                                              timeout=self.settings.http_timeout_seconds)
        return response

    def _record_event(self, endpoint: Endpoint, charged: int, remaining: int | None, now: dt.datetime) -> None:
        event = UsageEvent(
            event_id=f"sc_{now.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}",
            provider="scrapecreators", purpose=endpoint.key, at=now, pacific_date=pacific_date(now),
            credits_charged=charged, credits_remaining=remaining, endpoint=endpoint.path,
        )
        self.store.set(self.store.paths.usage_event(event.event_id), event.to_doc())


def _request_hash(method: str, url: str, params: dict[str, Any]) -> str:
    canonical = json.dumps(sorted((str(k), str(v)) for k, v in params.items()))
    return hashlib.sha256(f"{method.upper()} {url} {canonical}".encode()).hexdigest()


def _extract_int(body: Any, *keys: str) -> int | None:
    if not isinstance(body, dict):
        return None
    for key in keys:
        value = body.get(key)
        if value is None and isinstance(body.get("meta"), dict):
            value = body["meta"].get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def _header_int(headers: dict[str, str], *keys: str) -> int | None:
    for key in keys:
        value = headers.get(key) or headers.get(key.lower())
        if value is not None:
            try:
                return int(value)
            except ValueError:
                continue
    return None
