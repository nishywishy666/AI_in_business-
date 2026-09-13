"""Frozen ScrapeCreators allowlist (spec §10.1). Anything not listed here cannot be called."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Platform = Literal["tiktok", "instagram", "youtube", "facebook"]


@dataclass(frozen=True)
class Endpoint:
    key: str
    path: str
    platform: Platform
    kind: Literal["scan", "like"]
    scope: Literal["global", "niche", "like"]
    required_params: tuple[str, ...] = ()


SCAN_ENDPOINTS: dict[str, Endpoint] = {
    e.key: e for e in (
        Endpoint("tiktok_trending", "/v1/tiktok/get-trending-feed", "tiktok", "scan", "global"),
        Endpoint("youtube_shorts_trending", "/v1/youtube/shorts/trending", "youtube", "scan", "global"),
        Endpoint("instagram_reels_trending", "/v1/instagram/reels/trending", "instagram", "scan", "global"),
        Endpoint("tiktok_hashtag", "/v1/tiktok/search/hashtag", "tiktok", "scan", "niche", ("hashtag",)),
        Endpoint("tiktok_keyword", "/v1/tiktok/search/keyword", "tiktok", "scan", "niche", ("query",)),
        Endpoint("youtube_search", "/v1/youtube/search", "youtube", "scan", "niche", ("query",)),
        Endpoint("instagram_hashtag", "/v1/instagram/search/hashtag", "instagram", "scan", "niche", ("hashtag",)),
        Endpoint("facebook_page_reels", "/v1/facebook/profile/reels", "facebook", "scan", "niche", ("url",)),
        Endpoint("facebook_group_posts", "/v1/facebook/group/posts", "facebook", "scan", "niche", ("url",)),
    )
}

LIKE_ENDPOINTS: dict[str, Endpoint] = {
    e.key: e for e in (
        Endpoint("tiktok_transcript", "/v1/tiktok/video/transcript", "tiktok", "like", "like", ("url",)),
        Endpoint("instagram_transcript", "/v2/instagram/media/transcript", "instagram", "like", "like", ("url",)),
        Endpoint("youtube_transcript", "/v1/youtube/video/transcript", "youtube", "like", "like", ("url",)),
        Endpoint("facebook_transcript", "/v1/facebook/post/transcript", "facebook", "like", "like", ("url",)),
    )
}

ALL_ENDPOINTS: dict[str, Endpoint] = {**SCAN_ENDPOINTS, **LIKE_ENDPOINTS}
ALLOWED_PATHS: frozenset[str] = frozenset(e.path for e in ALL_ENDPOINTS.values())

# Only ever called before a scan when no cached credits figure exists (spec §2.6).
CREDIT_BALANCE_PATH = "/v1/account/credit-balance"

# Pagination and the 10-credit TikTok AI transcript fallback are never sent.
FORBIDDEN_PARAMS: frozenset[str] = frozenset({
    "use_ai_as_fallback", "cursor", "page", "max_cursor", "min_cursor", "offset", "next_page", "page_token",
    "continuation", "trim",
})


def endpoint_by_key(key: str) -> Endpoint:
    try:
        return ALL_ENDPOINTS[key]
    except KeyError:
        raise KeyError(f"unknown ScrapeCreators endpoint key: {key}") from None
