"""Read model over the sinks: local JSONL written by the real engine, and Firestore via a fake client."""
import datetime as dt

from dashboard.records import FirestoreSource, LocalJsonlSource, ReviewRecord, normalise_question, review_key
from services.common.firestore import BusinessPaths

from tests.dashboard_helpers import NOW, seed_runs


def test_seeded_runs_replay_into_calls_bookings_callbacks_and_gaps(tmp_path):
    sink, results = seed_runs(tmp_path / ".testruns", days=2)
    assert len(results) == 20 and {r["outcome"] for r in results} >= {"booked", "answered"}
    source = LocalJsonlSource(sink.root, clock=lambda: NOW)
    snap = source.load()
    assert len(snap.calls) == 20 and all(c.turns for c in snap.calls) and snap.has_demo_calls
    assert snap.calls[0].started_at >= snap.calls[-1].started_at  # newest first
    booked = [c for c in snap.calls if c.effective_outcome == "booked"]
    assert len(booked) == 6 and len(snap.bookings) == 6 and all(b.party_size for b in snap.bookings)
    assert all(b.email_sent for b in snap.bookings)
    reasons = {cb.reason for cb in snap.callbacks}
    assert {"catering", "allergen_unknown", "no_data"} <= reasons and all(cb.status == "open" for cb in snap.callbacks)
    gaps = [q for c in snap.calls for q, _ in c.gap_questions]
    assert any("gluten" in q for q in gaps) and any("delivery" in q for q in gaps)
    latencies = [ms for c in snap.calls for ms in c.agent_latencies]
    assert latencies and all(ms >= 0 for ms in latencies)


def test_dashboard_writes_go_to_their_own_file_and_survive_reload(tmp_path):
    sink, _ = seed_runs(tmp_path / ".testruns", days=1)
    source = LocalJsonlSource(sink.root, clock=lambda: NOW)
    before = {p.name: p.stat().st_size for p in sink.root.glob("CAsim*.jsonl")}
    cb = source.load().callbacks[0]
    assert source.set_callback_status(cb.id, "done", now=NOW) and not source.set_callback_status("nope", "done", now=NOW)
    key = review_key("Do you do delivery?")
    source.set_gap_review(ReviewRecord(key=key, status="approved", answer="Yes, via Uber Eats", question="Do you do delivery?"))
    source.update_settings({"notifPrefs": {"callbacks": False}})
    assert source.update_settings({"packetConfirmed": True}) == {"notifPrefs": {"callbacks": False}, "packetConfirmed": True}
    snap = source.load()
    assert next(c for c in snap.callbacks if c.id == cb.id).status == "done"
    assert snap.reviews[key].answer == "Yes, via Uber Eats"
    assert {p.name: p.stat().st_size for p in sink.root.glob("CAsim*.jsonl")} == before  # sink files untouched
    assert (sink.root / "_dashboard.jsonl").exists()


def test_question_normalisation_and_keys_are_stable():
    assert normalise_question("  Do you DO delivery?! ") == "do you do delivery"
    assert review_key("Do you do delivery?") == review_key("do you do delivery") != review_key("delivery?")
    assert "/" not in review_key("a/b/c") and len(review_key("x" * 500)) < 80


# ---- Firestore source over a fake client ------------------------------------------------------------

class _Snap:
    def __init__(self, doc_id, data):
        self.id, self._data, self.exists = doc_id, data, data is not None

    def to_dict(self):
        return dict(self._data or {})


class _Query:
    def __init__(self, store, path):
        self.store, self.path = store, path
        self._order, self._desc, self._limit = None, False, None

    def order_by(self, field, direction="ASCENDING"):
        self._order, self._desc = field, direction == "DESCENDING"
        return self

    def limit(self, n):
        self._limit = n
        return self

    def stream(self):
        rows = [(k.rsplit("/", 1)[1], v) for k, v in self.store.items() if k.startswith(self.path + "/") and "/" not in k[len(self.path) + 1:]]
        if self._order:
            rows.sort(key=lambda kv: str(kv[1].get(self._order) or ""), reverse=self._desc)
        if self._limit:
            rows = rows[:self._limit]
        return [_Snap(i, d) for i, d in rows]


class _Doc:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def get(self):
        return _Snap(self.path.rsplit("/", 1)[1], self.store.get(self.path))

    def set(self, data, merge=False):
        current = self.store.get(self.path) if merge else None
        self.store[self.path] = {**(current or {}), **data}


class FakeClient:
    def __init__(self, docs):
        self.store = dict(docs)

    def collection(self, path):
        return _Query(self.store, path)

    def document(self, path):
        return _Doc(self.store, path)


def test_firestore_source_reads_the_business_tree_and_writes_reviews():
    paths = BusinessPaths("biz1")
    started = (NOW - dt.timedelta(hours=1)).isoformat()
    client = FakeClient({
        paths.call("CA1"): {"callId": "CA1", "startedAt": started, "outcome": "answered", "durationMs": 60000, "sessionType": "phone"},
        paths.turn("CA1", 1): {"speaker": "caller", "textFinal": "do you do delivery?", "intent": "ANSWER_QUESTION", "routerConfidence": 0.9},
        paths.turn("CA1", 2): {"speaker": "agent", "textFinal": "I'll check", "answerSource": "not_found", "routerMs": 120, "answerMs": 0},
        paths.call("CA0"): {"callId": "CA0", "startedAt": (NOW - dt.timedelta(days=2)).isoformat(), "outcome": "booked"},
        paths.booking("k1"): {"callId": "CA0", "partySize": 4, "createdAt": (NOW - dt.timedelta(days=2)).isoformat(), "status": "confirmed"},
        f"{paths.callbacks}/cb1": {"callId": "CA1", "reason": "no_data", "status": "open", "createdAt": started, "question": "do you do delivery?"},
        f"{paths.unanswered}/{review_key('do you do delivery?')}": {"status": "dismissed"},
        paths.settings_doc: {"notifPrefs": {"digest": False}},
    })
    source = FirestoreSource(client, paths, clock=lambda: NOW, cache_seconds=0)
    snap = source.load()
    assert [c.call_id for c in snap.calls] == ["CA1", "CA0"] and snap.calls[0].has_gap and snap.calls[0].agent_latencies == [120]
    assert snap.bookings[0].party_size == 4 and snap.callbacks[0].reason == "no_data"
    assert snap.reviews[review_key("do you do delivery?")].status == "dismissed" and snap.settings == {"notifPrefs": {"digest": False}}
    assert source.set_callback_status("cb1", "done", now=NOW) and not source.set_callback_status("cb9", "done", now=NOW)
    source.set_gap_review(ReviewRecord(key="k", status="approved", answer="yes", question="q", reviewed_at=NOW))
    assert client.store[f"{paths.unanswered}/k"]["answer"] == "yes"
    assert source.update_settings({"packetConfirmed": True})["notifPrefs"] == {"digest": False}
    assert source.load().callbacks[0].status == "done"
