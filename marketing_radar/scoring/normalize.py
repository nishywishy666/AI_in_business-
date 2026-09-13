"""Raw provider payloads → TrendPacket (spec §9.2, §11.1).

ScrapeCreators shapes were not recorded against a live key; every lookup is a defensive
multi-key pick so the first live scan can correct field names without restructuring.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Iterable

from ..clock import UTC, parse_iso
from ..packets import TrendPacket

HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]+)")
SENTENCE_SPLIT = re.compile(r"[.!?\n]")
PLATFORM_SHORT = {"tiktok": "tt", "instagram": "ig", "youtube": "yt", "facebook": "fb", "reddit": "rd"}

_LIST_KEYS = ("aweme_list", "search_item_list", "reels", "posts", "shorts", "videos", "items", "data",
              "results", "medias", "media")


def normalize_response(platform: str, body: Any, *, source: str, scraped_at: dt.datetime) -> list[TrendPacket]:
    fn = {
        "tiktok": normalize_tiktok,
        "instagram": normalize_instagram,
        "youtube": normalize_youtube,
        "facebook": normalize_facebook,
        "reddit": normalize_reddit,
    }[platform]
    packets: list[TrendPacket] = []
    for item in _items(body):
        packet = fn(item, source=source, scraped_at=scraped_at)
        if packet is not None:
            packets.append(packet)
    return packets


def normalize_tiktok(item: dict, *, source: str, scraped_at: dt.datetime) -> TrendPacket | None:
    item = item.get("aweme_info") or item.get("item") or item
    provider_id = _str(pick(item, "aweme_id", "id", "video_id"))
    if not provider_id:
        return None
    caption = _str(pick(item, "desc", "description", "caption", "text")) or ""
    hashtags = [h.get("hashtag_name") for h in item.get("text_extra") or [] if isinstance(h, dict)]
    author = _str(pick(item, "author.unique_id", "author.uniqueId", "author.nickname", "author_name", "author"))
    return _packet(
        platform="tiktok", provider_id=provider_id, source=source, scraped_at=scraped_at,
        url=_str(pick(item, "share_url", "url", "webVideoUrl")) or f"https://www.tiktok.com/@{author or 'user'}/video/{provider_id}",
        author=author,
        published_at=_when(pick(item, "create_time", "createTime", "created_at", "createTimeISO")),
        likes=_int(pick(item, "statistics.digg_count", "stats.diggCount", "digg_count", "likes", "like_count")),
        comments=_int(pick(item, "statistics.comment_count", "stats.commentCount", "comment_count", "comments")),
        shares=_int(pick(item, "statistics.share_count", "stats.shareCount", "share_count", "shares")),
        views=_opt_int(pick(item, "statistics.play_count", "stats.playCount", "play_count", "views")),
        duration_sec=_duration(pick(item, "video.duration", "duration"), millis_if_large=True),
        caption=caption, hashtags=_merge_hashtags(hashtags, caption),
        sound=_str(pick(item, "music.title", "music.name", "sound")),
        thumbnail_url=_str(pick(item, "video.cover.url_list.0", "video.cover", "cover")),
    )


def normalize_instagram(item: dict, *, source: str, scraped_at: dt.datetime) -> TrendPacket | None:
    item = item.get("media") or item.get("node") or item
    provider_id = _str(pick(item, "id", "pk", "media_id", "code", "shortcode"))
    if not provider_id:
        return None
    caption = _str(pick(item, "caption.text", "caption_text", "caption", "text")) or ""
    code = _str(pick(item, "code", "shortcode"))
    likes = pick(item, "like_count", "likes", "likesCount")
    return _packet(
        platform="instagram", provider_id=provider_id, source=source, scraped_at=scraped_at,
        url=_str(pick(item, "url", "permalink")) or (f"https://www.instagram.com/reel/{code}/" if code else None),
        author=_str(pick(item, "user.username", "owner.username", "username", "ownerUsername")),
        published_at=_when(pick(item, "taken_at", "taken_at_timestamp", "timestamp", "created_at")),
        likes=_int(likes),
        comments=_int(pick(item, "comment_count", "comments", "commentsCount")),
        shares=_int(pick(item, "reshare_count", "share_count", "shares", "sharesCount")),
        views=_opt_int(pick(item, "play_count", "view_count", "video_view_count", "videoPlayCount", "views")),
        duration_sec=_duration(pick(item, "video_duration", "duration", "videoDuration")),
        caption=caption, hashtags=_merge_hashtags([], caption),
        sound=_str(pick(item, "clips_metadata.music_info.music_asset_info.title", "music.title", "musicInfo.song_name")),
        thumbnail_url=_str(pick(item, "thumbnail_url", "image_versions2.candidates.0.url", "displayUrl")),
    )


def normalize_youtube(item: dict, *, source: str, scraped_at: dt.datetime) -> TrendPacket | None:
    if "snippet" in item:  # YouTube Data API shape
        sn, st, cd = item.get("snippet") or {}, item.get("statistics") or {}, item.get("contentDetails") or {}
        provider_id = _str(item.get("id") if isinstance(item.get("id"), str) else pick(item, "id.videoId"))
        if not provider_id:
            return None
        title = sn.get("title") or ""
        desc = sn.get("description") or ""
        return _packet(
            platform="youtube", provider_id=provider_id, source=source, scraped_at=scraped_at,
            url=f"https://www.youtube.com/shorts/{provider_id}", author=sn.get("channelTitle"),
            published_at=_when(sn.get("publishedAt")),
            likes=_int(st.get("likeCount")), comments=_int(st.get("commentCount")), shares=0,
            views=_opt_int(st.get("viewCount")), duration_sec=_iso8601_duration(cd.get("duration")),
            caption=f"{title}\n{desc[:500]}".strip(), title=title, hashtags=_merge_hashtags([], f"{title} {desc}"),
            thumbnail_url=pick(sn, "thumbnails.high.url"),
        )
    provider_id = _str(pick(item, "id", "videoId", "video_id"))
    if not provider_id:
        return None
    title = _str(pick(item, "title")) or ""
    desc = _str(pick(item, "description", "desc")) or ""
    return _packet(
        platform="youtube", provider_id=provider_id, source=source, scraped_at=scraped_at,
        url=_str(pick(item, "url", "link")) or f"https://www.youtube.com/shorts/{provider_id}",
        author=_str(pick(item, "channel.name", "channel.title", "channelTitle", "channel", "author")),
        published_at=_when(pick(item, "publishedAt", "publishDate", "published_at", "uploadDate")),
        likes=_int(pick(item, "likeCount", "likes", "like_count")),
        comments=_int(pick(item, "commentCount", "comments", "comment_count")),
        shares=0,
        views=_opt_int(pick(item, "viewCount", "views", "view_count")),
        duration_sec=_duration(pick(item, "lengthSeconds", "duration_seconds", "duration")),
        caption=f"{title}\n{desc[:500]}".strip(), title=title, hashtags=_merge_hashtags([], f"{title} {desc}"),
        thumbnail_url=_str(pick(item, "thumbnail", "thumbnails.0.url")),
    )


def normalize_facebook(item: dict, *, source: str, scraped_at: dt.datetime) -> TrendPacket | None:
    provider_id = _str(pick(item, "id", "post_id", "reel_id"))
    if not provider_id:
        return None
    caption = _str(pick(item, "description", "text", "message", "caption")) or ""
    comments = _int(pick(item, "comments_count", "comment_count", "comments", "commentCount"))
    shares = _int(pick(item, "shares_count", "share_count", "shares", "shareCount"))
    likes = pick(item, "likes", "like_count", "likes_count")
    if likes is None:
        likes = pick(item, "reactions_count", "reaction_count", "reactions", "reactionCount")
    return _packet(
        platform="facebook", provider_id=provider_id, source=source, scraped_at=scraped_at,
        url=_str(pick(item, "url", "permalink_url", "link")),
        author=_str(pick(item, "page_name", "author.name", "author", "owner.name")),
        published_at=_when(pick(item, "publish_time", "timestamp", "created_time", "published_at")),
        likes=_int(likes), comments=comments, shares=shares,
        views=_opt_int(pick(item, "view_count", "views", "play_count")),
        duration_sec=_duration(pick(item, "duration", "video_duration", "length")),
        caption=caption, hashtags=_merge_hashtags([], caption),
        thumbnail_url=_str(pick(item, "thumbnail", "thumbnail_url")),
    )


def normalize_reddit(item: dict, *, source: str, scraped_at: dt.datetime) -> TrendPacket | None:
    data = item.get("data") or item
    provider_id = _str(pick(data, "id", "name"))
    if not provider_id or data.get("stickied"):
        return None
    title = _str(pick(data, "title")) or ""
    body = _str(pick(data, "selftext")) or ""
    return _packet(
        platform="reddit", provider_id=provider_id, source=source, scraped_at=scraped_at,
        url=("https://www.reddit.com" + data["permalink"]) if data.get("permalink") else _str(data.get("url")),
        author=_str(data.get("author")), published_at=_when(data.get("created_utc")),
        likes=_int(pick(data, "score", "ups")), comments=_int(data.get("num_comments")),
        shares=_int(data.get("num_crossposts")), views=None, duration_sec=None,
        caption=f"{title}\n{body[:500]}".strip(), title=title,
        hashtags=_merge_hashtags([data.get("subreddit") or ""], f"{title} {body}"),
    )


# ---- helpers ---------------------------------------------------------------------------

def _packet(**kwargs: Any) -> TrendPacket:
    platform = kwargs["platform"]
    caption = kwargs.get("caption") or ""
    kwargs.setdefault("hook", _hook(caption))
    kwargs["post_id"] = f"{PLATFORM_SHORT[platform]}_{kwargs['provider_id']}"
    return TrendPacket(**kwargs)


def _items(body: Any) -> Iterable[dict]:
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if not isinstance(body, dict):
        return []
    if "data" in body and isinstance(body["data"], dict) and "children" in body["data"]:  # reddit listing
        return [c for c in body["data"]["children"] if isinstance(c, dict)]
    for key in _LIST_KEYS:
        value = body.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            nested = _items(value)
            if nested:
                return nested
    return []


def pick(item: Any, *paths: str) -> Any:
    for path in paths:
        current: Any = item
        ok = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                ok = False
                break
        if ok and current is not None and current != "":
            return current
    return None


def _str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return None
    return str(value)


def _int(value: Any) -> int:
    parsed = _opt_int(value)
    return parsed if parsed is not None else 0


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().upper().replace(",", "")
    multiplier = 1
    if text.endswith("K"):
        multiplier, text = 1_000, text[:-1]
    elif text.endswith("M"):
        multiplier, text = 1_000_000, text[:-1]
    elif text.endswith("B"):
        multiplier, text = 1_000_000_000, text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return None


def _when(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:  # milliseconds
            value = value / 1000
        return dt.datetime.fromtimestamp(float(value), tz=UTC)
    if isinstance(value, str) and value.isdigit():
        return _when(int(value))
    return parse_iso(str(value))


def _duration(value: Any, *, millis_if_large: bool = False) -> int | None:
    seconds = _opt_int(value) if not isinstance(value, float) else int(value)
    if seconds is None:
        return None
    if millis_if_large and seconds > 1000:
        seconds = seconds // 1000
    return seconds


_ISO_DUR = re.compile(r"PT(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?")


def _iso8601_duration(value: str | None) -> int | None:
    if not value:
        return None
    match = _ISO_DUR.match(value)
    if not match:
        return None
    return int(match.group("h") or 0) * 3600 + int(match.group("m") or 0) * 60 + int(match.group("s") or 0)


def _merge_hashtags(explicit: Iterable[Any], text: str) -> list[str]:
    tags = [str(t).lower().lstrip("#") for t in explicit if t]
    tags += [t.lower() for t in HASHTAG_RE.findall(text or "")]
    return list(dict.fromkeys(t for t in tags if t))


def _hook(caption: str) -> str | None:
    first = SENTENCE_SPLIT.split(caption.strip(), maxsplit=1)[0].strip() if caption else ""
    return first[:140] or None
