"""Reddit public JSON adapter (0 credits). 1–2 subreddits mapped from context."""
from __future__ import annotations

import re

from ..clock import Clock
from ..config import Settings
from ..packets import ContextProfile, TrendPacket
from ..scoring.normalize import normalize_response
from .transport import HttpTransport

REDDIT_BASE = "https://www.reddit.com"
USER_AGENT = "marketing-radar/0.1 (trend brief; contact: parent dashboard)"


def default_subreddits(context: ContextProfile, limit: int = 2) -> list[str]:
    if context.subreddits:
        return context.subreddits[:limit]
    candidates: list[str] = []
    for term in [*context.keywords, context.niche]:
        compact = re.sub(r"[^a-z0-9]", "", term.lower())
        if 3 <= len(compact) <= 21 and compact not in candidates:
            candidates.append(compact)
    return candidates[:limit]


class FreeReddit:
    def __init__(self, transport: HttpTransport, settings: Settings, clock: Clock) -> None:
        self.transport = transport
        self.settings = settings
        self.clock = clock
        self.last_status: str = "ok"

    def hot(self, subreddits: list[str], *, limit: int = 25, source: str = "reddit") -> list[TrendPacket]:
        packets: list[TrendPacket] = []
        failures = 0
        for sub in subreddits[:2]:
            try:
                response = self.transport.request(
                    "GET", f"{REDDIT_BASE}/r/{sub}/hot.json", params={"limit": str(limit), "raw_json": "1"},
                    headers={"User-Agent": USER_AGENT}, timeout=self.settings.http_timeout_seconds)
            except Exception:
                failures += 1
                continue
            if not response.ok:
                failures += 1
                continue
            packets.extend(normalize_response("reddit", response.body, source=source, scraped_at=self.clock()))
        self.last_status = "unknown" if subreddits and failures == len(subreddits[:2]) else "ok"
        return packets
