"""The Overlord: answers the owner's questions from what the two agents already wrote.

Grounding only — it never scrapes, never triggers a scan, never reads Firestore outside what the
dashboard already loaded (OVERLORD.md). Gemini phrases the answer from a JSON packet computed in
Python: the call analytics, the latest scan, and (plan 0013) the business's own database — menu,
facts, bookings, callbacks, recent calls, unanswered questions — so "what toasties do we sell" or
"who is waiting on a call back" is answered from the same records the screens show. With no key (or
on any failure) a deterministic template answers from the same packet, so the bubble always replies.
Marketing answers cite the scan id; limits say whether they reset (Gemini/YouTube: midnight Pacific)
or never do (ScrapeCreators).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

log = logging.getLogger(__name__)

SYSTEM = """You are the Overlord, the owner's assistant for {business_name}, a small restaurant. Answer the owner's question using ONLY the DATA JSON supplied. Rules:
- Two to five plain sentences, spoken register, no markdown, no bullet points, no headings.
- Quote the numbers exactly as given. If a value is null or missing, say it is not available yet — never guess.
- DATA.knowledge is the business's own database: the confirmed menu (every item with price, description, dietary tags and confirmed allergens), the facts callers are told (hours, address, parking, delivery, catering and so on), bookings, callbacks with the callers' phone numbers, the recent call log with one-line summaries, and the questions the receptionist could not answer. Use it for anything about the business itself. DATA.knowledge.lookup, when present, is the receptionist's own answer to this exact question — prefer it.
- When asked for a list (menu items, callbacks, bookings), name every relevant entry, in flowing sentences.
- For anything about trends, content or marketing, start with "From scan <scan_id>" and say the numbers are from that scan, not a live scrape.
- If you mention a usage limit, say whether it resets (Gemini and YouTube reset at midnight Pacific) or never resets (ScrapeCreators credits).
- Do not invent menu items, bookings, calls, questions, or trends that are not in DATA."""

MENU_WORDS = ("menu", "toastie", "toasties", "sandwich", "sandwiches", "dish", "dishes", "food", "eat", "price",
              "prices", "cost", "sell", "serve", "offer", "items", "what do you have", "what do we have",
              "what do you do", "what's on", "whats on")
# a question about counts, a period, or the receptionist's records is an analytics question even if it
# also names a fact word ("who is waiting on a callback" must not become the wait-time fact)
COUNT_WORDS = ("how many", "number of", "count", "today", "this week", "last week", "yesterday", "took",
               "handled", "did the ai", "did the receptionist")
RECORD_WORDS = ("callback", "call back", "waiting", "booking", "cover", "question", "asking", "calling about",
                "unanswered", "calls")


class OverlordTransport(Protocol):
    model_id: str

    async def generate(self, *, system: str, prompt: str, timeout_s: float) -> str: ...


class GenAiOverlordTransport:
    """google-genai with a longer output budget than the voice answerer (a few sentences, and
    thinking models spend output tokens before the answer). Model id resolved like the voice path."""

    def __init__(self, api_key: str, model_id: str, *, client: Any = None) -> None:
        self.api_key, self.model_id, self._client = api_key, model_id, client

    @classmethod
    def resolve(cls, api_key: str) -> "GenAiOverlordTransport":
        from google import genai

        from config import thresholds
        from services.voice.answerer import resolve_model

        client = genai.Client(api_key=api_key)
        available = {m.name.split("/")[-1] for m in client.models.list()}
        return cls(api_key, resolve_model(available, thresholds()["GEMINI_MODEL_PREFERENCE"]), client=client)

    async def generate(self, *, system: str, prompt: str, timeout_s: float) -> str:
        from google import genai
        from google.genai import types

        if self._client is None:
            self._client = genai.Client(api_key=self.api_key)
        config = types.GenerateContentConfig(system_instruction=system, temperature=0.3, max_output_tokens=1024)
        response = await self._client.aio.models.generate_content(model=self.model_id, contents=prompt, config=config)
        return response.text or ""


def build_packet(*, business_name: str, voice_today: dict, voice_week: dict, marketing: dict | None,
                 knowledge: dict | None = None) -> dict[str, Any]:
    packet: dict[str, Any] = {"business": business_name, "voice": {"today": voice_today, "this_week": voice_week}}
    if knowledge is not None:
        packet["knowledge"] = knowledge
    if marketing is None:
        packet["marketing"] = {"scan_id": None, "note": "no scan has run yet"}
    elif marketing.get("empty"):
        packet["marketing"] = {"scan_id": None, "note": "no brief yet", "stats": marketing.get("stats")}
    else:
        packet["marketing"] = {
            "scan_id": marketing.get("scan_id"), "generated_at": marketing.get("generated_at"), "kind": marketing.get("kind"),
            "weekly_take": marketing.get("weekly_take"), "patterns": marketing.get("patterns"),
            "film_this": marketing.get("film_this"),
            "niche_top": [{"hook": c.get("hook"), "platform": c.get("platform"), "why": c.get("why_it_works")} for c in marketing.get("niche") or []],
            "global_top": [{"hook": c.get("hook"), "platform": c.get("platform"), "why": c.get("why_it_works")} for c in marketing.get("global") or []],
            "credits": marketing.get("credits"), "saved_scripts": marketing.get("saved_scripts"),
            "alerts": marketing.get("alerts"), "stats": marketing.get("stats"), "source_note": marketing.get("source_note"),
        }
    return packet


async def answer(question: str, packet: dict[str, Any], *, transport: OverlordTransport | None,
                 timeout_s: float = 8.0) -> dict[str, Any]:
    fallback = template_answer(question, packet)
    if transport is None:
        return {"answer": fallback, "model": None, "grounded_on": _sources(packet)}
    prompt = f"DATA: {json.dumps(packet, ensure_ascii=False, default=str)}\nOWNER ASKED: {question}\nAnswer:"
    try:
        text = await asyncio.wait_for(transport.generate(system=SYSTEM.format(business_name=packet.get("business") or "the business"),
                                                         prompt=prompt, timeout_s=timeout_s), timeout_s)
        text = " ".join((text or "").split())
        if not text:
            raise ValueError("empty reply")
        return {"answer": text, "model": getattr(transport, "model_id", None), "grounded_on": _sources(packet)}
    except Exception as exc:
        log.warning("overlord fallback: %s", exc)
        return {"answer": fallback, "model": None, "grounded_on": _sources(packet), "warning": f"template fallback: {type(exc).__name__}"}


def _sources(packet: dict) -> dict:
    out: dict[str, Any] = {"scan_id": (packet.get("marketing") or {}).get("scan_id"), "voice_periods": ["today", "this week"]}
    knowledge = packet.get("knowledge") or {}
    if knowledge:
        out["knowledge"] = [k for k in ("menu", "facts", "bookings", "callbacks", "recent_calls", "unanswered_questions")
                            if knowledge.get(k)]
    return out


def _menu_line(knowledge: dict) -> str:
    items = knowledge.get("menu") or []
    bits = []
    for item in items:
        desc = f" — {item['description']}" if item.get("description") else ""
        bits.append(f"{item['name']} ({item.get('price')}){desc}")
    name = knowledge.get("business_name") or "The business"
    return f"{name} has {len(items)} confirmed menu item{'s' if len(items) != 1 else ''}: " + "; ".join(bits) + "."


def template_answer(question: str, packet: dict[str, Any]) -> str:
    """Deterministic answer when no model is available. Same numbers, plainer words."""
    q = (question or "").lower()
    today, week = packet["voice"]["today"], packet["voice"]["this_week"]
    marketing = packet.get("marketing") or {}
    knowledge = packet.get("knowledge") or {}
    if any(w in q for w in ("trend", "post", "film", "content", "marketing", "video", "tiktok", "instagram")):
        if not marketing.get("scan_id"):
            return "No trend scan has landed yet, so there is nothing to recommend. The first scan is scheduled; check the Marketing screen once it runs."
        top = (marketing.get("niche_top") or [None])[0]
        film = marketing.get("film_this") or {}
        lead = f"From scan {marketing['scan_id']}: "
        if film.get("hook") or film.get("why"):
            lead += f"the one to film is \"{film.get('hook') or film.get('post_id')}\" — {film.get('why') or 'strongest niche-fit velocity'}. "
        elif top:
            lead += f"the top niche trend is \"{top.get('hook')}\" on {top.get('platform')}. "
        take = marketing.get("weekly_take")
        if take:
            lead += take.split(". ")[0].rstrip(".") + ". "
        credits = marketing.get("credits") or {}
        if credits.get("remaining") is not None:
            lead += f"{credits['remaining']} ScrapeCreators credits remain, and those never reset."
        return lead.strip()
    # the business itself — menu, hours, parking, delivery… — unless it is really about the records
    if knowledge and not any(w in q for w in COUNT_WORDS + RECORD_WORDS):
        if knowledge.get("menu") and any(w in q for w in MENU_WORDS):
            return _menu_line(knowledge)
        lookup = knowledge.get("lookup") or {}
        if lookup.get("answer"):
            return str(lookup["answer"])
    if "booking" in q or "cover" in q or "table" in q:
        return (f"Today the receptionist took {today.get('bookings', 0)} bookings for {today.get('covers', 0)} covers; "
                f"this week it is {week.get('bookings', 0)} bookings for {week.get('covers', 0)} covers, "
                f"versus {week.get('bookings_previous', 0)} the week before.")
    if "callback" in q or "waiting" in q or "call back" in q:
        opens = [c for c in (knowledge.get("callbacks") or []) if c.get("status") != "done"] or today.get("open_callbacks") or []
        if not opens:
            return "Nobody is waiting on a callback right now."
        parts = []
        for c in opens[:5]:
            who = c.get("name") or "a caller"
            if c.get("phone"):
                who += f" on {c['phone']}"
            parts.append(f"{who} asked \"{c.get('question') or c.get('reason')}\"")
        more = f", and {len(opens) - 5} more" if len(opens) > 5 else ""
        return (f"{len(opens)} callback{'s are' if len(opens) != 1 else ' is'} open: " + "; ".join(parts) + more
                + ". Open the Callbacks screen to see each call's transcript and mark them done.")
    if "question" in q or "ask" in q or "calling about" in q or "unanswered" in q:
        routes = week.get("by_route") or {}
        order = ", ".join(f"{k.lower()}s {v}" for k, v in sorted(routes.items(), key=lambda kv: -kv[1])) or "no calls yet"
        gaps = week.get("unanswered_questions") or []
        gap_line = (f" The most-asked unanswered question is \"{gaps[0]['question']}\" ({gaps[0]['asked_by_calls']} calls) — worth confirming."
                    if gaps else " Nothing has gone unanswered this week.")
        return f"This week by route: {order}.{gap_line}"
    return (f"Today: {today.get('calls', 0)} calls, {today.get('bookings', 0)} bookings, containment {today.get('containment') or 'no data'}. "
            f"This week: {week.get('calls', 0)} calls and {week.get('bookings', 0)} bookings. "
            + (f"Latest trend scan: {marketing['scan_id']}." if marketing.get("scan_id") else "No trend scan yet."))
