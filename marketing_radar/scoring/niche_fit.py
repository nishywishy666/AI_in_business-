from __future__ import annotations

import re

from ..packets import ContextProfile, TrendPacket

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = {"the", "and", "for", "with", "your", "you", "this", "that", "from", "are", "our", "how",
              "who", "what", "when", "why", "into", "than", "then", "them", "they", "will", "just", "have"}


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if len(t) > 2 and t not in _STOPWORDS}


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def niche_fit(packet: TrendPacket, context: ContextProfile) -> float:
    """0.0 without text; 1.0 on any exact hashtag/seed-phrase hit; else overlap of tokens."""
    text = f"{packet.caption} {' '.join(packet.hashtags)} {packet.title or ''}".strip()
    if not text:
        return 0.0
    post_tags = {_compact(t) for t in packet.hashtags}
    seed_tags = {_compact(t) for t in context.hashtags if t}
    seed_phrases = {_compact(k) for k in [context.niche, *context.keywords] if k}
    if post_tags & seed_tags:
        return 1.0
    compact_text = _compact(text)
    if any(phrase and phrase in compact_text for phrase in seed_phrases | seed_tags):
        return 1.0
    post_tokens = _tokens(text)
    seed_tokens: set[str] = set()
    for term in context.seed_terms:
        seed_tokens |= _tokens(term)
    if not post_tokens or not seed_tokens:
        return 0.0
    overlap = len(post_tokens & seed_tokens)
    return max(0.0, min(1.0, overlap / min(len(seed_tokens), 4)))
