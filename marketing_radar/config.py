from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

Quality = str  # "high" | "medium" | "low"


@dataclass(frozen=True)
class LadderRung:
    ids: tuple[str, ...]
    quality: Quality
    daily_cap: int
    label: str
    note: str | None = None


DEFAULT_LADDER: tuple[LadderRung, ...] = (
    LadderRung(("gemini-3.8-flash", "gemini-3-flash", "gemini-3.5-flash"), "high", 20, "Gemini 3 Flash"),
    LadderRung(("gemini-2.5-flash",), "medium", 1500, "Gemini 2.5 Flash",
               "Older Flash. Quality is lower than Gemini 3."),
    LadderRung(("gemini-2.5-flash-lite", "gemini-flash-lite"), "low", 1000, "Flash-Lite"),
    LadderRung(("gemini-2.0-flash",), "low", 1500, "Gemini 2.0 Flash"),
    LadderRung(("gemini-2.0-flash-lite",), "low", 1500, "Gemini 2.0 Flash-Lite"),
)

SCRAPECREATORS_BASE_URL = "https://api.scrapecreators.com"

# Facebook is the one platform ScrapeCreators cannot search by keyword or trend: organic content only
# comes from a page (`/v1/facebook/profile/reels`) or a group (`/v1/facebook/group/posts`), both keyed
# by URL. When the questionnaire names no Facebook page or group, these public pages stand in so a
# scan still has a Facebook column (plan 0013, owner override of spec §10.3). Override with
# MARKETING_FACEBOOK_FALLBACK_URLS (comma-separated); an empty value disables the fallback.
DEFAULT_FACEBOOK_FALLBACK_PAGES: tuple[str, ...] = ("https://www.facebook.com/buzzfeedtasty",)


# Free tier only (spec §12.1). A configured ladder may never reach for one of these, whatever the
# env says — running out of free quota means stepping down to a weaker free model, never paying.
PAID_MODEL_MARKERS = ("pro", "ultra")


def is_free_model(model_id: str) -> bool:
    parts = model_id.lower().replace("_", "-").split("-")
    return not any(marker in parts for marker in PAID_MODEL_MARKERS)


def parse_ladder_json(raw: str | None) -> tuple[LadderRung, ...]:
    if not raw or not raw.strip():
        return DEFAULT_LADDER
    rows = json.loads(raw)
    rungs = []
    for row in rows:
        ids = [i for i in (row.get("ids") or [row["id"]]) if is_free_model(i)]
        if not ids:
            continue  # the whole rung was paid models
        rungs.append(LadderRung(
            ids=tuple(ids),
            quality=row.get("quality", "low"),
            daily_cap=int(row.get("daily_cap", 0)),
            label=row.get("label") or ids[0],
            note=row.get("note"),
        ))
    if not rungs:
        raise ValueError("GEMINI_LADDER_JSON must contain at least one rung")
    return tuple(rungs)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    firebase_project_id: str | None = None
    firebase_credentials_json: str | None = None
    context_path_template: str = "users/{userId}/context"
    scrapecreators_api_key: str | None = None
    gemini_api_key: str | None = None
    youtube_api_key: str | None = None
    marketing_user_id: str | None = None
    offline: bool = False
    ladder: tuple[LadderRung, ...] = DEFAULT_LADDER
    cache_root: Path | None = None

    reserve: int = 15
    scan_interval_hours: int = 48
    scrape_cache_hours: int = 48
    # One live call per platform, every paid scan (TikTok, Instagram, Facebook, YouTube Shorts) — the
    # owner's override of spec constraint 5's "max 3 live calls" (plan 0013).
    max_live_calls: int = 4
    next_scan_estimated_cost: int = 4
    facebook_fallback_page_urls: tuple[str, ...] = DEFAULT_FACEBOOK_FALLBACK_PAGES
    youtube_daily_quota: int = 10_000
    draft_ttl_days: int = 14
    dedup_window_days: int = 14
    snapshot_stale_hours: int = 26
    scrapecreators_base_url: str = SCRAPECREATORS_BASE_URL
    http_timeout_seconds: float = 30.0
    fixtures_dir: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, load_dotenv: bool = True) -> "Settings":
        if env is None:
            if load_dotenv:
                from dotenv import load_dotenv as _load
                _load()
            env = os.environ
        cache_root = env.get("MARKETING_RADAR_CACHE_DIR")
        fallback_raw = env.get("MARKETING_FACEBOOK_FALLBACK_URLS")
        fallback = (tuple(u.strip() for u in fallback_raw.split(",") if u.strip()) if fallback_raw is not None
                    else DEFAULT_FACEBOOK_FALLBACK_PAGES)
        return cls(
            firebase_project_id=env.get("FIREBASE_PROJECT_ID") or None,
            firebase_credentials_json=env.get("FIREBASE_CREDENTIALS_JSON") or None,
            context_path_template=env.get("CONTEXT_PATH") or "users/{userId}/context",
            scrapecreators_api_key=env.get("SCRAPECREATORS_API_KEY") or None,
            gemini_api_key=env.get("GEMINI_API_KEY") or None,
            youtube_api_key=env.get("YOUTUBE_API_KEY") or None,
            marketing_user_id=env.get("MARKETING_USER_ID") or None,
            offline=_truthy(env.get("MARKETING_RADAR_OFFLINE")),
            ladder=parse_ladder_json(env.get("GEMINI_LADDER_JSON")),
            cache_root=Path(cache_root) if cache_root else None,
            facebook_fallback_page_urls=fallback,
        )

    def context_path(self, user_id: str) -> str:
        return self.context_path_template.replace("{userId}", user_id).replace("{uid}", user_id)

    @property
    def free_youtube_enabled(self) -> bool:
        return bool(self.youtube_api_key)


__all__ = ["Settings", "LadderRung", "DEFAULT_LADDER", "parse_ladder_json", "is_free_model",
           "PAID_MODEL_MARKERS", "DEFAULT_FACEBOOK_FALLBACK_PAGES", "field"]
