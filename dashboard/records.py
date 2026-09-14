"""Normalised read model over what the voice receptionist writes (vr_plan.md §11), plus the three
dashboard-owned writes (callback status, knowledge-gap review, settings).

Two sources, same entities: `LocalJsonlSource` replays `.testruns/*.jsonl` (what `LocalJsonlSink`
writes); `FirestoreSource` reads `businesses/{businessId}/**`. Pydantic at the boundary (R5): a
malformed record is dropped, never a 500. Everything is typed so `analytics.py` stays pure.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from services.common.firestore import BusinessPaths

from .packet import PACKET_VERSION, PacketStore, _json_default, upsert

log = logging.getLogger(__name__)
MIRROR_MAX_BYTES = 900_000  # Firestore caps a document at 1 MiB; leave room for the envelope

UTC = dt.timezone.utc
DASHBOARD_LOG = "_dashboard"


def parse_dt(value: Any) -> dt.datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if hasattr(value, "timestamp"):  # Firestore DatetimeWithNanoseconds behaves like datetime
        try:
            return dt.datetime.fromtimestamp(value.timestamp(), tz=UTC)
        except Exception:
            return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def normalise_question(text: str) -> str:
    """metrics.md 3.1: group ignoring case, repeated whitespace and punctuation; keep original for display."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())).strip()


class Entity(BaseModel):
    model_config = ConfigDict(extra="ignore")


class TurnRecord(Entity):
    call_id: str
    turn_index: int
    speaker: str
    text: str = ""
    intent: str | None = None
    router_confidence: float | None = None
    tool_called: str | None = None
    answer_source: str | None = None
    stt_ms: int | None = None
    router_ms: int | None = None
    answer_ms: int | None = None
    tts_ms: int | None = None
    interrupted: bool = False
    used_fallback: bool = False
    at: dt.datetime | None = None

    @classmethod
    def from_doc(cls, doc: dict, *, call_id: str | None = None, turn_index: int | None = None) -> "TurnRecord | None":
        try:
            return cls(
                call_id=str(doc.get("callId") or call_id or ""),
                turn_index=int(doc.get("turnIndex") if doc.get("turnIndex") is not None else (turn_index or 0)),
                speaker=str(doc.get("speaker") or "agent"), text=str(doc.get("textFinal") or ""),
                intent=doc.get("intent"), router_confidence=doc.get("routerConfidence"),
                tool_called=doc.get("toolCalled"), answer_source=doc.get("answerSource"),
                stt_ms=doc.get("sttMs"), router_ms=doc.get("routerMs"), answer_ms=doc.get("answerMs"),
                tts_ms=doc.get("ttsMs"), interrupted=bool(doc.get("interrupted", False)),
                used_fallback=bool(doc.get("usedFallback", False)), at=parse_dt(doc.get("at")),
            )
        except (ValidationError, TypeError, ValueError):
            return None

    @property
    def is_gap(self) -> bool:
        return self.speaker == "agent" and self.answer_source == "not_found"

    @property
    def latency_ms(self) -> int | None:
        """The model-side answer stage for an agent turn (router + answer). Not caller-perceived wait."""
        if self.speaker != "agent":
            return None
        parts = [v for v in (self.router_ms, self.answer_ms) if v is not None]
        return sum(parts) if parts else None


class CallRecord(Entity):
    call_id: str
    business_id: str = ""
    from_number: str | None = None
    session_type: str = "phone"
    started_at: dt.datetime
    ended_at: dt.datetime | None = None
    duration_ms: int | None = None
    outcome: str | None = None
    twilio_status: str | None = None
    cost_cents: float = 0.0
    resume_count: int = 0
    slot_state: dict = Field(default_factory=dict)
    turns: list[TurnRecord] = Field(default_factory=list)

    @classmethod
    def from_doc(cls, doc: dict, turns: list[TurnRecord] | None = None) -> "CallRecord | None":
        started = parse_dt(doc.get("startedAt"))
        if not doc.get("callId") or started is None:
            return None
        try:
            call = cls(
                call_id=str(doc["callId"]), business_id=str(doc.get("businessId") or ""), from_number=doc.get("from"),
                session_type=str(doc.get("sessionType") or "phone"), started_at=started,
                ended_at=parse_dt(doc.get("endedAt")), duration_ms=_int_or_none(doc.get("durationMs")),
                outcome=doc.get("outcome"), twilio_status=doc.get("twilioStatus"),
                cost_cents=float(doc.get("costCents") or 0), resume_count=int(doc.get("resumeCount") or 0),
                slot_state=dict(doc.get("slotState") or {}),
            )
        except (ValidationError, TypeError, ValueError):
            return None
        call.turns = sorted((t for t in (turns or []) if t.text or t.interrupted), key=lambda t: t.turn_index)
        return call

    # ---- derived facts used by analytics + presentation ------------------------------------------
    @property
    def duration_secs(self) -> float | None:
        if self.duration_ms is not None:
            return self.duration_ms / 1000
        if self.ended_at is not None:
            return max(0.0, (self.ended_at - self.started_at).total_seconds())
        return None

    @property
    def intents(self) -> set[str]:
        return {t.intent for t in self.turns if t.speaker == "caller" and t.intent}

    @property
    def effective_outcome(self) -> str:
        if self.outcome:
            return self.outcome
        if self.slot_state.get("stage") == "done":
            return "booked"
        return "answered" if self.turns else "abandoned"

    @property
    def gap_questions(self) -> list[tuple[str, dt.datetime | None]]:
        """(caller question, asked_at) for every agent turn that had no confirmed data."""
        out = []
        for i, turn in enumerate(self.turns):
            if not turn.is_gap:
                continue
            question = next((t.text for t in reversed(self.turns[:i]) if t.speaker == "caller" and t.text), None)
            out.append((question or turn.text or "unknown question", turn.at or self.started_at))
        return out

    @property
    def has_gap(self) -> bool:
        return any(t.is_gap for t in self.turns)

    @property
    def agent_latencies(self) -> list[int]:
        return [t.latency_ms for t in self.turns if t.latency_ms is not None]

    @property
    def confidences(self) -> list[float]:
        return [t.router_confidence for t in self.turns if t.speaker == "caller" and t.router_confidence is not None]

    @property
    def interruptions(self) -> int:
        return sum(1 for t in self.turns if t.interrupted)


class BookingRecord(Entity):
    key: str
    call_id: str = ""
    starts_at: dt.datetime | None = None
    party_size: int | None = None
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    status: str = "confirmed"
    created_at: dt.datetime
    email_sent: bool = False

    @classmethod
    def from_doc(cls, key: str, doc: dict) -> "BookingRecord | None":
        created = parse_dt(doc.get("createdAt")) or parse_dt(doc.get("startsAt"))
        if created is None:
            return None
        try:
            return cls(key=key, call_id=str(doc.get("callId") or ""), starts_at=parse_dt(doc.get("startsAt")),
                       party_size=_int_or_none(doc.get("partySize")), name=doc.get("name"), phone=doc.get("phone"),
                       email=doc.get("email"), status=str(doc.get("status") or "confirmed"), created_at=created,
                       email_sent=bool(doc.get("emailSent", False)))
        except (ValidationError, TypeError, ValueError):
            return None


class CallbackRecord(Entity):
    id: str
    call_id: str = ""
    name: str | None = None
    phone: str | None = None
    question: str | None = None
    reason: str = "other"
    status: str = "open"
    created_at: dt.datetime
    updated_at: dt.datetime | None = None

    @classmethod
    def from_doc(cls, callback_id: str, doc: dict) -> "CallbackRecord | None":
        created = parse_dt(doc.get("createdAt"))
        if created is None:
            return None
        try:
            return cls(id=callback_id, call_id=str(doc.get("callId") or ""), name=doc.get("name"), phone=doc.get("phone"),
                       question=doc.get("question"), reason=str(doc.get("reason") or "other"),
                       status=str(doc.get("status") or "open"), created_at=created, updated_at=parse_dt(doc.get("updatedAt")))
        except (ValidationError, TypeError, ValueError):
            return None


class ReviewRecord(Entity):
    key: str
    status: str  # approved | dismissed
    answer: str | None = None
    question: str | None = None
    reviewed_at: dt.datetime | None = None


class Snapshot(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    calls: list[CallRecord] = Field(default_factory=list)
    bookings: list[BookingRecord] = Field(default_factory=list)
    callbacks: list[CallbackRecord] = Field(default_factory=list)
    reviews: dict[str, ReviewRecord] = Field(default_factory=dict)
    settings: dict = Field(default_factory=dict)
    as_of: dt.datetime
    source: str = "local"

    @property
    def has_demo_calls(self) -> bool:
        return any(c.session_type == "sim" or c.call_id.startswith("CAsim") for c in self.calls)


class RecordSource(Protocol):
    kind: str

    def load(self) -> Snapshot: ...

    def set_callback_status(self, callback_id: str, status: str, *, now: dt.datetime) -> bool: ...

    def set_gap_review(self, review: ReviewRecord) -> None: ...

    def update_settings(self, fields: dict) -> dict: ...


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# ---- local JSONL ----------------------------------------------------------------------------------

class LocalJsonlSource:
    """Replays `.testruns/{callId}.jsonl` written by `LocalJsonlSink`. Dashboard writes go to
    `.testruns/_dashboard.jsonl` so the sink's own files are never modified."""

    kind = "local"

    def __init__(self, root: Path | str = Path(".testruns"), *, clock=lambda: dt.datetime.now(UTC)) -> None:
        self.root = Path(root)
        self.clock = clock
        self._lock = threading.Lock()

    def _dashboard_records(self) -> list[dict]:
        path = self.root / f"{DASHBOARD_LOG}.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def _append(self, record: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": self.clock().isoformat(), **record}, default=str, ensure_ascii=False)
        with self._lock, (self.root / f"{DASHBOARD_LOG}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def load(self) -> Snapshot:
        calls: list[CallRecord] = []
        bookings: dict[str, dict] = {}
        callbacks: dict[str, dict] = {}
        emails: set[str] = set()
        if self.root.exists():
            for path in sorted(self.root.glob("*.jsonl")):
                if path.stem.startswith("_"):
                    continue
                merged: dict | None = None
                turns: dict[int, dict] = {}
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    kind = record.get("kind")
                    doc = record.get("doc") or {}
                    if kind == "call":
                        if merged is None or not record.get("merge"):
                            merged = dict(doc)
                        else:
                            merged.update(doc)
                    elif kind == "turn":
                        index = int(record.get("turnIndex") or doc.get("turnIndex") or 0)
                        if doc.get("interruptedOnly"):
                            turns.setdefault(index, {**doc, "textFinal": turns.get(index, {}).get("textFinal", "")})
                            turns[index]["interrupted"] = True
                        else:
                            turns[index] = {**turns.get(index, {}), **doc}
                    elif kind == "booking":
                        bookings[record.get("idempotencyKey") or doc.get("idempotencyKey") or path.stem] = dict(doc)
                    elif kind == "bookingUpdate":
                        key = record.get("idempotencyKey")
                        if key in bookings:
                            bookings[key].update(record.get("fields") or {})
                    elif kind == "callback":
                        callbacks[str(record.get("id"))] = dict(doc)
                    elif kind == "emailSent":
                        emails.add(str(record.get("idempotencyKey")))
                if merged:
                    call = CallRecord.from_doc(merged, [t for t in (TurnRecord.from_doc(d, call_id=merged.get("callId"), turn_index=i)
                                                            for i, d in turns.items()) if t])
                    if call:
                        calls.append(call)
        reviews: dict[str, ReviewRecord] = {}
        settings: dict = {}
        for record in self._dashboard_records():
            kind = record.get("kind")
            if kind == "callbackStatus" and str(record.get("id")) in callbacks:
                callbacks[str(record["id"])]["status"] = record.get("status")
                callbacks[str(record["id"])]["updatedAt"] = record.get("at")
            elif kind == "review":
                reviews[record["key"]] = ReviewRecord(key=record["key"], status=record.get("status", "dismissed"),
                                                      answer=record.get("answer"), question=record.get("question"),
                                                      reviewed_at=parse_dt(record.get("at")))
            elif kind == "settings":
                settings.update(record.get("data") or {})
        booking_records = [b for b in (BookingRecord.from_doc(k, {**d, "emailSent": k in emails}) for k, d in bookings.items()) if b]
        callback_records = [c for c in (CallbackRecord.from_doc(k, d) for k, d in callbacks.items()) if c]
        calls.sort(key=lambda c: c.started_at, reverse=True)
        return Snapshot(calls=calls, bookings=booking_records, callbacks=callback_records, reviews=reviews,
                        settings=settings, as_of=self.clock(), source=self.kind)

    def set_callback_status(self, callback_id: str, status: str, *, now: dt.datetime) -> bool:
        known = {c.id for c in self.load().callbacks}
        if callback_id not in known:
            return False
        self._append({"kind": "callbackStatus", "id": callback_id, "status": status})
        return True

    def set_gap_review(self, review: ReviewRecord) -> None:
        self._append({"kind": "review", "key": review.key, "status": review.status, "answer": review.answer,
                      "question": review.question})

    def update_settings(self, fields: dict) -> dict:
        current = self.load().settings
        current.update(fields)
        self._append({"kind": "settings", "data": fields})
        return current


# ---- Firestore -------------------------------------------------------------------------------------

class FirestoreSource:
    """Reads `businesses/{businessId}/**` with single-field queries only (no composite indexes to
    deploy).

    The tree is fetched **once a day** as a single packet of raw documents (`dashboard/packet.py`),
    written to disk, and taken apart locally on every read after that. Before that, every snapshot
    cost one Firestore read per document — a call's turns are a subcollection each, so ~275 reads a
    load — and the dashboard's own 60s poll spent them all over again, exhausting a 50,000/day free
    tier from one open tab in about three hours.

    Writes patch the stored packet in place rather than discarding it, so closing a callback costs
    one write and no reads. `refresh()` forces a new pull when the owner asks for one.

    The packet is also mirrored to a single Firestore document. On Vercel the local copy lives in
    /tmp, which is per-instance and gone on a cold start — without the mirror every new instance
    would pay the full fetch again, which on a bad day is worse than having no cache at all. Reading
    the mirror is **one** read instead of ~275.
    """

    kind = "firestore"

    def __init__(self, client: Any, paths: BusinessPaths, *, call_limit: int = 400, cache_seconds: float = 15.0,
                 clock=lambda: dt.datetime.now(UTC), packet: PacketStore | None = None) -> None:
        self.client, self.paths, self.call_limit, self.cache_seconds, self.clock = client, paths, call_limit, cache_seconds, clock
        # No packet unless one is handed in: a shared default path would let one process read back a
        # packet another wrote, which is exactly what it looked like in the test suite.
        self.packet = packet
        self._cached: tuple[float, Snapshot] | None = None
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        """Drop the in-process snapshot only. The packet on disk stands until it ages out, because
        re-reading it is free and re-fetching it is not."""
        self._cached = None

    def refresh(self) -> Snapshot:
        """Pull the tree again now, whatever the packet's or the mirror's age — the manual path."""
        with self._lock:
            self._cached = None
            if self.packet is not None:
                self.packet.delete()
            return self._reload()  # rewrites both the local packet and the mirror

    def _docs(self, collection: str, *, order_by: str | None = None, descending: bool = False,
              limit: int | None = None) -> list[tuple[str, dict]]:
        query = self.client.collection(collection)
        if order_by:
            from google.cloud.firestore_v1 import Query

            query = query.order_by(order_by, direction=Query.DESCENDING if descending else Query.ASCENDING)
        if limit:
            query = query.limit(limit)
        return [(snap.id, snap.to_dict() or {}) for snap in query.stream()]

    def load(self) -> Snapshot:
        with self._lock:
            if self._cached and time.monotonic() - self._cached[0] < self.cache_seconds:
                return self._cached[1]
            if self.packet is None:
                return self._reload()                    # no packet configured: straight through
            now = self.clock()
            stored = self.packet.read()
            if stored is not None and self.packet.is_fresh(stored, now=now):
                snapshot = self._unpack(stored)          # free: no reads at all
                self._cached = (time.monotonic(), snapshot)
                return snapshot
            mirrored = self._read_mirror()               # one read, for a cold instance
            if mirrored is not None and self.packet.is_fresh(mirrored, now=now):
                self.packet.write(mirrored)              # keep it local for the rest of this instance
                snapshot = self._unpack(mirrored)
                self._cached = (time.monotonic(), snapshot)
                return snapshot
            return self._reload()

    def _reload(self) -> Snapshot:
        """The only path that spends a full tree of reads. Caller holds the lock."""
        packet = self._fetch_packet()
        if self.packet is not None:
            self.packet.write(packet)
            self._write_mirror(packet)
        snapshot = self._unpack(packet)
        self._cached = (time.monotonic(), snapshot)
        return snapshot

    # ---- the Firestore-side mirror ---------------------------------------------------------------
    @property
    def _mirror_path(self) -> str:
        return f"{self.paths.root}/dashboard/packet"

    def _read_mirror(self) -> dict | None:
        if self.packet is None:
            return None
        try:
            snap = self.client.document(self._mirror_path).get()
        except Exception as exc:  # the mirror is an optimisation; never let it break a load
            log.warning("packet mirror unreadable: %s", exc)
            return None
        if not getattr(snap, "exists", False):
            return None
        doc = snap.to_dict() or {}
        body = doc.get("packet")
        if not isinstance(body, str):
            return None
        try:
            packet = json.loads(body)
        except ValueError:
            return None
        return packet if isinstance(packet, dict) and packet.get("version") == PACKET_VERSION else None

    def _write_mirror(self, packet: dict) -> None:
        """One document, one write a day. Skipped when the packet would not fit — Firestore caps a
        document at 1 MiB, and a busy call log can exceed that."""
        try:
            body = json.dumps(packet, default=_json_default)
        except (TypeError, ValueError) as exc:
            log.warning("packet not serialisable for the mirror: %s", exc)
            return
        if len(body.encode("utf-8")) > MIRROR_MAX_BYTES:
            log.info("packet too large to mirror (%d bytes); local cache only", len(body))
            return
        try:
            self.client.document(self._mirror_path).set({"packet": body, "fetchedAt": packet.get("fetchedAt")})
        except Exception as exc:
            log.warning("could not mirror the packet: %s", exc)

    # ---- the packet: raw documents in, one dict out ---------------------------------------------
    def _fetch_packet(self) -> dict:
        calls = []
        for call_id, doc in self._docs(self.paths.calls, order_by="startedAt", descending=True, limit=self.call_limit):
            calls.append({"id": call_id, "doc": doc,
                          "turns": [{"id": i, "doc": d} for i, d in self._docs(self.paths.turns(call_id))]})
        snap = self.client.document(self.paths.settings_doc).get()
        return {
            "version": PACKET_VERSION,
            "businessId": self.paths.business_id,
            "fetchedAt": self.clock().isoformat(),
            "calls": calls,
            "emailsSent": [key for key, _ in self._docs(self.paths.emails_sent)],
            "bookings": [{"id": k, "doc": d} for k, d in self._docs(self.paths.bookings)],
            "callbacks": [{"id": k, "doc": d} for k, d in self._docs(self.paths.callbacks)],
            "unanswered": [{"id": k, "doc": d} for k, d in self._docs(self.paths.unanswered)],
            "settings": (snap.to_dict() or {}) if getattr(snap, "exists", False) else {},
        }

    def _unpack(self, packet: dict) -> Snapshot:
        """Break the packet down through the same `from_doc` parsing the live read used, so there is
        one definition of what a call is."""
        calls: list[CallRecord] = []
        for row in packet.get("calls") or []:
            call_id, doc = row.get("id"), row.get("doc") or {}
            turns = [t for t in (TurnRecord.from_doc(t_row.get("doc") or {}, call_id=call_id,
                                                     turn_index=_int_or_none(t_row.get("id")))
                                 for t_row in row.get("turns") or []) if t]
            call = CallRecord.from_doc({**doc, "callId": doc.get("callId") or call_id}, turns)
            if call:
                calls.append(call)
        emails = set(packet.get("emailsSent") or [])
        bookings = [b for b in (BookingRecord.from_doc(r["id"], {**(r.get("doc") or {}), "emailSent": r["id"] in emails})
                                for r in packet.get("bookings") or []) if b]
        callbacks = [c for c in (CallbackRecord.from_doc(r["id"], r.get("doc") or {})
                                 for r in packet.get("callbacks") or []) if c]
        reviews = {}
        for row in packet.get("unanswered") or []:
            key, doc = row["id"], row.get("doc") or {}
            reviews[key] = ReviewRecord(key=key, status=str(doc.get("status") or "dismissed"), answer=doc.get("answer"),
                                        question=doc.get("question"), reviewed_at=parse_dt(doc.get("reviewedAt")))
        calls.sort(key=lambda c: c.started_at, reverse=True)
        return Snapshot(calls=calls, bookings=bookings, callbacks=callbacks, reviews=reviews,
                        settings=dict(packet.get("settings") or {}), as_of=self.clock(), source=self.kind)

    def set_callback_status(self, callback_id: str, status: str, *, now: dt.datetime) -> bool:
        stored = self.packet.read() if self.packet is not None else None
        if stored is not None:  # the packet answers this for free
            known = any(row.get("id") == callback_id for row in stored.get("callbacks") or [])
        else:  # no packet yet: one document read, not a whole tree
            known = getattr(self.client.document(f"{self.paths.callbacks}/{callback_id}").get(), "exists", False)
        if not known:
            return False
        fields = {"status": status, "updatedAt": now.isoformat()}
        self.client.document(f"{self.paths.callbacks}/{callback_id}").set(fields, merge=True)
        self._patch(lambda pk: upsert(pk.setdefault("callbacks", []), callback_id, fields))
        return True

    def set_gap_review(self, review: ReviewRecord) -> None:
        fields = {"status": review.status, "answer": review.answer, "question": review.question,
                  "reviewedAt": (review.reviewed_at or self.clock()).isoformat()}
        self.client.document(f"{self.paths.unanswered}/{review.key}").set(fields, merge=True)
        self._patch(lambda pk: upsert(pk.setdefault("unanswered", []), review.key, fields))

    def update_settings(self, fields: dict) -> dict:
        ref = self.client.document(self.paths.settings_doc)
        ref.set(fields, merge=True)
        stored = self.packet.read() if self.packet is not None else None
        if stored is not None:
            current = dict(stored.get("settings") or {})
        else:  # no packet yet: read back the one document rather than the whole tree
            snap = ref.get()
            current = (snap.to_dict() or {}) if getattr(snap, "exists", False) else {}
        merged = {**current, **fields}
        self._patch(lambda pk: pk.__setitem__("settings", merged))
        return merged

    def _patch(self, mutate) -> None:
        """Keep the day's packet true after a write, rather than paying for a whole re-fetch. If
        there is no packet to patch (none configured, nothing written yet, or a read-only disk),
        fall back to dropping the in-process snapshot so the next read rebuilds it."""
        if self.packet is None or self.packet.patch(mutate) is None:
            self.invalidate()
        else:
            self._cached = None


def review_key(question: str) -> str:
    """Firestore-safe document id for a normalised question (no slashes, bounded length)."""
    import hashlib

    norm = normalise_question(question)
    slug = re.sub(r"[^a-z0-9]+", "-", norm).strip("-")[:60] or "question"
    return f"{slug}-{hashlib.sha1(norm.encode()).hexdigest()[:8]}"
