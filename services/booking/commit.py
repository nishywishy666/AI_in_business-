"""The concurrency-safe commit (vr_plan.md §8.3, V8, Rule 5).

The check and the write sit in ONE transaction that READS the slot document's denormalised
`seatsBooked` counter — never a query, never FieldValue.increment(). The decision itself is a
pure function shared by the Firestore transaction and the simulator's local committer, so the
same arithmetic is tested offline.
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from services.common.firestore import BusinessPaths

Outcome = Literal["created", "already_exists"]


class SlotFull(Exception):
    def __init__(self, slot_id: str, seats_booked: int, seats_total: int) -> None:
        super().__init__(f"slot {slot_id} full ({seats_booked}/{seats_total})")
        self.slot_id, self.seats_booked, self.seats_total = slot_id, seats_booked, seats_total


class SlotMissing(Exception):
    pass


def idempotency_key(call_sid: str, slot_id: str, name: str, party_size: int) -> str:
    return hashlib.sha256(f"{call_sid}|{slot_id}|{name.strip().lower()}|{party_size}".encode()).hexdigest()[:24]


def decide(slot_doc: dict | None, booking_exists: bool, party_size: int, slot_id: str) -> tuple[str, int]:
    """Pure decision: ('already_exists'|'created', new_seats_booked). Raises SlotFull / SlotMissing."""
    if slot_doc is None:
        raise SlotMissing(slot_id)
    booked, total = int(slot_doc.get("seatsBooked") or 0), int(slot_doc.get("seatsTotal") or 0)
    if booking_exists:
        return "already_exists", booked
    if booked + party_size > total:
        raise SlotFull(slot_id, booked, total)
    return "created", booked + party_size


class BookingCommitter(Protocol):
    def reserve(self, slot_id: str, party_size: int, key: str, booking_doc: dict) -> Outcome: ...

    def release(self, slot_id: str, party_size: int, key: str) -> None: ...


class FirestoreCommitter:
    def __init__(self, client: Any, paths: BusinessPaths) -> None:
        self.client, self.paths = client, paths

    def reserve(self, slot_id: str, party_size: int, key: str, booking_doc: dict) -> Outcome:
        from google.cloud import firestore

        slot_ref = self.client.document(self.paths.capacity_slot(slot_id))
        booking_ref = self.client.document(self.paths.booking(key))

        @firestore.transactional
        def _reserve(tx) -> Outcome:
            slot_snap = slot_ref.get(transaction=tx)
            booking_snap = booking_ref.get(transaction=tx)
            outcome, new_booked = decide(slot_snap.to_dict() if slot_snap.exists else None, booking_snap.exists,
                                         party_size, slot_id)
            if outcome == "created":
                tx.update(slot_ref, {"seatsBooked": new_booked})
                tx.create(booking_ref, booking_doc)
            return outcome

        return _reserve(self.client.transaction())

    def release(self, slot_id: str, party_size: int, key: str) -> None:
        from google.cloud import firestore

        slot_ref = self.client.document(self.paths.capacity_slot(slot_id))
        booking_ref = self.client.document(self.paths.booking(key))

        @firestore.transactional
        def _release(tx) -> None:
            slot = slot_ref.get(transaction=tx).to_dict() or {}
            tx.update(slot_ref, {"seatsBooked": max(0, int(slot.get("seatsBooked") or 0) - party_size)})
            tx.update(booking_ref, {"status": "cancelled"})

        _release(self.client.transaction())


@dataclass
class LocalCommitter:
    """Simulator committer: seat counters live in memory (seeded from the read-side slot docs), so a
    test run can exercise full-slot behaviour without ever writing capacitySlots (§12.3)."""

    reader: Any
    paths: BusinessPaths
    sink: Any
    counters: dict[str, dict] = field(default_factory=dict)
    bookings: dict[str, dict] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _slot(self, slot_id: str) -> dict | None:
        if slot_id not in self.counters:
            doc = self.reader.get_doc(self.paths.capacity_slot(slot_id))
            if doc is None:
                return None
            self.counters[slot_id] = {"seatsTotal": int(doc.get("seatsTotal") or 0),
                                      "seatsBooked": int(doc.get("seatsBooked") or 0)}
        return self.counters[slot_id]

    def reserve(self, slot_id: str, party_size: int, key: str, booking_doc: dict) -> Outcome:
        with self._lock:
            slot = self._slot(slot_id)
            outcome, new_booked = decide(slot, key in self.bookings, party_size, slot_id)
            if outcome == "created":
                slot["seatsBooked"] = new_booked
                self.bookings[key] = booking_doc
                self.sink.write_booking(key, booking_doc)
            return outcome

    def release(self, slot_id: str, party_size: int, key: str) -> None:
        with self._lock:
            slot = self._slot(slot_id)
            if slot:
                slot["seatsBooked"] = max(0, slot["seatsBooked"] - party_size)
            self.bookings.pop(key, None)
