"""Dependency wiring. Real clients when keys exist; fakes + fixtures when offline."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .agent import GeminiLadder, GeminiTransport, GenAiTransport
from .agent.gemini import GeminiRaw
from .cache import LocalCache
from .clock import Clock, utc_now
from .config import Settings
from .db import Backend, MemoryBackend, RadarStore
from .jobs.scan import ScanDeps
from .scrapers import (
    LIKE_ENDPOINTS,
    SCAN_ENDPOINTS,
    FixtureTransport,
    HttpTransport,
    HttpxTransport,
    ScrapeCreatorsClient,
)
from .scrapers.free_reddit import FreeReddit
from .scrapers.free_trends import FakeTrendsProvider, FreeTrends, PytrendsProvider, TrendsProvider
from .scrapers.free_youtube import FreeYouTube
from .scrapers.transcripts import FakeTranscriptProvider, TranscriptProvider, YouTubeTranscriptProvider

_POST_IDS = re.compile(r"POST_IDS:\s*(\[[^\]]*\])")


class OfflineGeminiTransport:
    """Deterministic stand-in: builds a valid synthesis from the ids in the prompt (no network)."""

    def __init__(self, *, rate_limit_models: set[str] | None = None) -> None:
        self.rate_limit_models = rate_limit_models or set()
        self.calls: list[tuple[str, str]] = []

    def generate(self, model_id: str, *, system: str, prompt: str, json_mode: bool = True) -> GeminiRaw:
        from .agent.gemini import RateLimited

        self.calls.append((model_id, prompt[:80]))
        if model_id in self.rate_limit_models:
            raise RateLimited(f"offline: {model_id} rate limited")
        purpose = _purpose(system)
        if purpose == "expand":
            return GeminiRaw(text=json.dumps({"hashtags": ["offlinetag1", "offlinetag2", "offlinetag3"], "subreddits": []}))
        if purpose == "angles":
            return GeminiRaw(text=json.dumps({
                "breakdown": "Opens on the time constraint, shows the routine in one take, closes on a visible result. "
                             "Short, direct, no music bed — the caption carries the promise.",
                "angles": ["Same one-take format: the 3 moves you can do while the kettle boils",
                           "Same before/after beat: one week of school-run stretches",
                           "Same hook grammar: 'you have 12 minutes, not 60' — bodyweight circuit for dads"],
            }), tokens=300)
        if purpose == "script":
            angle = re.search(r"CHOSEN_ANGLE: (.+)", prompt)
            chosen = angle.group(1).strip() if angle else "the chosen angle"
            return GeminiRaw(text=json.dumps({
                "filming_guide": f"Phone on the counter at chest height, natural light, one continuous take, 20-30s. "
                                 f"On-screen text with the time number. Angle: {chosen}.",
                "script": "You've got fifteen minutes before pickup? Good — that's all this needs. Squats while the "
                          "kettle boils, push-ups on the bench, thirty seconds of marching on the spot. Three rounds. "
                          "Done before the toast pops. Save this for tomorrow morning.",
                "caption": "15 minutes, zero equipment, done before school run. #homeworkout #busyparents #fitmom",
            }), tokens=350)
        if purpose == "chat":
            return GeminiRaw(text=json.dumps(_offline_chat(prompt)), tokens=200)
        match = _POST_IDS.search(prompt)
        ids = json.loads(match.group(1)) if match else []
        recap = "off-day recap" in prompt
        cards = [{"post_id": pid, "why_it_works": f"Fast hook and a clear payoff; {pid.split('_')[0]} audiences reward it.",
                  "format_guess": "talking head" if i % 2 else "on-screen text"} for i, pid in enumerate(ids)]
        payload = {
            "weekly_take": ("Nothing new was spent today. " if recap else "") +
                           "Short, direct hooks about saving time keep winning for this niche. Comparison formats "
                           "(quick routine vs long gym session) and before/after proofs are drawing the most shares. "
                           "Posts under 30 seconds with a single concrete takeaway outperform longer explainers. "
                           "Parent-specific framing (naps, school runs) beats generic fitness. Keep captions short "
                           "and lead with the constraint. Film in one take, on-screen text for the key number.",
            "patterns": ["Hook states the time constraint in the first line",
                         "Comparison or before/after structure",
                         "Under 30 seconds, one takeaway"],
            "ignore": ["Long follow-along sessions", "Generic motivation quotes", "Trending audio with no message"],
            "cards": cards,
            "film_this": {"post_id": ids[0], "why": "Strongest velocity among niche-fit posts; format is easy to reproduce."} if ids else None,
            "playbook_delta": ["Time-constraint hooks still win for busy-parent fitness",
                               "Comparison formats travel across TikTok and Instagram"],
        }
        return GeminiRaw(text=json.dumps(payload), tokens=420)


def _purpose(system: str) -> str:
    match = re.match(r"\s*PURPOSE:\s*(\w+)", system or "")
    return match.group(1) if match else "synthesize"


def _offline_chat(prompt: str) -> dict:
    """Deterministic chat policy: pick a tool from the user's words, then answer from TOOL_RESULT."""
    users = re.findall(r"^USER: (.+)$", prompt, flags=re.MULTILINE)
    message = (users[-1] if users else "").lower()
    has_result = "TOOL_RESULT:" in prompt
    attached = re.search(r'"attached_post": \{"post_id": "([^"]+)"', prompt)
    post_id = attached.group(1) if attached else None
    if not has_result:
        if "tomorrow" in message or "what should i post" in message or "film" in message:
            return {"reply": "", "tool": {"name": "recommend_tomorrow", "args": {}}}
        if "caption" in message:
            return {"reply": "", "tool": {"name": "captions", "args": {"post_id": post_id}}}
        if "hook" in message:
            return {"reply": "", "tool": {"name": "rewrite_hook", "args": {"post_id": post_id}}}
        if "like" in message or "angle" in message:
            return {"reply": "", "tool": {"name": "like_trend", "args": {"post_id": post_id}}}
        if "brief" in message or "trending" in message or "working" in message:
            return {"reply": "", "tool": {"name": "get_brief", "args": {}}}
        return {"reply": "Ask me what to post tomorrow, for captions or a hook rewrite on a card, or to Like a trend "
                         "for angles. I only use the latest brief — I can't scrape new data from chat.", "tool": None}
    if "captions" in prompt.split("TOOL_RESULT:")[-1][:200] or "exactly 3 caption" in prompt:
        return {"reply": "Three caption options:\n1. 15 minutes, zero equipment, done before pickup. #homeworkout #busyparents\n"
                         "2. The workout that fits between the kettle and the school run. #fitmom #homeworkout\n"
                         "3. No gym, no excuses, no more than 15 minutes. #busyparents #noequipment", "tool": None}
    if '"angles"' in prompt:
        return {"reply": "Liked. Three angles that keep the format: (1) the kettle-boil 3 moves, (2) one week of "
                         "school-run stretches, (3) '12 minutes, not 60' dad circuit. Which one should I script?", "tool": None}
    if '"patterns"' in prompt and "alternative hooks" in prompt:
        return {"reply": "Three hooks: 'You have 15 minutes before pickup — use them.' / 'Nobody with kids has an hour. "
                         "Here's 12.' / 'Stop waiting for gym time. Kitchen floor works.'", "tool": None}
    scan = re.search(r'"scan_id": "([^"]+)"', prompt)
    return {"reply": f"Based on scan {scan.group(1) if scan else 'latest'}: film the time-constraint hook in one take, "
                     f"under 30 seconds, with the number on screen. Post it tomorrow morning.", "tool": None}


LIKE_FIXTURE = {"success": True, "credits_charged": 1, "credits_remaining": 94,
                "transcript": "You've got fifteen minutes? Great, that's all this takes. Squats, push-ups, march. "
                              "Three rounds. Done before the toast pops."}


def offline_transport(fixtures_dir: Path) -> FixtureTransport:
    transport = FixtureTransport(fixtures_dir)
    for endpoint in SCAN_ENDPOINTS.values():
        transport.route(endpoint.path, Path("scrapecreators") / f"{endpoint.key}.json")
    for endpoint in LIKE_ENDPOINTS.values():
        transport.route(endpoint.path, LIKE_FIXTURE)
    transport.route("/v1/account/credit-balance", {"credits_remaining": 100})
    transport.route("https://www.googleapis.com/youtube/v3/search", Path("free") / "youtube_search.json")
    transport.route("https://www.googleapis.com/youtube/v3/videos", Path("free") / "youtube_videos.json")
    transport.route("https://www.reddit.com/r/", Path("free") / "reddit_hot.json")
    return transport


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = REPO_ROOT / "tests" / "fixtures"


def offline_settings(settings: Settings, *, fixtures_dir: Path | None = None,
                     cache_root: Path | None = None) -> Settings:
    """Force offline mode: fixtures + placeholder keys + a repo-local cache root (CLI and dashboard)."""
    settings.offline = True
    settings.fixtures_dir = fixtures_dir or settings.fixtures_dir or DEFAULT_FIXTURES
    settings.scrapecreators_api_key = settings.scrapecreators_api_key or "offline"
    settings.youtube_api_key = settings.youtube_api_key or "offline"
    settings.gemini_api_key = settings.gemini_api_key or "offline"
    settings.cache_root = cache_root or settings.cache_root or REPO_ROOT / ".marketing_radar_cache"
    return settings


def offline_backend(user_id: str, settings: Settings, *, context_seed: dict | None = None) -> Backend:
    """JSON-file Firestore stand-in under the cache root, with the parent questionnaire seeded once."""
    from .db.json_backend import JsonFileBackend

    backend = JsonFileBackend((settings.cache_root or DEFAULT_FIXTURES) / "offline_firestore.json")
    context_path = settings.context_path(user_id)
    if backend.get(context_path) is None and context_seed is not None:
        backend.set(context_path, context_seed)
    return backend


def build_deps(user_id: str, settings: Settings, *, backend: Backend | None = None,
               transport: HttpTransport | None = None, gemini_transport: GeminiTransport | None = None,
               trends_provider: TrendsProvider | None = None, clock: Clock = utc_now,
               cache_root: Path | None = None, transcripts: TranscriptProvider | None = None) -> ScanDeps:
    if not user_id:
        raise ValueError("user_id is required")
    if backend is None:
        if settings.offline:
            backend = MemoryBackend()
        else:
            from .db.firestore_backend import FirestoreBackend

            backend = FirestoreBackend(settings.firebase_project_id, settings.firebase_credentials_json)
    store = RadarStore(user_id, backend, settings)
    cache = LocalCache(user_id, cache_root or settings.cache_root)

    if transport is None:
        if settings.offline:
            if settings.fixtures_dir is None:
                raise ValueError("offline mode needs settings.fixtures_dir")
            transport = offline_transport(settings.fixtures_dir)
        else:
            transport = HttpxTransport()

    if gemini_transport is None:
        if settings.offline or not settings.gemini_api_key:
            gemini_transport = OfflineGeminiTransport()
        else:
            gemini_transport = GenAiTransport(settings.gemini_api_key)

    if trends_provider is None:
        trends_provider = FakeTrendsProvider() if settings.offline else PytrendsProvider
    trends = FreeTrends(trends_provider)

    if transcripts is None:
        transcripts = (FakeTranscriptProvider({"ytfree00001": "Fifteen minutes, no equipment. Squats, push-ups, march. Go."})
                       if settings.offline else YouTubeTranscriptProvider())

    return ScanDeps(
        store=store, cache=cache, settings=settings, clock=clock,
        scraper=ScrapeCreatorsClient(store, transport, settings, clock),
        youtube=FreeYouTube(store, transport, settings, clock),
        reddit=FreeReddit(transport, settings, clock),
        trends=trends,
        gemini=GeminiLadder(store, settings, gemini_transport, clock),
        youtube_transcripts=transcripts,
    )
