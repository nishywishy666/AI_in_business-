"""Paid scan every 2 days (or on the owner's "Refresh now") + off-day recap (spec §10.4, §11.3, §12.2;
the call plan is plan 0013's one-call-per-platform, not the spec §10.3 rotation)."""
from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field

from ..agent import GeminiExhausted, GeminiLadder, SynthesisRejected, SynthesisResult, run_synthesis
from ..cache import LocalCache
from ..clock import Clock, iso, parse_iso, utc_date
from ..config import Settings
from ..context import local_expand
from ..db import RadarStore
from ..packets import Alert, BriefRef, ContextProfile, Credits, FilmThis, LatestPointer, Profile, ScanBrief, TrendPacket
from ..scoring import ScoredLists, score_batch, select_lists
from ..scoring.normalize import normalize_response
from ..scrapers import CreditsExhausted, ScrapeCreatorsClient, ScrapeCreatorsError, endpoint_by_key
from ..scrapers.free_reddit import FreeReddit, default_subreddits
from ..scrapers.free_trends import FreeTrends, TrendSignal
from ..scrapers.free_youtube import FreeYouTube
from ..scrapers.transcripts import TranscriptProvider
from ..usage import refresh_snapshot
from .daily_pull import ContextMissing, daily_pull

log = logging.getLogger(__name__)


@dataclass
class PlannedCall:
    slot: int
    endpoint_key: str
    params: dict = field(default_factory=dict)


@dataclass
class ScanDeps:
    store: RadarStore
    cache: LocalCache
    settings: Settings
    clock: Clock
    scraper: ScrapeCreatorsClient
    youtube: FreeYouTube | None = None
    reddit: FreeReddit | None = None
    trends: FreeTrends | None = None
    gemini: GeminiLadder | None = None
    youtube_transcripts: TranscriptProvider | None = None


@dataclass
class ScanOutcome:
    brief: ScanBrief | None
    scored: list[TrendPacket] = field(default_factory=list)
    planned: list[PlannedCall] = field(default_factory=list)
    live_calls: int = 0
    credits_spent: int = 0
    sources_used: list[str] = field(default_factory=list)
    note: str | None = None
    synthesis: SynthesisResult | None = None


# ---- one live call per platform (plan 0013; replaces the spec §10.3 rotation) -----------
# The owner overrode spec constraint 5 ("max 3 live paid calls per scan") and §10.3's alternating
# third call: every paid scan now pulls TikTok, Instagram, Facebook and YouTube Shorts, and no platform
# is shed to protect the credit reserve. The rotation only decides *which* endpoint carries a
# platform's slot, so consecutive scans do not ask for the same URL and get the 48h cache back.

PLATFORM_SLOTS: tuple[str, ...] = ("tiktok", "instagram", "facebook", "youtube")


def plan_calls(scan_index: int, context: ContextProfile, credits_remaining: int | None, settings: Settings, *,
               free_youtube_covers_shorts: bool = False) -> list[PlannedCall]:
    """One call per platform, in `PLATFORM_SLOTS` order. Credit shedding only drops the calls that
    literally cannot be paid for: unknown balance → all, 0 → none, N < len → the first N.
    `free_youtube_covers_shorts` is accepted for compatibility and ignored — the free YouTube Data
    API never stands in for the Shorts trending feed any more."""
    hashtags = context.hashtags or local_expand(context)
    hashtag = hashtags[(scan_index // 4) % len(hashtags)] if hashtags else None
    keywords = context.keywords or [context.niche]
    keyword = keywords[(scan_index // 4) % max(1, len(keywords))] or context.niche or hashtag or "trending"
    hashtag = hashtag or keyword.replace(" ", "")

    tiktok = (PlannedCall(1, "tiktok_trending"), PlannedCall(1, "tiktok_hashtag", {"hashtag": hashtag}),
              PlannedCall(1, "tiktok_keyword", {"query": keyword}), PlannedCall(1, "tiktok_trending"))[scan_index % 4]
    instagram = (PlannedCall(2, "instagram_hashtag", {"hashtag": hashtag}) if scan_index % 2 == 0
                 else PlannedCall(2, "instagram_reels_trending"))
    facebook = _facebook_call(scan_index, context, settings)
    youtube = PlannedCall(4, "youtube_shorts_trending")
    calls = [c for c in (tiktok, instagram, facebook, youtube) if c is not None]

    calls = calls[:settings.max_live_calls]
    if credits_remaining is None:
        return calls
    if credits_remaining <= 0:
        return []
    return calls[:credits_remaining]


def _facebook_call(scan_index: int, context: ContextProfile, settings: Settings) -> PlannedCall | None:
    """Facebook has no keyword/trending endpoint on ScrapeCreators (spec §10.1 allowlist), so the slot
    is a page's reels or a group's posts: the questionnaire's own URLs first, alternating page/group
    when both exist, else the configured fallback pages. None only when there is nothing to call."""
    pages, groups = list(context.facebook_page_urls), list(context.facebook_group_urls)
    if not pages and not groups:
        pages = list(settings.facebook_fallback_page_urls)
    if pages and groups:
        if scan_index % 2 == 1:
            return PlannedCall(3, "facebook_group_posts", {"url": groups[(scan_index // 2) % len(groups)]})
        return PlannedCall(3, "facebook_page_reels", {"url": pages[(scan_index // 2) % len(pages)]})
    if groups:
        return PlannedCall(3, "facebook_group_posts", {"url": groups[scan_index % len(groups)]})
    if pages:
        return PlannedCall(3, "facebook_page_reels", {"url": pages[scan_index % len(pages)]})
    return None


# ---- paid scan ------------------------------------------------------------------------

def run_paid_scan(deps: ScanDeps, *, fresh: bool = False) -> ScanOutcome:
    """`fresh=True` is the owner's "Refresh now": every platform is fetched live, bypassing the 48h
    request cache. The scheduled scan keeps `fresh=False` and reuses anything fetched recently."""
    now = deps.clock()
    try:
        bundle = daily_pull(deps.store, deps.cache, deps.settings, deps.clock)
    except ContextMissing:
        _context_missing_alert(deps, now)
        return ScanOutcome(brief=None, note="context_missing")
    context = bundle.context
    latest = _latest(deps.store)

    credits = deps.scraper.balance(about_to_scan=True)
    planned = plan_calls(latest.scan_index, context, credits, deps.settings,
                         free_youtube_covers_shorts=bool(deps.youtube and deps.youtube.enabled))

    packets: list[TrendPacket] = []
    sources: list[str] = []
    live_calls = 0
    spent = 0
    for call in planned:
        endpoint = endpoint_by_key(call.endpoint_key)
        try:
            result = deps.scraper.fetch(endpoint, call.params, fresh=fresh)
        except CreditsExhausted:
            log.warning("ScrapeCreators credits exhausted during scan")
            break
        except (ScrapeCreatorsError, ValueError) as exc:
            log.warning("scan call %s failed: %s", call.endpoint_key, exc)
            continue
        if not result.cached:
            live_calls += 1
            spent += result.credits_charged
        sources.append(call.endpoint_key)
        got = normalize_response(endpoint.platform, result.body, source=call.endpoint_key, scraped_at=now)
        if not got and result.body:
            # the call was paid for and answered, but nothing in it parsed — the envelope shape is
            # the prime suspect (CLAUDE.md), and this is the line that says so in the scan log
            log.warning("scan call %s returned a body with no recognisable posts", call.endpoint_key)
        packets.extend(got)

    free_packets, free_sources, trends, free_statuses = _run_free_sources(deps, context)
    packets.extend(free_packets)
    sources.extend(free_sources)

    if not planned and latest.scan_id:
        outcome = run_offday_recap(deps, free_packets=free_packets, free_sources=free_sources, trends=trends,
                                   free_statuses=free_statuses)
        outcome.note = (outcome.note or "") + " paid_scan_skipped_no_credits"
        return outcome
    if not packets:
        refresh_snapshot(deps.store, deps.settings, deps.clock, cache=deps.cache, free_statuses=free_statuses)
        return ScanOutcome(brief=None, planned=planned, live_calls=live_calls, credits_spent=spent,
                           sources_used=sources, note="no_packets")

    scored = score_batch(packets, context, now)
    lists = select_lists(scored, recent_post_ids=_recent_post_ids(deps, now))
    scan_id = _unique_scan_id(deps.store, utc_date(now))
    outcome = _synthesize_and_write(
        deps, context=context, latest=latest, lists=lists, scored=scored, scan_id=scan_id, kind="paid_scan",
        sources=sources, trends=trends, spent=spent, credits_remaining=deps.scraper.cached_credits_remaining(),
        live_calls=live_calls, planned=planned, free_statuses=free_statuses, now=now,
    )
    return outcome


# ---- off-day recap --------------------------------------------------------------------

def run_offday_recap(deps: ScanDeps, *, free_packets: list[TrendPacket] | None = None,
                     free_sources: list[str] | None = None, trends: list[TrendSignal] | None = None,
                     free_statuses: dict[str, str] | None = None) -> ScanOutcome:
    now = deps.clock()
    try:
        bundle = daily_pull(deps.store, deps.cache, deps.settings, deps.clock)
    except ContextMissing:
        _context_missing_alert(deps, now)
        return ScanOutcome(brief=None, note="context_missing")
    context = bundle.context
    latest = _latest(deps.store)
    if not latest.last_paid_scan_id:
        return ScanOutcome(brief=None, note="nothing_to_recap")

    cached_posts = [TrendPacket.model_validate(data) for _, data in
                    deps.store.list(deps.store.paths.posts, where=[("scan_id", "==", latest.last_paid_scan_id)])]
    if free_packets is None:
        free_packets, free_sources, trends, free_statuses = _run_free_sources(deps, context)
    packets = cached_posts + list(free_packets or [])
    if not packets:
        return ScanOutcome(brief=None, note="nothing_to_recap")
    scored = score_batch(packets, context, now)
    recent = _recent_post_ids(deps, now, exclude_scan_id=latest.last_paid_scan_id)
    lists = select_lists(scored, recent_post_ids=recent)
    scan_id = _unique_scan_id(deps.store, f"{utc_date(now)}-recap")
    return _synthesize_and_write(
        deps, context=context, latest=latest, lists=lists, scored=scored, scan_id=scan_id, kind="offday_recap",
        sources=list(free_sources or []), trends=trends, spent=0, credits_remaining=deps.scraper.cached_credits_remaining(),
        live_calls=0, planned=[], free_statuses=free_statuses, now=now,
    )


# ---- shared tail ----------------------------------------------------------------------

def _synthesize_and_write(deps: ScanDeps, *, context: ContextProfile, latest: LatestPointer, lists: ScoredLists,
                          scored: list[TrendPacket], scan_id: str, kind: str, sources: list[str],
                          trends: list[TrendSignal] | None, spent: int, credits_remaining: int | None,
                          live_calls: int, planned: list[PlannedCall], free_statuses: dict[str, str] | None,
                          now: dt.datetime) -> ScanOutcome:
    playbook = [data.get("text", "") for _, data in
                deps.store.list(deps.store.paths.playbook, order_by="created_at", descending=True, limit=10)][::-1]
    synthesis: SynthesisResult | None = None
    note: str | None = None
    if deps.gemini is not None:
        try:
            synthesis = run_synthesis(deps.gemini, context, lists.top_for_gemini, playbook, trends=trends,
                                      recap=(kind == "offday_recap"))
        except GeminiExhausted as exc:
            note = f"gemini_exhausted until {iso(exc.resets_at)}"
        except SynthesisRejected as exc:
            if exc.kind == "unknown_post_id":
                _persist_posts(deps, lists.all_packets(), scan_id=None, cards={})
                refresh_snapshot(deps.store, deps.settings, deps.clock, cache=deps.cache, free_statuses=free_statuses)
                _raise_alert(deps, now, "synthesis", "warning",
                             f"Gemini returned post ids that were not in the scan ({exc.detail}); brief {scan_id} not written.")
                return ScanOutcome(brief=None, scored=scored, planned=planned, live_calls=live_calls,
                                   credits_spent=spent, sources_used=sources, note="synthesis_rejected")
            note = f"synthesis_malformed: {exc.detail[:120]}"
    else:
        note = "no_gemini"

    cards = {c.post_id: c for c in synthesis.output.cards} if synthesis else {}
    film = lists.film_this
    film_why = ""
    if synthesis and synthesis.output.film_this:
        chosen = next((p for p in lists.top_for_gemini if p.post_id == synthesis.output.film_this.post_id), None)
        if chosen is not None:
            film, film_why = chosen, synthesis.output.film_this.why
    elif film is not None:
        film_why = "Highest combined score for your niche this scan."

    _persist_posts(deps, lists.all_packets(), scan_id=scan_id, cards=cards)
    if synthesis:
        for text in synthesis.output.playbook_delta:
            entry_id = f"{now.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
            deps.store.set(deps.store.paths.playbook_entry(entry_id),
                           {"text": text, "scan_id": scan_id, "created_at": iso(now)})

    next_scan_at = (now + dt.timedelta(hours=deps.settings.scan_interval_hours) if kind == "paid_scan"
                    else latest.next_scan_at)
    brief = ScanBrief(
        scan_id=scan_id, generated_at=now, next_scan_at=next_scan_at, kind=kind,
        profile=Profile(niche=context.niche, platforms=context.platforms, goal=context.goal),
        credits=Credits(remaining=credits_remaining, spent_this_scan=spent, reserve=deps.settings.reserve),
        sources_used=list(dict.fromkeys(sources)),
        weekly_take=synthesis.output.weekly_take if synthesis else None,
        patterns=synthesis.output.patterns if synthesis else [],
        ignore=synthesis.output.ignore if synthesis else [],
        **{"global": [_ref(deps, p) for p in lists.global_]},
        niche=[_ref(deps, p) for p in lists.niche],
        film_this=FilmThis(post_id=film.post_id, why=film_why) if film else None,
        playbook_delta=synthesis.output.playbook_delta if synthesis else [],
        model_used=synthesis.model_used if synthesis else None,
        quality=synthesis.quality if synthesis else None,
        note=note,
    )
    deps.store.set(deps.store.paths.scan(scan_id), brief.to_doc())

    pointer = LatestPointer(
        scan_id=scan_id, generated_at=now, next_scan_at=next_scan_at, kind=kind,
        scan_index=latest.scan_index + (1 if kind == "paid_scan" else 0),
        last_paid_scan_at=now if kind == "paid_scan" else latest.last_paid_scan_at,
        last_paid_scan_id=scan_id if kind == "paid_scan" else latest.last_paid_scan_id,
    )
    pointer_doc = pointer.to_doc()
    pointer_doc["spent_last_scan"] = spent if kind == "paid_scan" else (deps.store.get(deps.store.paths.latest) or {}).get("spent_last_scan", 0)
    deps.store.set(deps.store.paths.latest, pointer_doc)

    snapshot = refresh_snapshot(deps.store, deps.settings, deps.clock, cache=deps.cache, free_statuses=free_statuses)
    deps.cache.write_brief(brief)
    deps.cache.write_stats(snapshot)
    return ScanOutcome(brief=brief, scored=scored, planned=planned, live_calls=live_calls, credits_spent=spent,
                       sources_used=brief.sources_used, note=note, synthesis=synthesis)


def _run_free_sources(deps: ScanDeps, context: ContextProfile):
    packets: list[TrendPacket] = []
    sources: list[str] = []
    statuses: dict[str, str] = {}
    trends: list[TrendSignal] | None = None
    if deps.youtube is not None and deps.youtube.enabled:
        try:
            got = deps.youtube.search([*context.keywords[:2]] or [context.niche])
            if got:
                packets.extend(got)
                sources.append("youtube_data_api")
        except Exception as exc:  # free source failures never stop a scan
            log.warning("free YouTube failed: %s", exc)
    if deps.reddit is not None:
        try:
            got = deps.reddit.hot(default_subreddits(context))
            statuses["reddit"] = deps.reddit.last_status
            if got:
                packets.extend(got)
                sources.append("reddit")
        except Exception as exc:
            log.warning("reddit failed: %s", exc)
            statuses["reddit"] = "unknown"
    if deps.trends is not None:
        try:
            trends = deps.trends.signals([context.niche, *context.keywords][:5])
            statuses["google_trends"] = deps.trends.last_status
            if trends:
                sources.append("google_trends")
        except Exception as exc:
            log.warning("trends failed: %s", exc)
            statuses["google_trends"] = "unknown"
    return packets, sources, trends, statuses


def _persist_posts(deps: ScanDeps, packets: list[TrendPacket], *, scan_id: str | None, cards: dict) -> None:
    for p in packets:
        existing = deps.store.get(deps.store.paths.post(p.post_id)) or {}
        update = p.model_copy(update={
            "scan_id": scan_id or existing.get("scan_id"),
            "liked": bool(existing.get("liked", False)),
            "script_id": existing.get("script_id"),
            "transcript": existing.get("transcript"),
        })
        card = cards.get(p.post_id)
        if card is not None:
            update = update.model_copy(update={"why_it_works": card.why_it_works, "format_guess": card.format_guess})
        elif existing.get("why_it_works"):
            update = update.model_copy(update={"why_it_works": existing["why_it_works"],
                                               "format_guess": existing.get("format_guess")})
        deps.store.set(deps.store.paths.post(p.post_id), update.to_doc())


def _ref(deps: ScanDeps, p: TrendPacket) -> BriefRef:
    return BriefRef(post_id=p.post_id, packet_ref=deps.store.paths.packet_ref(p.post_id))


def _latest(store: RadarStore) -> LatestPointer:
    doc = store.get(store.paths.latest)
    return LatestPointer.model_validate(doc) if doc else LatestPointer()


def _unique_scan_id(store: RadarStore, base: str) -> str:
    scan_id, suffix = base, 2
    while store.exists(store.paths.scan(scan_id)):
        scan_id = f"{base}-{suffix}"
        suffix += 1
    return scan_id


def _recent_post_ids(deps: ScanDeps, now: dt.datetime, *, exclude_scan_id: str | None = None) -> set[str]:
    cutoff = now - dt.timedelta(days=deps.settings.dedup_window_days)
    ids: set[str] = set()
    for scan_id, data in deps.store.list(deps.store.paths.scans):
        if scan_id == exclude_scan_id:
            continue
        generated = parse_iso(data.get("generated_at"))
        if generated is None or generated < cutoff:
            continue
        for ref in [*data.get("global", []), *data.get("niche", [])]:
            ids.add(ref.get("post_id"))
        if data.get("film_this"):
            ids.add(data["film_this"].get("post_id"))
    ids.discard(None)
    return ids


def _context_missing_alert(deps: ScanDeps, now: dt.datetime) -> None:
    _raise_alert(deps, now, "context", "critical",
                 "No questionnaire context found for this user. The marketing agent cannot scan until the "
                 "main dashboard stores it.")


def _raise_alert(deps: ScanDeps, now: dt.datetime, provider: str, severity: str, message: str) -> None:
    alert = Alert(alert_id=f"{provider}_{severity}", provider=provider, severity=severity, message=message,
                  created_at=now)
    doc = alert.to_doc()
    doc["updated_at"] = iso(now)
    deps.store.set(deps.store.paths.notification(alert.alert_id), doc)
