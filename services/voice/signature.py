"""X-Twilio-Signature validation (§6.2) and the WebSocket HMAC token (§6.3)."""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Mapping

from twilio.request_validator import RequestValidator

from config import threshold


def public_url(public_base_url: str, path: str, raw_query: str = "") -> str:
    """The exact URL Twilio called. Built from PUBLIC_BASE_URL — never from request.url, which
    behind a tunnel or Vercel's proxy carries the wrong scheme or an internal host."""
    base = public_base_url.rstrip("/")
    url = f"{base}{path if path.startswith('/') else '/' + path}"
    return f"{url}?{raw_query}" if raw_query else url


def validate_twilio_signature(auth_token: str, url: str, form_params: Mapping[str, str],
                              signature: str | None) -> bool:
    if not signature or not auth_token:
        return False
    return bool(RequestValidator(auth_token).validate(url, dict(form_params), signature))


def compute_twilio_signature(auth_token: str, url: str, form_params: Mapping[str, str]) -> str:
    """For tests and the simulator only — what Twilio would have sent."""
    return RequestValidator(auth_token).compute_signature(url, dict(form_params))


# ---- WebSocket token -------------------------------------------------------------------

def _sign(secret: str, call_sid: str, expiry: int) -> str:
    return hmac.new(secret.encode(), f"{call_sid}.{expiry}".encode(), hashlib.sha256).hexdigest()


def mint_ws_token(secret: str, call_sid: str, *, now: float | None = None, ttl_s: int | None = None) -> str:
    ttl = int(threshold("WS_TOKEN_TTL_S")) if ttl_s is None else ttl_s
    expiry = int((now if now is not None else time.time()) + ttl)
    return f"{expiry}.{_sign(secret, call_sid, expiry)}"


def verify_ws_token(secret: str, token: str | None, call_sid: str, *, now: float | None = None) -> bool:
    if not token or not call_sid or "." not in token:
        return False
    expiry_text, _, signature = token.partition(".")
    if not expiry_text.isdigit():
        return False
    expiry = int(expiry_text)
    current = now if now is not None else time.time()
    if expiry < current:
        return False
    return hmac.compare_digest(_sign(secret, call_sid, expiry), signature)


class UsedTokenCache:
    """Tokens already used to open a stream. Instance-pinned (§3), so in-process is enough."""

    def __init__(self) -> None:
        self._used: dict[str, int] = {}

    def consume(self, token: str, expiry_hint: int | None = None) -> bool:
        """Returns False if the token was already used."""
        self._prune()
        if token in self._used:
            return False
        expiry = expiry_hint if expiry_hint is not None else int(token.partition(".")[0] or 0)
        self._used[token] = expiry
        return True

    def _prune(self) -> None:
        cutoff = int(time.time()) - 3600
        for token, expiry in list(self._used.items()):
            if expiry < cutoff:
                del self._used[token]
