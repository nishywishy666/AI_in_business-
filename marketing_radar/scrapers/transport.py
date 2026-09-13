"""HTTP transport protocol with a real httpx implementation and a fixture-backed fake."""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlparse


@dataclass
class HttpResponse:
    status: int
    body: Any
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class HttpTransport(Protocol):
    def request(self, method: str, url: str, *, params: dict[str, Any] | None = None,
                headers: dict[str, str] | None = None, timeout: float = 30.0) -> HttpResponse: ...


class HttpxTransport:
    def __init__(self) -> None:
        import httpx

        self._client = httpx.Client(follow_redirects=True)

    def request(self, method: str, url: str, *, params: dict[str, Any] | None = None,
                headers: dict[str, str] | None = None, timeout: float = 30.0) -> HttpResponse:
        response = self._client.request(method, url, params=params, headers=headers, timeout=timeout)
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        return HttpResponse(response.status_code, body, {k.lower(): v for k, v in response.headers.items()})


@dataclass
class RecordedCall:
    method: str
    url: str
    params: dict[str, Any]
    headers: dict[str, str]

    @property
    def path(self) -> str:
        return urlparse(self.url).path

    @property
    def host(self) -> str:
        return urlparse(self.url).netloc


Responder = Callable[[RecordedCall], HttpResponse]


class FixtureTransport:
    """Routes requests to JSON fixtures by URL path prefix. Records every call for assertions."""

    def __init__(self, fixtures_dir: Path | None = None) -> None:
        self.fixtures_dir = fixtures_dir
        self._routes: list[tuple[str, Responder]] = []
        self._queued: dict[str, deque[HttpResponse]] = {}
        self.calls: list[RecordedCall] = []

    def route(self, pattern: str, responder: Responder | Path | str | dict | list) -> "FixtureTransport":
        if isinstance(responder, (Path, str)):
            path = Path(responder)
            if not path.is_absolute() and self.fixtures_dir is not None:
                path = self.fixtures_dir / path
            self._routes.append((pattern, _file_responder(path)))
        elif isinstance(responder, (dict, list)):
            payload = responder
            self._routes.append((pattern, lambda call, payload=payload: HttpResponse(200, json.loads(json.dumps(payload)))))
        else:
            self._routes.append((pattern, responder))
        return self

    def fail_next(self, pattern: str, status: int, body: Any = None) -> None:
        self._queued.setdefault(pattern, deque()).append(HttpResponse(status, body if body is not None else {"error": status}))

    def request(self, method: str, url: str, *, params: dict[str, Any] | None = None,
                headers: dict[str, str] | None = None, timeout: float = 30.0) -> HttpResponse:
        call = RecordedCall(method, url, dict(params or {}), dict(headers or {}))
        self.calls.append(call)
        for pattern, queue in self._queued.items():
            if _matches(pattern, call) and queue:
                return queue.popleft()
        for pattern, responder in self._routes:
            if _matches(pattern, call):
                return responder(call)
        return HttpResponse(404, {"error": f"no fixture route for {call.host}{call.path}"})

    def calls_to(self, pattern: str) -> list[RecordedCall]:
        return [call for call in self.calls if _matches(pattern, call)]


def _matches(pattern: str, call: RecordedCall) -> bool:
    if pattern.startswith("http"):
        return call.url.startswith(pattern)
    return call.path.startswith(pattern) or pattern in call.url


def _file_responder(path: Path) -> Responder:
    def respond(call: RecordedCall) -> HttpResponse:
        if not path.exists():
            return HttpResponse(404, {"error": f"fixture missing: {path}"})
        return HttpResponse(200, json.loads(path.read_text(encoding="utf-8")))

    return respond
