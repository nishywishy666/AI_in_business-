"""Data contracts (spec §9, §12, §13). All documents carry schema_version = "1.0".

Datetimes are stored as ISO-8601 strings (``model_dump(mode="json")``) so Firestore docs stay
plain JSON and the local cache round-trips without custom encoders.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .. import SCHEMA_VERSION

Platform = Literal["tiktok", "instagram", "youtube", "facebook", "reddit"]
Quality = Literal["high", "medium", "low"]
ProviderStatus = Literal["ok", "low", "exhausted", "unknown"]
Severity = Literal["info", "warning", "critical", "exhausted"]


class RadarModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class ContextProfile(RadarModel):
    user_id: str
    niche: str = ""
    keywords: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=lambda: ["tiktok", "instagram"])
    format: str | None = None
    region: str | None = None
    language: str | None = None
    goal: str | None = None
    facebook_page_urls: list[str] = Field(default_factory=list)
    facebook_group_urls: list[str] = Field(default_factory=list)
    competitor_handles: list[str] = Field(default_factory=list)
    subreddits: list[str] = Field(default_factory=list)
    audience_line: str | None = None
    raw_text: str = ""
    source_path: str = ""
    pulled_at: dt.datetime
    hashtags_expanded_by: str | None = None

    @property
    def seed_terms(self) -> list[str]:
        return [self.niche, *self.keywords, *self.hashtags]


class TrendPacket(RadarModel):
    schema_version: str = SCHEMA_VERSION
    platform: Platform
    post_id: str
    provider_id: str
    url: str | None = None
    author: str | None = None
    published_at: dt.datetime | None = None
    likes: int = 0
    comments: int = 0
    shares: int = 0
    views: int | None = None
    duration_sec: int | None = None
    caption: str = ""
    title: str | None = None
    hook: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    sound: str | None = None
    thumbnail_url: str | None = None
    source: str = ""
    scan_id: str | None = None
    scraped_at: dt.datetime | None = None

    raw_per_hour: float = 0.0
    hours_since_post: float | None = None
    velocity: float = 0.0
    recency: float = 0.0
    niche_fit: float = 0.0
    platform_weight: float = 1.0
    final: float = 0.0

    why_it_works: str | None = None
    format_guess: str | None = None
    liked: bool = False
    # the owner's bookmark. Kept on the post, not derived from a saved script, so saving never
    # depends on the AI being available to write one.
    saved: bool = False
    transcript: str | None = None
    breakdown: str | None = None
    angles: list[str] | None = None
    chosen_angle: str | None = None
    filming_guide: str | None = None
    script_id: str | None = None

    @property
    def is_primary_platform(self) -> bool:
        return self.platform in ("tiktok", "instagram")


class Profile(RadarModel):
    niche: str = ""
    platforms: list[str] = Field(default_factory=list)
    goal: str | None = None


class Credits(RadarModel):
    remaining: int | None = None
    spent_this_scan: int = 0
    reserve: int = 15
    source: str = "scrapecreators"


class BriefRef(RadarModel):
    post_id: str
    packet_ref: str


class FilmThis(RadarModel):
    post_id: str
    why: str = ""


class ScanBrief(RadarModel):
    schema_version: str = SCHEMA_VERSION
    scan_id: str
    generated_at: dt.datetime
    next_scan_at: dt.datetime | None = None
    kind: Literal["paid_scan", "offday_recap"] = "paid_scan"
    profile: Profile = Field(default_factory=Profile)
    credits: Credits = Field(default_factory=Credits)
    usage: dict[str, str] = Field(default_factory=lambda: {"href": "/api/stats"})
    sources_used: list[str] = Field(default_factory=list)
    weekly_take: str | None = None
    patterns: list[str] = Field(default_factory=list)
    ignore: list[str] = Field(default_factory=list)
    global_: list[BriefRef] = Field(default_factory=list, alias="global")
    niche: list[BriefRef] = Field(default_factory=list)
    film_this: FilmThis | None = None
    playbook_delta: list[str] = Field(default_factory=list)
    model_used: str | None = None
    quality: Quality | None = None
    note: str | None = None

    def all_post_ids(self) -> list[str]:
        ids = [ref.post_id for ref in self.global_] + [ref.post_id for ref in self.niche]
        if self.film_this:
            ids.append(self.film_this.post_id)
        return list(dict.fromkeys(ids))


class LatestPointer(RadarModel):
    scan_id: str | None = None
    generated_at: dt.datetime | None = None
    next_scan_at: dt.datetime | None = None
    kind: str | None = None
    scan_index: int = 0
    last_paid_scan_at: dt.datetime | None = None
    last_paid_scan_id: str | None = None


class Script(RadarModel):
    script_id: str
    user_id: str
    post_id: str
    status: Literal["draft", "saved"] = "draft"
    angle: str = ""
    filming_guide: str = ""
    script: str = ""
    caption: str | None = None
    model_used: str | None = None
    quality: Quality | None = None
    created_at: dt.datetime
    saved_at: dt.datetime | None = None
    expires_at: dt.datetime | None = None


class ScrapeCacheEntry(RadarModel):
    request_hash: str
    endpoint: str
    url: str
    params: dict[str, Any] = Field(default_factory=dict)
    fetched_at: dt.datetime
    expires_at: dt.datetime
    status: int = 200
    credits_charged: int = 0
    credits_remaining: int | None = None
    body: Any = None


class UsageEvent(RadarModel):
    event_id: str
    provider: Literal["scrapecreators", "gemini", "youtube_data_api"]
    purpose: str
    at: dt.datetime
    pacific_date: str
    model: str | None = None
    tokens: int | None = None
    credits_charged: int | None = None
    credits_remaining: int | None = None
    units: int | None = None
    endpoint: str | None = None
    cached: bool = False


class GeminiDaily(RadarModel):
    pacific_date: str
    used: dict[str, int] = Field(default_factory=dict)
    exhausted: list[str] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)
    # model id -> ISO timestamp it may be tried again. A 429 off Gemini's per-minute rate limit is a
    # short cooldown, not the day's quota; only a daily-quota 429 goes in `exhausted`.
    cooldown_until: dict[str, str] = Field(default_factory=dict)


class SynthesisCard(RadarModel):
    post_id: str
    why_it_works: str = ""
    format_guess: str = ""


class SynthesisOutput(RadarModel):
    weekly_take: str = ""
    patterns: list[str] = Field(default_factory=list)
    ignore: list[str] = Field(default_factory=list)
    cards: list[SynthesisCard] = Field(default_factory=list)
    film_this: FilmThis | None = None
    playbook_delta: list[str] = Field(default_factory=list)


class ScrapeCreatorsUsage(RadarModel):
    remaining: int | None = None
    spent_last_scan: int = 0
    spent_today: int = 0
    reserve: int = 15
    usable_now: int = 0
    next_scan_estimated_cost: int = 3
    transcripts_affordable: bool = False
    status: ProviderStatus = "unknown"
    resets: bool = False
    resets_at: None = None
    reset_note: str = "Credits never expire. When they hit 0 they stay 0 until you buy or claim more."


class GeminiModelUsage(RadarModel):
    id: str
    label: str
    quality: Quality
    used_today: int = 0
    daily_cap: int = 0
    remaining: int = 0
    status: Literal["ok", "exhausted", "unavailable"] = "ok"


class GeminiUsage(RadarModel):
    tier: str = "free"
    active_model: str | None = None
    active_quality: Quality | None = None
    quality_warning: str | None = None
    resets: bool = True
    resets_at: dt.datetime | None = None
    resets_in: str | None = None
    reset_note: str = "All Gemini free-tier RPD counters reset at midnight Pacific Time."
    models: list[GeminiModelUsage] = Field(default_factory=list)


class YouTubeUsage(RadarModel):
    daily_units_used: int = 0
    daily_quota: int = 10_000
    remaining: int = 10_000
    status: ProviderStatus = "ok"
    resets: bool = True
    resets_at: dt.datetime | None = None
    reset_note: str = "Free quota resets daily at midnight Pacific Time."


class FreeSource(RadarModel):
    id: str
    status: ProviderStatus = "ok"
    note: str = "no credit balance"


class Alert(RadarModel):
    alert_id: str
    provider: str
    severity: Severity
    message: str
    resets: bool = False
    resets_at: dt.datetime | None = None
    created_at: dt.datetime | None = None


class UsageSnapshot(RadarModel):
    schema_version: str = SCHEMA_VERSION
    checked_at: dt.datetime
    stale: bool = False
    scrapecreators: ScrapeCreatorsUsage = Field(default_factory=ScrapeCreatorsUsage)
    gemini: GeminiUsage = Field(default_factory=GeminiUsage)
    youtube_data_api: YouTubeUsage = Field(default_factory=YouTubeUsage)
    free_sources: list[FreeSource] = Field(default_factory=lambda: [
        FreeSource(id="reddit"), FreeSource(id="google_trends"),
    ])
    alerts: list[Alert] = Field(default_factory=list)
