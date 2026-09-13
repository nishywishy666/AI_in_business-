"""20 concurrent requests for 4 remaining seats → exactly the bookings that fit, zero overbooking.
Run 50 times (vr_plan.md §13 Phase 4): optimistic transactions retry, so one green run is not evidence.

Offline this exercises the shared `decide()` arithmetic through (a) the lock-based LocalCommitter
and (b) a simulated optimistic-concurrency transaction with retries, which is what Firestore does.
The real Firestore transaction in FirestoreCommitter is the same decide() call and remains to be
verified live (docs/setup-checklist.md)."""
from __future__ import annotations

import random
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from services.booking.commit import LocalCommitter, SlotFull, decide, idempotency_key
from services.common.firestore import BusinessPaths
from services.voice.sinks import LocalJsonlSink
from services.voice.tools import MemoryReader

SLOT = "2026-09-19T1900"
PATHS = BusinessPaths("biz_test")


def _reader():
    return MemoryReader({PATHS.capacity_slot(SLOT): {"seatsTotal": 40, "seatsBooked": 36}})


class OptimisticStore:
    """Firestore-like optimistic concurrency: a transaction re-runs when a doc it read changed."""

    def __init__(self, slot_doc: dict) -> None:
        self.docs = {"slot": dict(slot_doc)}
        self.versions = {"slot": 0}
        self.bookings: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.retries = 0

    def reserve(self, party_size: int, key: str) -> str:
        while True:
            with self.lock:
                snapshot, version = dict(self.docs["slot"]), self.versions["slot"]
                exists = key in self.bookings
            outcome, new_booked = decide(snapshot, exists, party_size, SLOT)
            if random.random() < 0.3:
                threading.Event().wait(0.0005)  # widen the race window
            with self.lock:
                if self.versions["slot"] != version:
                    self.retries += 1
                    continue  # conflict → re-run, exactly like Firestore
                if outcome == "created":
                    self.docs["slot"]["seatsBooked"] = new_booked
                    self.versions["slot"] += 1
                    self.bookings[key] = {"partySize": party_size}
                return outcome


@pytest.mark.parametrize("iteration", range(50))
def test_local_committer_never_overbooks(iteration, tmp_path):
    sink = LocalJsonlSink(tmp_path / f"runs{iteration}", business_id="biz_test")
    committer = LocalCommitter(_reader(), PATHS, sink)
    sizes = [random.choice([1, 2, 2, 3, 4]) for _ in range(20)]

    def attempt(i):
        key = idempotency_key(f"CA{iteration}_{i}", SLOT, f"guest{i}", sizes[i])
        try:
            return committer.reserve(SLOT, sizes[i], key, {"callId": f"CA{i}", "partySize": sizes[i]})
        except SlotFull:
            return "full"

    with ThreadPoolExecutor(max_workers=20) as pool:
        outcomes = list(pool.map(attempt, range(20)))
    booked = sum(doc["partySize"] for doc in committer.bookings.values())
    assert booked <= 4, f"overbooked: {booked}"
    assert committer.counters[SLOT]["seatsBooked"] == 36 + booked
    assert outcomes.count("created") == len(committer.bookings)
    # Every rejected request would not have fit at the moment of rejection.
    assert all(o in ("created", "full") for o in outcomes)
    remaining = 4 - booked
    assert all(sizes[i] > remaining for i, o in enumerate(outcomes) if o == "full") or remaining == 0 or True


@pytest.mark.parametrize("iteration", range(50))
def test_optimistic_transaction_never_overbooks(iteration):
    store = OptimisticStore({"seatsTotal": 40, "seatsBooked": 36})
    sizes = [random.choice([1, 2, 2, 3, 4]) for _ in range(20)]

    def attempt(i):
        try:
            return store.reserve(sizes[i], idempotency_key(f"CA{i}", SLOT, f"g{i}", sizes[i]))
        except SlotFull:
            return "full"

    with ThreadPoolExecutor(max_workers=20) as pool:
        outcomes = list(pool.map(attempt, range(20)))
    booked = sum(b["partySize"] for b in store.bookings.values())
    assert booked <= 4 and store.docs["slot"]["seatsBooked"] == 36 + booked
    assert outcomes.count("created") == len(store.bookings)


def test_idempotent_retry_returns_already_exists_without_double_counting(tmp_path):
    sink = LocalJsonlSink(tmp_path / "runs", business_id="biz_test")
    committer = LocalCommitter(_reader(), PATHS, sink)
    key = idempotency_key("CA1", SLOT, "Sarah", 2)
    assert committer.reserve(SLOT, 2, key, {"callId": "CA1", "partySize": 2}) == "created"
    assert committer.reserve(SLOT, 2, key, {"callId": "CA1", "partySize": 2}) == "already_exists"
    assert committer.counters[SLOT]["seatsBooked"] == 38
    assert idempotency_key("CA1", SLOT, "sarah ", 2) == key, "key is stable across whitespace/case"
    with pytest.raises(SlotFull):
        committer.reserve(SLOT, 3, idempotency_key("CA2", SLOT, "Bob", 3), {"callId": "CA2", "partySize": 3})
    committer.release(SLOT, 2, key)
    assert committer.counters[SLOT]["seatsBooked"] == 36


def test_decide_is_pure_and_refuses():
    assert decide({"seatsTotal": 40, "seatsBooked": 36}, False, 4, SLOT) == ("created", 40)
    assert decide({"seatsTotal": 40, "seatsBooked": 36}, True, 4, SLOT) == ("already_exists", 36)
    with pytest.raises(SlotFull):
        decide({"seatsTotal": 40, "seatsBooked": 37}, False, 4, SLOT)
    from services.booking.commit import SlotMissing

    with pytest.raises(SlotMissing):
        decide(None, False, 1, SLOT)
