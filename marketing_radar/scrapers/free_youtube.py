"""YouTube Data API v3 adapter (0 ScrapeCreators credits; 10k units/day, resets midnight Pacific).

Ported from previous_work tools/idea-scout/youtube-discovery.py: search.list (100 units) then
videos.list (1 unit per page of 50).
"""
from __future__ import annotations

import datetime as dt
import uuid

from ..clock import Clock, pacific_date
from ..config import Settings
from ..db import RadarStore
from ..packets import TrendPacket, UsageEvent
from ..scoring.normalize import normalize_response
from .transport import HttpTransport

API_BASE = "https://www.googleapis.com/youtube/v3"
SEARCH_COST = 100
VIDEOS_COST = 1


class FreeYouTube:
    def __init__(self, store: RadarStore, transport: HttpTransport, settings: Settings, clock: Clock) -> None:
        self.store = store
        self.transport = transport
        self.settings = settings
        self.clock = clock

    @property
    def enabled(self) -> bool:
        return bool(self.settings.youtube_api_key)

    def units_used_today(self) -> int:
        today = pacific_date(self.clock())
        rows = self.store.list(self.store.paths.usage_events,
                               where=[("provider", "==", "youtube_data_api"), ("pacific_date", "==", today)])
        return sum(int(data.get("units") or 0) for _, data in rows)

    def remaining(self) -> int:
        return max(0, self.settings.youtube_daily_quota - self.units_used_today())

    def search(self, queries: list[str], *, max_results: int = 25, days_back: int = 7,
               max_duration_sec: int | None = 60, source: str = "youtube_data_api") -> list[TrendPacket]:
        if not self.enabled or not queries:
            return []
        now = self.clock()
        published_after = (now - dt.timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        video_ids: dict[str, str] = {}
        for query in queries:
            if self.remaining() < SEARCH_COST + VIDEOS_COST:
                break
            response = self.transport.request("GET", f"{API_BASE}/search", params={
                "part": "id", "type": "video", "videoDuration": "short", "order": "viewCount",
                "maxResults": str(min(max_results, 50)), "publishedAfter": published_after,
                "q": query, "key": self.settings.youtube_api_key,
            }, timeout=self.settings.http_timeout_seconds)
            self._record(SEARCH_COST, "search", now)
            if not response.ok or not isinstance(response.body, dict):
                continue
            for item in response.body.get("items", []):
                vid = (item.get("id") or {}).get("videoId")
                if vid:
                    video_ids.setdefault(vid, query)
        packets: list[TrendPacket] = []
        ids = list(video_ids)
        for start in range(0, len(ids), 50):
            if self.remaining() < VIDEOS_COST:
                break
            response = self.transport.request("GET", f"{API_BASE}/videos", params={
                "part": "snippet,statistics,contentDetails", "id": ",".join(ids[start:start + 50]),
                "key": self.settings.youtube_api_key,
            }, timeout=self.settings.http_timeout_seconds)
            self._record(VIDEOS_COST, "videos", now)
            if not response.ok:
                continue
            packets.extend(normalize_response("youtube", response.body, source=source, scraped_at=now))
        if max_duration_sec is not None:
            packets = [p for p in packets if p.duration_sec is None or p.duration_sec <= max_duration_sec]
        return packets

    def _record(self, units: int, purpose: str, now: dt.datetime) -> None:
        event = UsageEvent(event_id=f"yt_{now.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}",
                           provider="youtube_data_api", purpose=purpose, at=now, pacific_date=pacific_date(now),
                           units=units)
        self.store.set(self.store.paths.usage_event(event.event_id), event.to_doc())
