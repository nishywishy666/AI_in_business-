"""Like → (credit-gated transcript) → 3 angles → filming guide + script (spec §12.3)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import uuid
from dataclasses import dataclass

from pydantic import ValidationError

from ..clock import iso
from ..jobs.daily_pull import daily_pull
from ..jobs.scan import ScanDeps
from ..packets import Script, TrendPacket
from ..scrapers import LIKE_ENDPOINTS, ScrapeCreatorsError
from ..scrapers.transcripts import youtube_video_id
from ..usage import refresh_snapshot
from .prompts import angles_prompt, angles_system, script_prompt, script_system

log = logging.getLogger(__name__)
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
MAX_TRANSCRIPT_SECONDS = 120


class PostNotFound(KeyError):
    pass


@dataclass
class LikeResult:
    post: TrendPacket
    angles: list[str]
    breakdown: str | None
    transcript_source: str  # youtube_free | scrapecreators | caption_only | cached
    model_used: str
    quality: str
    quality_warning: str | None


def like_post(deps: ScanDeps, post_id: str) -> LikeResult:
    now = deps.clock()
    post = _load_post(deps, post_id)
    context = daily_pull(deps.store, deps.cache, deps.settings, deps.clock).context

    post = post.model_copy(update={"liked": True})
    transcript_source = "cached" if post.transcript else "caption_only"
    if not post.transcript:
        transcript, transcript_source = _fetch_transcript(deps, post)
        if transcript:
            post = post.model_copy(update={"transcript": transcript})
    deps.store.set(deps.store.paths.post(post.post_id), post.to_doc())

    result = deps.gemini.generate("angles", system=angles_system(),
                                  prompt=angles_prompt(context, post, transcript_source))
    data = _parse_json(result.text)
    angles = [str(a).strip() for a in (data.get("angles") or []) if str(a).strip()][:3]
    if len(angles) < 3:
        raise ValueError(f"Gemini returned {len(angles)} angles, expected 3")
    breakdown = str(data.get("breakdown") or "").strip() or None
    post = post.model_copy(update={"angles": angles, "breakdown": breakdown})
    deps.store.set(deps.store.paths.post(post.post_id), post.to_doc())
    refresh_snapshot(deps.store, deps.settings, deps.clock, cache=deps.cache)
    return LikeResult(post, angles, breakdown, transcript_source, result.model_used, result.quality,
                      result.quality_warning)


def choose_angle(deps: ScanDeps, post_id: str, angle: int | str) -> Script:
    """Generate filming guide + script for one angle and store it as a draft (expires in 14 days)."""
    now = deps.clock()
    post = _load_post(deps, post_id)
    if not post.angles:
        raise ValueError("post has no angles yet — call like_post first")
    if isinstance(angle, int):
        if not 0 <= angle < len(post.angles):
            raise ValueError(f"angle index {angle} out of range")
        chosen = post.angles[angle]
    else:
        chosen = angle.strip()
    context = daily_pull(deps.store, deps.cache, deps.settings, deps.clock).context

    result = deps.gemini.generate("script", system=script_system(), prompt=script_prompt(context, post, chosen))
    data = _parse_json(result.text)
    script = Script(
        script_id=uuid.uuid4().hex, user_id=deps.store.user_id, post_id=post.post_id, status="draft",
        angle=chosen, filming_guide=str(data.get("filming_guide") or "").strip(),
        script=str(data.get("script") or "").strip(), caption=(str(data.get("caption")).strip() if data.get("caption") else None),
        model_used=result.model_used, quality=result.quality, created_at=now,
        expires_at=now + dt.timedelta(days=deps.settings.draft_ttl_days),
    )
    if not script.script:
        raise ValueError("Gemini returned an empty script")
    deps.store.set(deps.store.paths.script(script.script_id), script.to_doc())
    deps.store.update(deps.store.paths.post(post.post_id), {
        "chosen_angle": chosen, "filming_guide": script.filming_guide, "script_id": script.script_id,
    })
    refresh_snapshot(deps.store, deps.settings, deps.clock, cache=deps.cache)
    return script


# ---- helpers ---------------------------------------------------------------------------

def _load_post(deps: ScanDeps, post_id: str) -> TrendPacket:
    doc = deps.store.get(deps.store.paths.post(post_id))
    if doc is None:
        raise PostNotFound(post_id)
    try:
        return TrendPacket.model_validate(doc)
    except ValidationError as exc:
        raise PostNotFound(f"{post_id}: corrupt packet ({exc})") from exc


def _fetch_transcript(deps: ScanDeps, post: TrendPacket) -> tuple[str | None, str]:
    """YouTube → free library. Else 1 ScrapeCreators credit only if remaining > reserve and ≤ 120s."""
    if post.platform == "youtube":
        provider = deps.youtube_transcripts
        video_id = youtube_video_id(post.url, post.provider_id)
        if provider is None or not video_id:
            return None, "caption_only"
        try:
            text = provider.fetch(video_id)
        except Exception as exc:
            log.warning("free transcript failed for %s: %s", post.post_id, exc)
            return None, "caption_only"
        return (text, "youtube_free") if text else (None, "caption_only")

    if post.platform == "reddit":
        return None, "caption_only"
    remaining = deps.scraper.cached_credits_remaining()
    if remaining is None or remaining <= deps.settings.reserve:
        return None, "caption_only"
    if post.duration_sec is None or post.duration_sec > MAX_TRANSCRIPT_SECONDS or not post.url:
        return None, "caption_only"
    endpoint = LIKE_ENDPOINTS.get(f"{post.platform}_transcript")
    if endpoint is None:
        return None, "caption_only"
    try:
        result = deps.scraper.fetch(endpoint, {"url": post.url})
    except (ScrapeCreatorsError, ValueError) as exc:
        log.warning("transcript call failed for %s: %s", post.post_id, exc)
        return None, "caption_only"
    text = _extract_transcript(result.body)
    return (text, "scrapecreators") if text else (None, "caption_only")


def _extract_transcript(body) -> str | None:
    if isinstance(body, str):
        return body.strip() or None
    if not isinstance(body, dict):
        return None
    for key in ("transcript", "text", "transcript_text", "captions"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            parts = [str(v.get("text") if isinstance(v, dict) else v) for v in value]
            joined = " ".join(p for p in parts if p and p != "None").strip()
            if joined:
                return joined
    return None


def _parse_json(text: str) -> dict:
    cleaned = _FENCE.sub("", text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Gemini did not return a JSON object")
    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini returned malformed JSON: {exc}") from exc
    return data if isinstance(data, dict) else {}


__all__ = ["like_post", "choose_angle", "LikeResult", "PostNotFound", "iso"]
