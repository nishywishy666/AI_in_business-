"""Parse the parent's questionnaire (Firestore map or context.md) into a ContextProfile.

Never asks the questionnaire again and never writes back to the parent document.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from ..packets import ContextProfile

_ALIASES: dict[str, tuple[str, ...]] = {
    "niche": ("niche", "topic", "industry", "business"),
    "keywords": ("keywords", "seed keywords", "seeds", "seed keywords / hashtags", "search terms"),
    "hashtags": ("hashtags", "seed hashtags", "tags"),
    "platforms": ("platforms", "platform", "channels", "post on"),
    "format": ("format", "formats", "content format", "format they can produce"),
    "region": ("region", "country", "location", "market"),
    "language": ("language", "lang"),
    "goal": ("goal", "goals", "objective"),
    "facebook_page_urls": ("facebook pages", "facebook page urls", "facebook page", "facebook", "fb pages"),
    "facebook_group_urls": ("facebook group", "facebook groups", "facebook group url", "facebook group urls",
                            "fb group"),
    "competitor_handles": ("competitors", "competitor handles", "competitor", "accounts to watch"),
    "subreddits": ("subreddits", "subreddit", "reddit"),
    "audience_line": ("audience", "target audience", "who is it for", "audience line"),
}
_ALIAS_LOOKUP = {alias: field for field, aliases in _ALIASES.items() for alias in aliases}
_LIST_FIELDS = {"keywords", "hashtags", "platforms", "facebook_page_urls", "facebook_group_urls",
                "competitor_handles", "subreddits"}
_CAPS = {"facebook_page_urls": 2, "facebook_group_urls": 1, "competitor_handles": 3, "subreddits": 2}
_TEXT_KEYS = ("content", "markdown", "text", "context", "body", "raw")
_KEY_LINE = re.compile(r"^\s*(?:[-*]\s*)?\**([A-Za-z][A-Za-z /]{1,40}?)\**\s*[:：]\s*(.*)$")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")
_BULLET = re.compile(r"^\s*[-*•]\s+(.+?)\s*$")
_URL = re.compile(r"https?://\S+")
_STOPWORDS = {"the", "and", "for", "with", "your", "you", "from", "that", "this", "are", "our", "how", "who"}


def parse_context(source: dict | str | None, *, user_id: str, source_path: str,
                  now: dt.datetime) -> ContextProfile:
    if source is None:
        raise ValueError("context source is empty")
    if isinstance(source, dict):
        text_blob = _dict_text_blob(source)
        fields = _fields_from_markdown(text_blob) if text_blob is not None else _fields_from_map(source)
        raw_text = text_blob if text_blob is not None else _dump_map(source)
    else:
        fields = _fields_from_markdown(source)
        raw_text = source
    return _build_profile(fields, user_id=user_id, source_path=source_path, now=now, raw_text=raw_text)


def local_expand(profile: ContextProfile, limit: int = 8) -> list[str]:
    """Fallback for the optional Gemini expand: derive hashtags from niche + keywords locally."""
    tags: list[str] = []
    for term in [profile.niche, *profile.keywords]:
        compact = re.sub(r"[^a-z0-9]", "", term.lower())
        if len(compact) > 2 and compact not in tags:
            tags.append(compact)
        for token in re.findall(r"[a-z0-9]+", term.lower()):
            if len(token) > 3 and token not in _STOPWORDS and token not in tags:
                tags.append(token)
    return tags[:limit]


def _dict_text_blob(source: dict) -> str | None:
    lowered = {str(k).lower(): v for k, v in source.items()}
    if any(alias in lowered for alias in _ALIAS_LOOKUP):
        return None
    for key in _TEXT_KEYS:
        value = lowered.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _dump_map(source: dict) -> str:
    return "\n".join(f"{k}: {v}" for k, v in source.items())


def _fields_from_map(source: dict) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in source.items():
        field = _ALIAS_LOOKUP.get(_norm_key(str(key)))
        if field:
            fields[field] = value
    return fields


def _fields_from_markdown(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    current: str | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        heading = _HEADING.match(line)
        if heading:
            current = _ALIAS_LOOKUP.get(_norm_key(heading.group(1)))
            continue
        key_line = _KEY_LINE.match(line)
        if key_line and _ALIAS_LOOKUP.get(_norm_key(key_line.group(1))):
            field = _ALIAS_LOOKUP[_norm_key(key_line.group(1))]
            value = key_line.group(2).strip()
            if value:
                fields[field] = value
                current = None
            else:
                fields.setdefault(field, [])
                current = field
            continue
        bullet = _BULLET.match(line)
        if current:
            item = bullet.group(1) if bullet else line.strip()
            existing = fields.get(current)
            if isinstance(existing, list):
                existing.append(item)
            elif existing in (None, ""):
                fields[current] = [item]
            else:
                fields[current] = f"{existing} {item}"
    return fields


def _norm_key(key: str) -> str:
    return re.sub(r"\s+", " ", key.strip().lower().strip("*:").replace("_", " "))


def _split_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value]
    else:
        items = re.split(r"[,;\n]+", str(value))
    return [item.strip().strip("-•* ").strip() for item in items if item and item.strip().strip("-•* ")]


def _build_profile(fields: dict[str, Any], *, user_id: str, source_path: str, now: dt.datetime,
                   raw_text: str) -> ContextProfile:
    data: dict[str, Any] = {}
    for field in _LIST_FIELDS:
        data[field] = _split_list(fields.get(field))
    for field in ("niche", "format", "region", "language", "goal", "audience_line"):
        value = fields.get(field)
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        data[field] = str(value).strip() if value not in (None, "") else None

    data["hashtags"] = _dedup(_clean_hashtag(tag) for tag in data["hashtags"] if _clean_hashtag(tag))
    data["keywords"] = _dedup(k for k in data["keywords"] if k)
    data["platforms"] = _dedup(p.lower().replace(" ", "") for p in data["platforms"]) or ["tiktok", "instagram"]
    data["competitor_handles"] = [h.lstrip("@") for h in data["competitor_handles"]]
    data["subreddits"] = [s.lstrip("r/").lstrip("/").replace("r/", "") for s in data["subreddits"]]

    urls = _URL.findall(raw_text)
    fb_urls = [u.rstrip(".,)'\"]") for u in urls if "facebook.com" in u.lower()]
    pages = data["facebook_page_urls"] or [u for u in fb_urls if "/groups/" not in u.lower()]
    groups = data["facebook_group_urls"] or [u for u in fb_urls if "/groups/" in u.lower()]
    data["facebook_page_urls"] = _dedup(u for u in pages if "facebook.com" in u.lower())
    data["facebook_group_urls"] = _dedup(u for u in groups if "facebook.com" in u.lower())

    goal = (data.get("goal") or "").lower()
    if goal:
        for canonical in ("followers", "leads", "authority"):
            if canonical in goal:
                data["goal"] = canonical
                break

    for field, cap in _CAPS.items():
        data[field] = data[field][:cap]

    return ContextProfile(
        user_id=user_id,
        niche=data.get("niche") or "",
        keywords=data["keywords"][:6],
        hashtags=data["hashtags"],
        platforms=data["platforms"],
        format=data.get("format"),
        region=data.get("region"),
        language=data.get("language"),
        goal=data.get("goal"),
        facebook_page_urls=data["facebook_page_urls"],
        facebook_group_urls=data["facebook_group_urls"],
        competitor_handles=data["competitor_handles"],
        subreddits=data["subreddits"],
        audience_line=data.get("audience_line"),
        raw_text=raw_text,
        source_path=source_path,
        pulled_at=now,
    )


def _clean_hashtag(tag: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", tag.lower().lstrip("#"))


def _dedup(items) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))
