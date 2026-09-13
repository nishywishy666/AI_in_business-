"""Marketing agent wiring for the dashboard: build the `ScanDeps` the parent owns, seed the
questionnaire, and turn the latest ScanBrief into the trend cards the Marketing screen binds.

Tenancy decision (plan 0005): `MARKETING_USER_ID` defaults to `BUSINESS_ID`. Offline whenever
`MARKETING_RADAR_OFFLINE=1` or the live keys are missing — then the JSON-file backend under
`.marketing_radar_cache/` plus recorded fixtures stand in and no credit is ever spent.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from marketing_radar.agent.gemini import GeminiExhausted
from marketing_radar.agent.like import PostNotFound, choose_angle, like_post
from marketing_radar.config import Settings
from marketing_radar.deps import build_deps, offline_backend, offline_settings
from marketing_radar.jobs.scan import ScanDeps
from marketing_radar.services import get_brief, get_marketing_summary, get_post, list_scripts, save_script
from marketing_radar.services.api import RadarApi

from .settings import DashboardSettings

log = logging.getLogger(__name__)

PLATFORM_LABELS = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube Shorts", "facebook": "Facebook",
                   "reddit": "Reddit"}
CHAT_GREETING = ("I've read the latest scan. Ask what's trending for you or globally, what to post tomorrow, "
                 "or for captions on a card.")
EMPTY_GREETING = "No scan has run yet. The first trend scan is scheduled; ask me again once it lands."


def is_offline(settings: Settings) -> bool:
    return settings.offline or not (settings.gemini_api_key and settings.scrapecreators_api_key and settings.firebase_project_id)


def context_seed(dash: DashboardSettings) -> dict | None:
    path = dash.dataset_dir / "marketing_context.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


class MarketingHub:
    """Lazy, process-wide holder for the marketing ScanDeps + RadarApi."""

    def __init__(self, business_id: str, dash: DashboardSettings, *, settings: Settings | None = None,
                 deps: ScanDeps | None = None) -> None:
        self.business_id = business_id
        self.dash = dash
        self._settings = settings
        self._deps = deps
        self._injected = (settings, deps)  # what reset() restores, so an injected double survives it
        self._lock = threading.Lock()
        self.error: str | None = None

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = Settings.from_env()
        return self._settings

    @property
    def user_id(self) -> str:
        return self.settings.marketing_user_id or self.business_id

    @property
    def offline(self) -> bool:
        return is_offline(self.settings)

    def deps(self) -> ScanDeps:
        with self._lock:
            if self._deps is None:
                self._deps = self._build()
            return self._deps

    def _build(self) -> ScanDeps:
        settings = self.settings
        seed = context_seed(self.dash)
        if self.offline:
            offline_settings(settings)
            backend = offline_backend(self.user_id, settings, context_seed=seed)
            return build_deps(self.user_id, settings, backend=backend)
        deps = build_deps(self.user_id, settings)
        if seed is not None:
            try:
                if deps.store.read_context() is None:
                    path = settings.context_path(self.user_id)
                    # lesson 0001: the default context path is a collection; seed one document inside it.
                    if len(path.split("/")) % 2 == 1:
                        path = f"{path}/questionnaire"
                    deps.store.backend.set(path, seed)
                    log.info("seeded marketing questionnaire at %s", path)
            except Exception as exc:  # Firestore unreachable: the brief endpoints will report 503
                log.warning("could not seed marketing context: %s", exc)
        return deps

    def api(self) -> RadarApi:
        return RadarApi(self.deps())

    def reset(self) -> None:
        """Drop the memoised settings/deps so the next read rebuilds them. A failed build (bad keys,
        Firestore unreachable) is otherwise cached for the life of the process — "Refresh now" is the
        user's way out of that."""
        with self._lock:
            self._settings, self._deps = self._injected
            self.error = None

    # ---- read side -----------------------------------------------------------------------------------
    def brief(self, *, force: bool = False) -> dict | None:
        deps = self.deps()
        try:
            return get_brief(deps.store, deps.cache, deps.settings, clock=deps.clock, force=force)
        except Exception as exc:
            self.error = str(exc)[:200]
            log.warning("brief unavailable: %s", exc)
            return None

    def summary(self) -> dict | None:
        deps = self.deps()
        try:
            return get_marketing_summary(deps.store, deps.cache, deps.settings, clock=deps.clock)
        except Exception as exc:
            log.warning("marketing summary unavailable: %s", exc)
            return None

    def trends(self, *, force: bool = False) -> dict[str, Any]:
        """UI-shaped cards. `score` is rank-normalised inside each list (top card = 99) because the
        AIOS `final` is a relative velocity, not a percentage. `force=True` (the "Refresh now" button)
        re-reads Firestore instead of the once-a-day local cache."""
        if force:
            self.reset()
        try:
            brief, deps = self.brief(force=force), self.deps()
        except Exception as exc:  # deps could not be built at all: report it, don't 500 the dashboard
            self.error = str(exc)[:200]
            log.warning("marketing deps unavailable: %s", exc)
            brief, deps = None, None
        if brief is None:
            return {"items": [], "likedIds": [], "savedIds": [], "savedCount": 0, "scanId": None, "empty": True,
                    "note": self.error or "No scan yet — the first trend scan is scheduled.", "greeting": EMPTY_GREETING}
        items, liked, saved_ids = [], [], []
        try:
            saved = list_scripts(deps.store, status="saved")
        except Exception:
            saved = []
        saved_posts = {s.get("post_id") for s in saved if s.get("post_id")}
        for niche, refs in ((True, brief.get("niche") or []), (False, brief.get("global") or [])):
            posts = []
            for ref in refs:
                post = get_post(deps.store, ref["post_id"])
                if post:
                    posts.append(post)
            top = max((float(p.get("final") or 0) for p in posts), default=0.0)
            for rank, post in enumerate(posts):
                final = float(post.get("final") or 0)
                # proportional to the list leader; falls back to list order when every final is 0
                score = round(60 + 39 * (final / top)) if top > 0 else max(60, 99 - 6 * rank)
                card = trend_card(post, niche=niche, score=score)
                if brief.get("film_this") and brief["film_this"].get("post_id") == post["post_id"]:
                    card["filmThis"] = brief["film_this"].get("why")
                items.append(card)
                if post.get("liked"):
                    liked.append(post["post_id"])
                if post.get("saved") or post["post_id"] in saved_posts:
                    saved_ids.append(post["post_id"])
        return {"items": items, "likedIds": liked, "savedIds": saved_ids, "savedCount": len(set(saved_ids) | saved_posts),
                "platformsNote": _platforms_note(items, brief), "scanId": brief["scan_id"],
                "generatedAt": brief.get("generated_at"), "nextScanAt": brief.get("next_scan_at"), "kind": brief.get("kind"),
                "weeklyTake": brief.get("weekly_take"), "patterns": brief.get("patterns") or [], "credits": brief.get("credits"),
                "empty": False, "note": brief.get("note"), "greeting": CHAT_GREETING, "offline": self.offline}

    # ---- write side (Like / Save) -------------------------------------------------------------------
    def like(self, post_id: str) -> tuple[int, dict]:
        return self.api().like(post_id)

    def save(self, post_id: str) -> tuple[int, dict]:
        """Save from a card or the modal: mark the post saved, then Like → angle 0 → saved script
        (spec §9.3) as a best effort. The bookmark is recorded first and on its own, because the
        script needs Gemini: when the free ladder is cooling off, the save must still stick rather
        than roll back in the UI."""
        deps = self.deps()
        try:
            post = get_post(deps.store, post_id)
            if post is None:
                return 404, {"error": f"post {post_id} not found"}
            deps.store.update(deps.store.paths.post(post_id), {"saved": True})
        except PostNotFound as exc:
            return 404, {"error": str(exc)}
        except Exception as exc:
            return 503, {"error": "marketing data unavailable", "detail": str(exc)[:200]}
        try:
            if not post.get("angles"):
                like_post(deps, post_id)
            if post.get("script_id"):
                try:
                    script = save_script(deps.store, post["script_id"], clock=deps.clock)
                    return 200, {"post_id": post_id, "saved": True, "script": script.to_doc()}
                except KeyError:
                    pass
            script = choose_angle(deps, post_id, 0)
            saved = save_script(deps.store, script.script_id, clock=deps.clock)
            return 200, {"post_id": post_id, "saved": True, "script": saved.to_doc()}
        except GeminiExhausted as exc:
            return 200, {"post_id": post_id, "saved": True, "script": None,
                         "note": f"Saved. The script will be written once a free model is available again "
                                 f"(after {exc.resets_at.isoformat()})."}
        except Exception as exc:  # the bookmark is already stored; say so instead of failing the save
            log.warning("saved %s but could not write its script: %s", post_id, exc)
            return 200, {"post_id": post_id, "saved": True, "script": None,
                         "note": "Saved. The script could not be written just now."}


def _platforms_note(items: list[dict], brief: dict) -> str | None:
    """Why a scan can come back all-YouTube: TikTok, Instagram and Facebook are ScrapeCreators
    endpoints (paid credits), while YouTube, Reddit and Google Trends are the free sources. With no
    credits left the scan still runs — on the free sources only — so say so rather than look broken."""
    present = {c["platform"] for c in items}
    missing = [label for label in ("TikTok", "Instagram", "Facebook") if label not in present]
    if not missing or not items:
        return None
    credits = brief.get("credits") if isinstance(brief.get("credits"), dict) else {}
    remaining = credits.get("remaining")
    tail = (f"{remaining} ScrapeCreators credit{'s' if remaining != 1 else ''} left"
            if isinstance(remaining, int) else "they need ScrapeCreators credits")
    return f"No {', '.join(missing)} posts in this scan — {tail}."


def trend_card(post: dict, *, niche: bool, score: int) -> dict:
    platform = PLATFORM_LABELS.get(str(post.get("platform") or "").lower(), str(post.get("platform") or "").title())
    title = (post.get("hook") or post.get("title") or post.get("caption") or post["post_id"]).strip()
    if len(title) > 80:
        title = title[:77].rstrip() + "…"
    why = post.get("why_it_works") or _metrics_line(post)
    return {"id": post["post_id"], "niche": niche, "platform": platform, "score": score, "title": title, "why": why,
            "url": post.get("url"), "author": post.get("author"), "thumbnail": post.get("thumbnail_url"),
            "format": post.get("format_guess"), "likes": post.get("likes"), "views": post.get("views")}


def _metrics_line(post: dict) -> str:
    bits = [f"{post.get('likes', 0):,} likes", f"{post.get('comments', 0):,} comments", f"{post.get('shares', 0):,} shares"]
    if post.get("views"):
        bits.insert(0, f"{post['views']:,} views")
    return " · ".join(bits) + " — ranked by relative velocity and niche fit."
