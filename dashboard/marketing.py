"""Marketing agent wiring for the dashboard: build the `ScanDeps` the parent owns, seed the
questionnaire, and turn the latest ScanBrief into the trend cards the Marketing screen binds.

Tenancy decision (plan 0005): `MARKETING_USER_ID` defaults to `BUSINESS_ID`. Offline whenever
`MARKETING_RADAR_OFFLINE=1` or the live keys are missing — then the JSON-file backend under
`.marketing_radar_cache/` plus recorded fixtures stand in and no credit is ever spent.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from marketing_radar.agent.gemini import GeminiExhausted
from marketing_radar.agent.like import PostNotFound, choose_angle, like_post
from marketing_radar.config import Settings
from marketing_radar.deps import build_deps, offline_backend, offline_settings
from marketing_radar.jobs.scan import PLATFORM_SLOTS, ScanDeps
from marketing_radar.scrapers import endpoint_by_key
from marketing_radar.services import get_brief, get_marketing_summary, get_post, list_scripts, save_script
from marketing_radar.services.api import RadarApi

from .settings import DashboardSettings

log = logging.getLogger(__name__)

PLATFORM_LABELS = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube Shorts", "facebook": "Facebook",
                   "reddit": "Reddit"}
# The dashboard polls, so anything it reads on every poll is read all day. A brief carries 3 global +
# 5 niche posts, each its own document, and the usage snapshot is another handful — ~30 reads a poll,
# which is ~43,000 a day against a 50,000 free tier. These are cached for a beat instead; a scan lands
# every two days, so a few minutes of staleness costs nothing and the numbers stay inside the tier.
TRENDS_TTL = float(os.environ.get("MARKETING_TRENDS_TTL", "300") or 300)
STATUS_TTL = float(os.environ.get("MARKETING_STATUS_TTL", "60") or 60)

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
        self._memo: dict[str, tuple[float, Any]] = {}
        self._pull_lock = threading.Lock()  # one owner-triggered pull at a time (plan 0013)
        self.error: str | None = None

    # ---- read-through cache ----------------------------------------------------------------------
    def _memoized(self, key: str, ttl: float, build: Callable[[], Any]) -> Any:
        hit = self._memo.get(key)
        if hit is not None and ttl > 0 and time.monotonic() - hit[0] < ttl:
            return hit[1]
        value = build()
        self._memo[key] = (time.monotonic(), value)
        return value

    def forget(self, *keys: str) -> None:
        """Drop cached reads after something changes them (a like, a save, a forced refresh)."""
        for key in keys or tuple(self._memo):
            self._memo.pop(key, None)

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
            self._memo.clear()
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

    def ai_status(self, *, fresh: bool = False) -> dict:
        """The line above each chat: which free model is answering, how much of its day is left, and
        the scan credits behind the trend data. Read-only and best effort — a chat that works while
        this is unavailable should not be blocked by it.

        Cached for `STATUS_TTL`, because the bootstrap asks for it on every poll. `fresh=True` is the
        path the chats take after a turn, where the whole point is that the number moved."""
        if fresh:
            self.forget("ai_status")
        return self._memoized("ai_status", STATUS_TTL, self._ai_status)

    def _ai_status(self) -> dict:
        out: dict[str, Any] = {"provider": "Gemini free tier", "model": None, "modelId": None,
                               "usedToday": None, "capToday": None, "credits": None, "paused": False,
                               "resetsIn": None, "offline": self.offline}
        try:
            from marketing_radar.usage import build_snapshot

            deps = self.deps()
            snap = build_snapshot(deps.store, deps.settings, deps.clock)
        except Exception as exc:
            log.warning("ai status unavailable: %s", exc)
            return out
        active = next((m for m in snap.gemini.models if m.status == "ok"), None)
        out["model"] = active.label if active else None
        out["modelId"] = active.id if active else None
        out["usedToday"] = active.used_today if active else None
        out["capToday"] = active.daily_cap if active else None
        out["paused"] = active is None
        out["resetsIn"] = snap.gemini.resets_in
        out["credits"] = snap.scrapecreators.remaining
        out["qualityWarning"] = snap.gemini.quality_warning
        return out

    def summary(self) -> dict | None:
        deps = self.deps()
        try:
            return get_marketing_summary(deps.store, deps.cache, deps.settings, clock=deps.clock)
        except Exception as exc:
            log.warning("marketing summary unavailable: %s", exc)
            return None

    def trends(self, *, force: bool = False) -> dict[str, Any]:
        """UI-shaped cards, cached for `TRENDS_TTL` — the bootstrap asks for these on every poll, and
        building them costs one read per post. `force=True` (the "Refresh now" button) re-reads
        Firestore instead of the once-a-day local cache.

        `score` is rank-normalised inside each list (top card = 99) because the AIOS `final` is a
        relative velocity, not a percentage."""
        if not force:
            return self._memoized("trends", TRENDS_TTL, lambda: self._trends(force=False))
        self.reset()
        return self._memoized("trends", TRENDS_TTL, lambda: self._trends(force=True))

    def _trends(self, *, force: bool = False) -> dict[str, Any]:
        """Builds the cards. `force` only steers `get_brief` here — the caller has already reset."""
        try:
            brief, deps = self.brief(force=force), self.deps()
        except Exception as exc:  # deps could not be built at all: report it, don't 500 the dashboard
            self.error = str(exc)[:200]
            log.warning("marketing deps unavailable: %s", exc)
            brief, deps = None, None
        if brief is None:
            return {"items": [], "likedIds": [], "savedIds": [], "savedCount": 0, "scanId": None, "empty": True,
                    "note": self.error or "No scan yet — the first trend scan is scheduled.", "greeting": EMPTY_GREETING,
                    "offline": self.offline, **self._pull_info()}
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
                "empty": False, "note": brief.get("note"), "greeting": CHAT_GREETING, "offline": self.offline,
                **self._pull_info()}

    def _pull_info(self) -> dict[str, Any]:
        """What "Refresh now" will do, for the button's confirmation: every platform, live."""
        try:
            cost = int(self.settings.max_live_calls)
        except Exception:
            cost = len(PLATFORM_SLOTS)
        return {"pullCost": cost, "pullPlatforms": [PLATFORM_LABELS[p] for p in PLATFORM_SLOTS]}

    # ---- the owner's "Refresh now" (plan 0013) ------------------------------------------------------
    def pull_now(self) -> dict[str, Any]:
        """A real pull, not a re-read: every platform is fetched live through ScrapeCreators (bypassing
        the 48h request cache), the brief is rebuilt, then the trends are re-read. One at a time — a
        second click while a pull is running reports that instead of spending again."""
        if not self._pull_lock.acquire(blocking=False):
            out = self.trends(force=True)
            out["pull"] = {"ran": False, "note": "A pull is already running; the list will update when it lands."}
            return out
        try:
            from marketing_radar.jobs.scan import run_paid_scan

            outcome = run_paid_scan(self.deps(), fresh=True)
            platforms = [PLATFORM_LABELS.get(endpoint_by_key(c.endpoint_key).platform, c.endpoint_key)
                         for c in outcome.planned]
            pull: dict[str, Any] = {"ran": True, "note": outcome.note, "liveCalls": outcome.live_calls,
                                    "creditsSpent": outcome.credits_spent, "platforms": platforms,
                                    "scanId": outcome.brief.scan_id if outcome.brief else None}
        except Exception as exc:
            self.error = str(exc)[:200]
            log.warning("pull failed: %s", exc)
            pull = {"ran": False, "note": f"Pull failed: {str(exc)[:160]}"}
        finally:
            self._pull_lock.release()
        out = self.trends(force=True)
        out["pull"] = pull
        return out

    # ---- write side (Like / Save) -------------------------------------------------------------------
    def like(self, post_id: str) -> tuple[int, dict]:
        try:
            return self.api().like(post_id)
        finally:
            self.forget("trends", "ai_status")  # the card's state and the model counters both moved

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
            self.forget("trends")
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
    """Every scan is meant to carry all four platforms (plan 0013). When one is missing, say which and
    why: its endpoint was never called (no credits, or Facebook without a page/group URL), or it was
    called and nothing from it ranked into the lists."""
    if not items:
        return None
    present = {c["platform"] for c in items}
    label_of = {p: PLATFORM_LABELS[p] for p in PLATFORM_SLOTS}
    missing = [p for p in PLATFORM_SLOTS if label_of[p] not in present]
    if not missing:
        return None
    sources = [str(s) for s in (brief.get("sources_used") or [])]
    called = {p for p in PLATFORM_SLOTS if any(s.startswith(p) for s in sources)}
    not_called = [label_of[p] for p in missing if p not in called]
    ranked_out = [label_of[p] for p in missing if p in called]
    credits = brief.get("credits") if isinstance(brief.get("credits"), dict) else {}
    remaining = credits.get("remaining")
    parts = []
    if not_called:
        why = []
        if "Facebook" in not_called:
            why.append("Facebook needs a page or group URL (ScrapeCreators has no Facebook search)")
        if [m for m in not_called if m != "Facebook"] or not why:
            why.append(f"{remaining} ScrapeCreators credit{'s' if remaining != 1 else ''} left"
                       if isinstance(remaining, int) else "they need ScrapeCreators credits")
        parts.append(f"{', '.join(not_called)} not pulled this scan: {'; '.join(why)}")
    if ranked_out:
        parts.append(f"nothing from {', '.join(ranked_out)} ranked high enough this scan")
    return ". ".join(p[0].upper() + p[1:] for p in parts) + "."


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
