"""Write sinks (vr_plan.md §12.3, V12). Isolation is structural, not filtered.

`FirestoreSink` is the only class here that touches google.cloud. `LocalJsonlSink` appends to
./.testruns/{callId}.jsonl and holds no Firestore client at all, so a simulator run physically
cannot write to production data. Reads are not in this module — the simulator reads real menu
and facts through services.voice.tools like production does.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Literal, Protocol

from services.common.config import VoiceConfig
from services.common.firestore import BusinessPaths

SinkKind = Literal["firestore", "local", "emulator"]
TESTRUNS_DIR = Path(".testruns")


class CallSink(Protocol):
    kind: SinkKind

    def write_call(self, call_id: str, doc: dict, *, merge: bool = False) -> None: ...

    def write_turn(self, call_id: str, turn_index: int, doc: dict) -> None: ...

    def write_booking(self, idempotency_key: str, doc: dict) -> Literal["created", "already_exists"]: ...

    def write_callback(self, doc: dict) -> str: ...

    def write_usage_event(self, doc: dict) -> None: ...

    def write_email_sent(self, idempotency_key: str, doc: dict) -> bool: ...

    def increment_rollup(self, local_date: str, counters: dict[str, int]) -> None: ...

    def read_call(self, call_id: str) -> dict | None: ...


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class LocalJsonlSink:
    """Simulator sink. One JSONL file per call under .testruns/. No Firestore anywhere in this class."""

    kind: SinkKind = "local"

    def __init__(self, root: Path | str = TESTRUNS_DIR, business_id: str = "sim") -> None:
        self.root = Path(root)
        self.business_id = business_id
        self._lock = threading.Lock()
        self._bookings: dict[str, dict] = {}
        self._emails: set[str] = set()

    def path_for(self, call_id: str) -> Path:
        return self.root / f"{call_id}.jsonl"

    def _append(self, call_id: str, record: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": _now(), "businessId": self.business_id, **record}, default=str, ensure_ascii=False)
        with self._lock, self.path_for(call_id).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def write_call(self, call_id: str, doc: dict, *, merge: bool = False) -> None:
        self._append(call_id, {"kind": "call", "merge": merge, "doc": doc})

    def write_turn(self, call_id: str, turn_index: int, doc: dict) -> None:
        self._append(call_id, {"kind": "turn", "turnIndex": turn_index, "doc": doc})

    def write_booking(self, idempotency_key: str, doc: dict) -> Literal["created", "already_exists"]:
        # Never touches capacitySlots — a simulator booking cannot consume real seats (§12.3).
        if idempotency_key in self._bookings:
            return "already_exists"
        self._bookings[idempotency_key] = doc
        self._append(doc.get("callId", "unknown"), {"kind": "booking", "idempotencyKey": idempotency_key, "doc": doc})
        return "created"

    def write_callback(self, doc: dict) -> str:
        callback_id = f"cb_{uuid.uuid4().hex[:12]}"
        self._append(doc.get("callId", "unknown"), {"kind": "callback", "id": callback_id, "doc": doc})
        return callback_id

    def write_usage_event(self, doc: dict) -> None:
        self._append(doc.get("callId", "unknown"), {"kind": "usageEvent", "doc": doc})

    def write_email_sent(self, idempotency_key: str, doc: dict) -> bool:
        if idempotency_key in self._emails:
            return False
        self._emails.add(idempotency_key)
        self._append(doc.get("callId", "unknown"), {"kind": "emailSent", "idempotencyKey": idempotency_key, "doc": doc})
        return True

    def increment_rollup(self, local_date: str, counters: dict[str, int]) -> None:
        self._append("_rollups", {"kind": "rollup", "localDate": local_date, "counters": counters})

    def write_booking_fields(self, idempotency_key: str, fields: dict) -> None:
        doc = self._bookings.get(idempotency_key)
        if doc is not None:
            doc.update(fields)
            self._append(doc.get("callId", "unknown"), {"kind": "bookingUpdate", "idempotencyKey": idempotency_key,
                                                        "fields": fields})

    def read_call(self, call_id: str) -> dict | None:
        """Merged view of the call document, from this sink's own JSONL (for resume in the simulator)."""
        merged: dict | None = None
        for record in self.read(call_id):
            if record.get("kind") != "call":
                continue
            if merged is None or not record.get("merge"):
                merged = dict(record["doc"])
            else:
                merged.update(record["doc"])
        return merged

    def read(self, call_id: str) -> list[dict]:
        path = self.path_for(call_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class FirestoreSink:
    """Production sink. Idempotent documents use create() so a duplicate fails loudly (Rule 5)."""

    kind: SinkKind = "firestore"

    def __init__(self, client: Any, paths: BusinessPaths, *, kind: SinkKind = "firestore") -> None:
        self.client = client
        self.paths = paths
        self.kind = kind

    def write_call(self, call_id: str, doc: dict, *, merge: bool = False) -> None:
        self.client.document(self.paths.call(call_id)).set(doc, merge=merge)

    def write_turn(self, call_id: str, turn_index: int, doc: dict) -> None:
        self.client.document(self.paths.turn(call_id, turn_index)).set(doc)

    def write_booking(self, idempotency_key: str, doc: dict) -> Literal["created", "already_exists"]:
        from google.api_core.exceptions import AlreadyExists

        try:
            self.client.document(self.paths.booking(idempotency_key)).create(doc)
        except AlreadyExists:
            return "already_exists"
        return "created"

    def write_callback(self, doc: dict) -> str:
        _, ref = self.client.collection(self.paths.callbacks).add(doc)
        return ref.id

    def write_usage_event(self, doc: dict) -> None:
        self.client.collection(self.paths.usage_events).add(doc)

    def write_email_sent(self, idempotency_key: str, doc: dict) -> bool:
        from google.api_core.exceptions import AlreadyExists

        try:
            self.client.document(self.paths.email_sent(idempotency_key)).create(doc)
        except AlreadyExists:
            return False
        return True

    def write_booking_fields(self, idempotency_key: str, fields: dict) -> None:
        self.client.document(self.paths.booking(idempotency_key)).set(fields, merge=True)

    def read_call(self, call_id: str) -> dict | None:
        snap = self.client.document(self.paths.call(call_id)).get()
        return snap.to_dict() if snap.exists else None

    def increment_rollup(self, local_date: str, counters: dict[str, int]) -> None:
        from google.cloud import firestore

        ref = self.client.document(self.paths.rollup(local_date))

        @firestore.transactional
        def _apply(tx):
            snap = ref.get(transaction=tx)
            current = snap.to_dict() if snap.exists else {}
            updated = {key: int(current.get(key, 0)) + int(value) for key, value in counters.items()}
            updated["localDate"] = local_date
            updated["updatedAt"] = _now()
            tx.set(ref, updated, merge=True)

        _apply(self.client.transaction())


def make_sink(config: VoiceConfig, *, client: Any | None = None) -> CallSink:
    if config.session_sink == "local":
        return LocalJsonlSink(business_id=config.business_id)
    if config.session_sink == "emulator" and not (config.firestore_emulator_host or os.environ.get("FIRESTORE_EMULATOR_HOST")):
        raise RuntimeError("SESSION_SINK=emulator requires FIRESTORE_EMULATOR_HOST")
    if client is None:
        from services.common.firestore import make_client

        client = make_client(config)
    return FirestoreSink(client, BusinessPaths(config.business_id), kind=config.session_sink)
