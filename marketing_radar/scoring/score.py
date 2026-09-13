"""AIOS scoring, ported from previous_work tools/idea-scout/score-ideas.py (spec §11).

Differences that are required: no Higgsfield producibility term, relative velocity always on,
multiplied by niche_fit and platform_weight.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from ..packets import ContextProfile, TrendPacket
from .niche_fit import niche_fit

SOFT_THRESHOLD = 0.6
SECONDARY_WIN_MARGIN = 0.15


def hours_since(published_at: dt.datetime | None, now: dt.datetime) -> float | None:
    if published_at is None:
        return None
    return max(0.5, (now - published_at).total_seconds() / 3600.0)


def raw_per_hour(likes: int, comments: int, shares: int | None, hours: float | None) -> float:
    if hours is None or hours <= 0:
        return 0.0
    weighted = int(likes or 0) + 3 * int(comments or 0) + 5 * int(shares or 0)
    return max(0.0, weighted / hours)


def recency_weight(published_at: dt.datetime | None, now: dt.datetime) -> float:
    hours = hours_since(published_at, now)
    if hours is None:
        return 0.5
    return max(0.3, 1.0 - (hours / 24.0) / 14.0)


def platform_weight(platform: str) -> float:
    return 1.0 if platform in ("tiktok", "instagram") else 0.85


def relative_velocity(raws: list[float]) -> list[float]:
    n = len(raws)
    if n == 0:
        return []
    if n == 1:
        return [1.0]
    out = []
    for r in raws:
        lower = sum(1 for v in raws if v < r)
        equal = sum(1 for v in raws if v == r)
        pct = (lower + 0.5 * (equal - 1)) / (n - 1)
        out.append(round(max(0.0, min(1.0, pct)), 4))
    return out


def score_batch(packets: list[TrendPacket], context: ContextProfile, now: dt.datetime) -> list[TrendPacket]:
    """Score one scan's combined batch in place-ish (returns new packet copies)."""
    prepared: list[TrendPacket] = []
    for p in packets:
        hours = hours_since(p.published_at, now)
        raw = raw_per_hour(p.likes, p.comments, p.shares, hours)
        prepared.append(p.model_copy(update={
            "hours_since_post": round(hours, 2) if hours is not None else None,
            "raw_per_hour": round(raw, 2),
            "recency": round(recency_weight(p.published_at, now), 4),
            "niche_fit": round(niche_fit(p, context), 4),
            "platform_weight": platform_weight(p.platform),
        }))
    velocities = relative_velocity([p.raw_per_hour for p in prepared])
    scored = []
    for p, v in zip(prepared, velocities):
        final = v * p.niche_fit * p.recency * p.platform_weight
        scored.append(p.model_copy(update={"velocity": v, "final": round(final, 4)}))
    return dedup_keep_best(scored)


def dedup_keep_best(packets: list[TrendPacket]) -> list[TrendPacket]:
    best: dict[tuple[str, str], TrendPacket] = {}
    for p in packets:
        key = (p.platform, p.provider_id)
        current = best.get(key)
        if current is None or p.final > current.final:
            best[key] = p
    return sorted(best.values(), key=lambda p: p.final, reverse=True)


@dataclass
class ScoredLists:
    global_: list[TrendPacket] = field(default_factory=list)
    niche: list[TrendPacket] = field(default_factory=list)
    film_this: TrendPacket | None = None
    top_for_gemini: list[TrendPacket] = field(default_factory=list)
    weak_niche: bool = False

    def all_packets(self) -> list[TrendPacket]:
        seen: dict[str, TrendPacket] = {}
        for p in [*self.global_, *self.niche, *([self.film_this] if self.film_this else []), *self.top_for_gemini]:
            seen.setdefault(p.post_id, p)
        return list(seen.values())


def select_lists(scored: list[TrendPacket], *, recent_post_ids: set[str] | None = None,
                 top_n_for_gemini: int = 15) -> ScoredLists:
    recent = recent_post_ids or set()
    fresh = [p for p in scored if p.post_id not in recent]
    pool = fresh or scored

    def primary_first(p: TrendPacket) -> tuple[float, int]:
        return (p.velocity, 1 if p.is_primary_platform else 0)

    global_list = sorted(pool, key=primary_first, reverse=True)[:3]

    niche_pool = [p for p in pool if p.niche_fit >= 0.5]
    weak = len(niche_pool) < 3
    if weak:
        niche_pool = sorted(pool, key=lambda p: p.final, reverse=True)
    niche_list = sorted(niche_pool, key=lambda p: p.final, reverse=True)[:5]

    film = _film_this_candidate(pool)
    if film is None and scored:
        film = _film_this_candidate(scored)

    top = sorted(pool, key=lambda p: p.final, reverse=True)[:top_n_for_gemini]
    for p in [*global_list, *niche_list, *([film] if film else [])]:
        if all(x.post_id != p.post_id for x in top):
            top.append(p)
    return ScoredLists(global_=global_list, niche=niche_list, film_this=film, top_for_gemini=top, weak_niche=weak)


def _film_this_candidate(pool: list[TrendPacket]) -> TrendPacket | None:
    if not pool:
        return None
    primary = [p for p in pool if p.is_primary_platform]
    secondary = [p for p in pool if not p.is_primary_platform]
    best_primary = max(primary, key=lambda p: p.final, default=None)
    best_secondary = max(secondary, key=lambda p: p.final, default=None)
    if best_primary is None:
        return best_secondary
    if best_secondary is not None and best_secondary.final - best_primary.final >= SECONDARY_WIN_MARGIN:
        return best_secondary
    return best_primary
