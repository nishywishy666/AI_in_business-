"""The five lookups and two writes (vr_plan.md §10), plus the load-once business context.

Python functions called by the state machines and the answer path. They are never exposed to
Gemini and the Groq router never selects them. Every read honours V9: unconfirmed data is unknown.
"""
from __future__ import annotations

import datetime as dt
import difflib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from config import load_yaml
from config.disclaimers import CROSS_CONTACT
from contracts.voice import CallbackReason
from services.common.firestore import BusinessPaths

from .sinks import CallSink

log = logging.getLogger(__name__)
AnswerSource = Literal["menu", "fact", "allergen", "not_found", "template"]


# ---- read side -------------------------------------------------------------------------------

class BusinessReader(Protocol):
    def get_doc(self, path: str) -> dict | None: ...

    def list_docs(self, collection_path: str, *, where: list[tuple[str, str, Any]] | None = None) -> list[tuple[str, dict]]: ...


class FirestoreReader:
    def __init__(self, client: Any) -> None:
        self.client = client

    def get_doc(self, path: str) -> dict | None:
        snap = self.client.document(path).get()
        return snap.to_dict() if snap.exists else None

    def list_docs(self, collection_path: str, *, where=None) -> list[tuple[str, dict]]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        query = self.client.collection(collection_path)
        for name, op, value in where or ():
            query = query.where(filter=FieldFilter(name, op, value))
        return [(snap.id, snap.to_dict() or {}) for snap in query.stream()]


class MemoryReader:
    """Test/offline reader over a {path: doc} dict. Same shapes as Firestore."""

    def __init__(self, docs: dict[str, dict] | None = None) -> None:
        self.docs = dict(docs or {})

    def get_doc(self, path: str) -> dict | None:
        doc = self.docs.get(path)
        return dict(doc) if doc is not None else None

    def list_docs(self, collection_path: str, *, where=None) -> list[tuple[str, dict]]:
        prefix = collection_path.rstrip("/") + "/"
        rows = []
        for path, doc in self.docs.items():
            if not path.startswith(prefix) or "/" in path[len(prefix):]:
                continue
            if all(_match(doc, clause) for clause in (where or ())):
                rows.append((path[len(prefix):], dict(doc)))
        return rows


class JsonBusinessReader(MemoryReader):
    """Offline reader over a dataset folder (`data/business/<name>/{menu_items,facts}.json`) plus
    capacity slots materialised in memory from config/capacity.yaml windows. Same shapes as Firestore,
    so the simulator and the dashboard answer from the real business data with zero keys (plan 0005).
    Keys starting with "_" (readme notes) are skipped."""

    def __init__(self, dataset_dir: Any, business_id: str, *, windows: list[dict] | None = None,
                 slot_days: int = 60, today: dt.date | None = None) -> None:
        from pathlib import Path

        folder = Path(dataset_dir)
        paths = BusinessPaths(business_id)
        docs: dict[str, dict] = {}
        for item_id, doc in _load_json(folder / "menu_items.json").items():
            docs[paths.menu_item(item_id)] = doc
        for key, doc in _load_json(folder / "facts.json").items():
            docs[paths.fact(key)] = doc
        if windows:
            from services.booking.capacity import materialise_slots, parse_windows

            start = today or dt.date.today()
            for slot_id, doc in materialise_slots(parse_windows(windows), start, slot_days, business_id).items():
                docs[paths.capacity_slot(slot_id)] = doc
        super().__init__(docs)
        self.dataset_dir = folder
        self.business_id = business_id


def _load_json(path: Any) -> dict:
    import json

    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not str(k).startswith("_")}


def _match(doc: dict, clause: tuple[str, str, Any]) -> bool:
    name, op, value = clause
    current = doc.get(name)
    return {"==": current == value, "!=": current != value}.get(op, False)


# ---- load-once business context ---------------------------------------------------------------

@dataclass
class MenuItem:
    item_id: str
    name: str
    section: str | None
    price_cents: int | None
    dietary_tags: list[str]
    allergens: dict[str, dict]
    doc: dict

    @property
    def price_spoken(self) -> str | None:
        if self.price_cents is None:
            return None
        dollars, cents = divmod(int(self.price_cents), 100)
        return f"${dollars}.{cents:02d}"


@dataclass
class BusinessContext:
    business_id: str
    business_name: str
    items: list[MenuItem]
    fact_keys: set[str]
    windows: list[dict] = field(default_factory=list)
    loaded_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    @property
    def sections(self) -> dict[str, list[MenuItem]]:
        out: dict[str, list[MenuItem]] = {}
        for item in self.items:
            out.setdefault((item.section or "menu").lower(), []).append(item)
        return out

    def find_item(self, text: str) -> MenuItem | None:
        """Fuzzy name match: exact/substring first, then difflib over item names."""
        haystack = _norm(text)
        if not haystack:
            return None
        for item in sorted(self.items, key=lambda i: -len(i.name)):
            if _norm(item.name) and _norm(item.name) in haystack:
                return item
        names = {_norm(i.name): i for i in self.items}
        tokens = haystack.split()
        candidates: list[tuple[float, MenuItem]] = []
        for norm_name, item in names.items():
            ratio = difflib.SequenceMatcher(None, norm_name, haystack).ratio()
            name_tokens = set(norm_name.split())
            overlap = len(name_tokens & set(tokens)) / max(1, len(name_tokens))
            score = max(ratio, overlap)
            if score >= 0.6:
                candidates.append((score, item))
        return max(candidates, key=lambda c: c[0])[1] if candidates else None

    def find_section(self, text: str) -> str | None:
        haystack = _norm(text)
        for section in self.sections:
            if section and section in haystack:
                return section
        return None


def load_business_context(reader: BusinessReader, business_id: str, *, windows: list[dict] | None = None) -> BusinessContext:
    """One read of confirmed menu items + fact keys per call (§3, §10). Never per turn."""
    paths = BusinessPaths(business_id)
    items = []
    for item_id, doc in reader.list_docs(paths.menu_items, where=[("confirmedByOwner", "==", True)]):
        if doc.get("confirmedByOwner") is not True:
            continue
        price = doc.get("priceCents")
        items.append(MenuItem(
            item_id=item_id, name=str(doc.get("name") or item_id), section=doc.get("section"),
            price_cents=int(price) if isinstance(price, (int, float)) and not isinstance(price, bool) else None,
            dietary_tags=[str(t) for t in (doc.get("dietaryTags") or [])],
            allergens={str(k): (v if isinstance(v, dict) else {}) for k, v in (doc.get("allergens") or {}).items()},
            doc=doc,
        ))
    fact_keys = {fact_id for fact_id, _ in reader.list_docs(paths.facts)}
    # TODO(spec): the runtime source of {business_name} is not stated; facts/business_name is read if present.
    name_doc = reader.get_doc(paths.fact("business_name")) if "business_name" in fact_keys else None
    business_name = str(_fact_value(name_doc) or business_id)
    return BusinessContext(business_id=business_id, business_name=business_name, items=items, fact_keys=fact_keys,
                           windows=list(windows or []))


# ---- the five lookups ---------------------------------------------------------------------------

@dataclass
class Lookup:
    tool: str
    args: dict[str, Any]
    source: AnswerSource
    payload: dict | None = None
    reason: CallbackReason | None = None
    template: str | None = None  # deterministic phrasing used when Gemini is unavailable


def get_section_items(ctx: BusinessContext, section: str) -> Lookup:
    """≤ 3 items + a remaining count (Rule 7)."""
    items = ctx.sections.get(section.lower(), [])
    if not items:
        return Lookup("get_section_items", {"section": section}, "not_found", reason="no_data")
    shown = items[:3]
    payload = {"section": section, "items": [{"name": i.name, "price": i.price_spoken} for i in shown],
               "remaining_count": max(0, len(items) - 3)}
    names = ", ".join(i.name for i in shown)
    from . import templates

    return Lookup("get_section_items", {"section": section}, "menu", payload,
                  template=templates.MENU_SECTION.format(section=section, items=names))


def lookup_menu_item(ctx: BusinessContext, name: str) -> Lookup:
    item = ctx.find_item(name)
    if item is None:
        return Lookup("lookup_menu_item", {"name": name}, "not_found", reason="no_data")
    from . import templates

    if item.price_cents is None:
        return Lookup("lookup_menu_item", {"name": name}, "not_found", reason="no_data",
                      payload={"name": item.name, "price": None},
                      template=templates.MENU_ITEM_NO_PRICE.format(name=item.name))
    payload = {"name": item.name, "section": item.section, "price": item.price_spoken,
               "price_cents": item.price_cents, "dietary_tags": item.dietary_tags,
               "description": item.doc.get("description")}
    return Lookup("lookup_menu_item", {"name": name}, "menu", payload,
                  template=templates.MENU_ITEM_PRICE.format(name=item.name, price=item.price_spoken))


def get_item_allergens(ctx: BusinessContext, name: str, allergen: str | None = None) -> Lookup:
    """Only entries with confirmed == true count. Anything else is unknown → callback (Rule 2)."""
    item = ctx.find_item(name)
    args = {"name": name, "allergen": allergen}
    if item is None:
        return Lookup("get_item_allergens", args, "not_found", reason="no_data")
    confirmed = {aid: entry for aid, entry in item.allergens.items() if entry.get("confirmed") is True}
    if allergen:
        aid = _allergen_id(allergen)
        entry = confirmed.get(aid) if aid else None
        if entry is None:
            return Lookup("get_item_allergens", args, "not_found", reason="allergen_unknown",
                          payload={"name": item.name, "allergen": allergen})
        statuses = {aid: entry.get("status")}
    else:
        if not confirmed:
            return Lookup("get_item_allergens", args, "not_found", reason="allergen_unknown",
                          payload={"name": item.name})
        statuses = {aid: entry.get("status") for aid, entry in confirmed.items()}
    payload = {"name": item.name, "allergens": statuses, "cross_contact": CROSS_CONTACT}
    from . import templates

    spoken = "; ".join(f"{_allergen_label(a)}: {s}" for a, s in statuses.items())
    return Lookup("get_item_allergens", args, "allergen", payload,
                  template=templates.ALLERGEN_STATUS.format(name=item.name, statuses=spoken))


def get_business_fact(reader: BusinessReader, business_id: str, fact_key: str) -> Lookup:
    """One document get. Missing document = unknown → callback (V9)."""
    doc = reader.get_doc(BusinessPaths(business_id).fact(fact_key))
    value = _fact_value(doc)
    if doc is None or value in (None, ""):
        return Lookup("get_business_fact", {"fact_key": fact_key}, "not_found", reason="no_data")
    from . import templates

    template = (templates.FACT_HOURS.format(hours=value, day="today") if fact_key == "hours"
                else templates.FACT_GENERIC.format(value=value))
    return Lookup("get_business_fact", {"fact_key": fact_key}, "fact",
                  {"fact_key": fact_key, "value": value, "doc": {k: v for k, v in doc.items() if k != "value"}},
                  template=template)


@dataclass
class Availability:
    slot_id: str
    open: bool
    seats_total: int | None
    seats_booked: int | None
    alternatives: list[str] = field(default_factory=list)
    exists: bool = True


def slot_id_for(date: dt.date, time: dt.time) -> str:
    return f"{date.isoformat()}T{time.strftime('%H%M')}"


def check_availability(reader: BusinessReader, business_id: str, date: dt.date, time: dt.time,
                       party_size: int) -> Availability:
    """Reads the denormalised counter on the slot document, never Calendar (V8).
    Alternatives are filled in by services/booking/capacity.py (Phase 4)."""
    slot_id = slot_id_for(date, time)
    doc = reader.get_doc(BusinessPaths(business_id).capacity_slot(slot_id))
    if doc is None:
        return Availability(slot_id, False, None, None, exists=False)
    total, booked = int(doc.get("seatsTotal") or 0), int(doc.get("seatsBooked") or 0)
    return Availability(slot_id, booked + party_size <= total, total, booked)


# ---- question → lookup (the mapping §10 leaves to Python) ---------------------------------------

_SYN = load_yaml("fact_synonyms.yaml")
_ALLERGEN_CFG = load_yaml("allergens.yaml").get("allergens", [])


def answer_question(question: str, ctx: BusinessContext, reader: BusinessReader) -> Lookup:
    q = _norm(question)
    allergen_word = next((w for w in _SYN.get("allergen_words", []) if _norm(w) in q), None)
    item = ctx.find_item(question)
    if allergen_word and item is not None:
        return get_item_allergens(ctx, item.name, _allergen_from_question(q))
    if item is not None:
        return lookup_menu_item(ctx, item.name)
    section = ctx.find_section(question)
    if section:
        return get_section_items(ctx, section)
    for fact_key, synonyms in (_SYN.get("facts") or {}).items():
        if any(_norm(s) in q for s in synonyms):
            if fact_key in ctx.fact_keys:
                return get_business_fact(reader, ctx.business_id, fact_key)
            return Lookup("get_business_fact", {"fact_key": fact_key}, "not_found", reason="no_data")
    if allergen_word:
        return Lookup("get_item_allergens", {"name": None, "allergen": _allergen_from_question(q)}, "not_found",
                      reason="allergen_unknown")
    if any(_norm(w) in q for w in _SYN.get("section_words", [])) and ctx.sections:
        first = next(iter(ctx.sections))
        return get_section_items(ctx, first)
    return Lookup("none", {"question": question}, "not_found", reason="no_data")


# ---- writes ------------------------------------------------------------------------------------

def take_callback(sink: CallSink, *, call_id: str, business_id: str, name: str | None, phone: str | None,
                  question: str | None, reason: CallbackReason, now: dt.datetime | None = None) -> str:
    """callbacks/{auto}. Owner email is sent by Phase 4's mailer; the doc is the open task."""
    now = now or dt.datetime.now(dt.timezone.utc)
    doc = {"callId": call_id, "businessId": business_id, "name": name, "phone": phone, "question": question,
           "reason": reason, "status": "open", "createdAt": now.isoformat()}
    callback_id = sink.write_callback(doc)
    log.info("callback taken", extra={"call_id": call_id, "reason": reason, "callback_id": callback_id})
    return callback_id


# ---- helpers -----------------------------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).strip()


def _fact_value(doc: dict | None) -> Any:
    if not doc:
        return None
    for key in ("value", "text", "answer"):
        if doc.get(key) not in (None, ""):
            return doc[key]
    return None


def _allergen_id(word: str) -> str | None:
    w = _norm(word)
    for entry in _ALLERGEN_CFG:
        names = {entry["id"], entry.get("label", ""), *(entry.get("includes") or [])}
        if any(_norm(n) and (_norm(n) in w or w in _norm(n)) for n in names if n):
            return entry["id"]
    aliases = {"dairy": "milk", "nuts": "tree_nut", "nut": "tree_nut", "shellfish": "crustacea", "prawn": "crustacea",
               "prawns": "crustacea", "wheat": "gluten", "coeliac": "gluten", "celiac": "gluten"}
    return aliases.get(w)


def _allergen_label(allergen_id: str) -> str:
    for entry in _ALLERGEN_CFG:
        if entry["id"] == allergen_id:
            return entry.get("label", allergen_id)
    return allergen_id


def _allergen_from_question(q: str) -> str | None:
    for token in q.split():
        if _allergen_id(token):
            return token
    return None
