"""Prompt builders. Gemini only writes prose over numbers Python already computed."""
from __future__ import annotations

import json

from ..packets import ContextProfile, TrendPacket
from ..scrapers.free_trends import TrendSignal

SYNTHESIS_SCHEMA = {
    "weekly_take": "5-8 sentences on what is working right now",
    "patterns": ["hook/format/length pattern", "...", "..."],
    "ignore": ["thing not to copy", "...", "..."],
    "cards": [{"post_id": "one of the provided ids", "why_it_works": "2 lines", "format_guess": "short label"}],
    "film_this": {"post_id": "one of the provided ids", "why": "why this one, for this niche"},
    "playbook_delta": ["lesson to append", "..."],
}

_RULES = (
    "Rules:\n"
    "- Output JSON only. No markdown fences, no commentary.\n"
    "- Every post_id you mention MUST be one of the provided ids. Never invent a post_id.\n"
    "- Never invent view counts, likes, or any metric, and never claim a platform that is not on the packet.\n"
    "- Prefer TikTok or Instagram for film_this unless a YouTube/Facebook item is clearly stronger.\n"
    "- Write for the user's niche and goal. Be concrete: hooks, formats, lengths, not generic advice.\n"
    "- Return exactly 3 patterns and exactly 3 ignore items. Give a card for every provided id."
)


def _profile_block(context: ContextProfile) -> str:
    return json.dumps({
        "niche": context.niche,
        "keywords": context.keywords,
        "hashtags": context.hashtags,
        "platforms": context.platforms,
        "format": context.format,
        "region": context.region,
        "language": context.language,
        "goal": context.goal,
        "audience": context.audience_line,
    }, ensure_ascii=False)


def packet_summary(p: TrendPacket) -> dict:
    return {
        "post_id": p.post_id,
        "platform": p.platform,
        "author": p.author,
        "published_at": p.published_at.isoformat() if p.published_at else None,
        "likes": p.likes, "comments": p.comments, "shares": p.shares, "views": p.views,
        "duration_sec": p.duration_sec,
        "caption": (p.caption or "")[:280],
        "hook": p.hook,
        "hashtags": p.hashtags[:8],
        "sound": p.sound,
        "scores": {"velocity": p.velocity, "recency": p.recency, "niche_fit": p.niche_fit, "final": p.final},
    }


def synthesis_system(context: ContextProfile) -> str:
    return (
        "You are the marketing trend analyst for a small business creator. You receive scored social posts "
        "(numbers computed deterministically in Python) plus the creator's profile and playbook. "
        "Your job is to explain what is working and what to film next, as strict JSON.\n\n" + _RULES
    )


def synthesis_prompt(context: ContextProfile, packets: list[TrendPacket], playbook: list[str],
                     trends: list[TrendSignal] | None = None, *, recap: bool = False) -> str:
    ids = [p.post_id for p in packets]
    sections = [
        f"MODE: {'off-day recap — nothing new was scraped; say what still holds and refresh the playbook' if recap else 'paid scan — fresh posts'}",
        f"PROFILE: {_profile_block(context)}",
        f"POST_IDS: {json.dumps(ids)}",
        "PACKETS (top scored, best first):",
        json.dumps([packet_summary(p) for p in packets], ensure_ascii=False, indent=1),
    ]
    if trends:
        sections.append("GOOGLE_TRENDS: " + json.dumps(
            [{"keyword": t.keyword, "latest": t.latest, "avg": t.average, "trend": t.label} for t in trends]))
    if playbook:
        sections.append("PLAYBOOK (last entries, newest last):\n- " + "\n- ".join(playbook[-10:]))
    sections.append("Respond with JSON matching this schema exactly:\n" + json.dumps(SYNTHESIS_SCHEMA, indent=1))
    return "\n\n".join(sections)


def repair_prompt(original_prompt: str, bad_text: str, error: str) -> str:
    return (
        original_prompt
        + "\n\nYour previous answer was rejected: " + error
        + "\nPrevious answer (do not repeat its mistakes):\n" + bad_text[:2000]
        + "\n\nReturn corrected JSON only."
    )


def expand_system() -> str:
    return ("PURPOSE: expand\nYou expand a creator profile into 5-8 short hashtags (lowercase, no #, no spaces) "
            "and up to 2 subreddit names. JSON only: {\"hashtags\": [...], \"subreddits\": [...]}")


def expand_prompt(context: ContextProfile) -> str:
    return f"PROFILE: {_profile_block(context)}\nReturn JSON only."


# ---- Like → angles → script (spec §12.3) --------------------------------------------

ANGLES_SCHEMA = {"breakdown": "3-5 sentences: hook, structure, pacing, why it performed",
                 "angles": ["angle 1", "angle 2", "angle 3"]}
SCRIPT_SCHEMA = {"filming_guide": "shot-by-shot guide: setting, framing, on-screen text, length",
                 "script": "spoken lines, ready to read aloud",
                 "caption": "post caption with 3-5 hashtags"}


def angles_system() -> str:
    return (
        "PURPOSE: angles\nYou are a short-form content strategist. Given one post that performed well and the "
        "creator's profile, break down why it works and propose exactly 3 ORIGINAL angles that KEEP THE FORMAT "
        "(hook style, structure, length) but CHANGE THE TOPIC to fit this creator's niche. Never copy the post; "
        "never invent metrics. JSON only, no fences:\n" + json.dumps(ANGLES_SCHEMA, indent=1)
    )


def angles_prompt(context: ContextProfile, packet: TrendPacket, transcript_source: str) -> str:
    return "\n\n".join([
        f"PROFILE: {_profile_block(context)}",
        "POST: " + json.dumps(packet_summary(packet), ensure_ascii=False),
        f"TRANSCRIPT_SOURCE: {transcript_source}",
        "TRANSCRIPT: " + ((packet.transcript or "")[:3000] or "(none — work from the caption and metrics)"),
        "Return JSON only.",
    ])


def script_system() -> str:
    return (
        "PURPOSE: script\nYou write short-form video scripts for one creator. Given the chosen angle and the "
        "reference post's format, write a filming guide, the spoken script (under 120 words unless the format "
        "clearly needs more), and a caption. Keep the format, own the topic. JSON only, no fences:\n"
        + json.dumps(SCRIPT_SCHEMA, indent=1)
    )


def script_prompt(context: ContextProfile, packet: TrendPacket, angle: str) -> str:
    return "\n\n".join([
        f"PROFILE: {_profile_block(context)}",
        "REFERENCE_POST: " + json.dumps(packet_summary(packet), ensure_ascii=False),
        "BREAKDOWN: " + (packet.breakdown or "(none)"),
        f"CHOSEN_ANGLE: {angle}",
        "Return JSON only.",
    ])


# ---- Marketing chat (spec §12.4) ----------------------------------------------------

CHAT_TOOLS = {
    "get_brief": "latest ScanBrief: weekly_take, patterns, global/niche cards, film_this. args: {}",
    "get_post": "one TrendPacket with real metrics and scores. args: {\"post_id\": str}",
    "rewrite_hook": "grounding to rewrite a post's hook for this creator. args: {\"post_id\": str}",
    "captions": "grounding to write EXACTLY 3 caption options. args: {\"post_id\": str}",
    "recommend_tomorrow": "film_this + niche cards + playbook to recommend what to post next. args: {}",
    "like_trend": "run the Like flow (breakdown + 3 angles) on a post. args: {\"post_id\": str}",
}


def chat_system(context: ContextProfile) -> str:
    tools = "\n".join(f"- {name}: {desc}" for name, desc in CHAT_TOOLS.items())
    return (
        "PURPOSE: chat\nYou are the marketing agent inside a small-business dashboard, chatting with the creator "
        "about their latest trend brief. You are grounded ONLY in the brief, posts and tool results provided. "
        "Never invent metrics, posts, or platforms. You cannot scrape or fetch new data; if asked for fresher "
        "data, say when the next scan runs. Answer in the creator's language, concretely.\n\n"
        "Tools you may call (at most one per turn, Python executes it and returns TOOL_RESULT):\n" + tools +
        "\n\nAlways answer with JSON only, no fences:\n"
        '{"reply": "your message to the creator, or empty if you are calling a tool", '
        '"tool": null | {"name": "tool_name", "args": {...}}}\n'
        "When a tool result is present, write the final reply and set tool to null. "
        "For captions return exactly 3 options in the reply. Profile: " + _profile_block(context)
    )


def chat_prompt(history: list[dict], grounding: dict, user_message: str, tool_result: dict | None) -> str:
    lines = ["GROUNDING: " + json.dumps(grounding, ensure_ascii=False)]
    if history:
        lines.append("HISTORY:\n" + "\n".join(f"{m['role'].upper()}: {m['content']}" for m in history))
    lines.append(f"USER: {user_message}")
    if tool_result is not None:
        lines.append("TOOL_RESULT: " + json.dumps(tool_result, ensure_ascii=False, default=str)[:6000])
    lines.append("Return JSON only.")
    return "\n\n".join(lines)
