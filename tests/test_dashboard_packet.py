"""The daily packet: the tree is read once, served from disk after that, and writes patch it.

The numbers matter here, not just the behaviour — the whole point is the count of Firestore reads,
so the fake client counts every document it hands out.
"""
import datetime as dt

import pytest

from dashboard.packet import PacketStore, upsert
from dashboard.records import FirestoreSource
from services.common.firestore import BusinessPaths

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 10, 5, 0, tzinfo=UTC)
BUSINESS = "uncle_tony"


class FakeDoc:
    def __init__(self, doc_id, data):
        self.id, self._data, self.exists = doc_id, data, True

    def to_dict(self):
        return dict(self._data)


class FakeQuery:
    def __init__(self, client, path):
        self.client, self.path, self._limit = client, path, None

    def order_by(self, *_args, **_kwargs):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def stream(self):
        rows = list(self.client.data.get(self.path, {}).items())[: self._limit]
        self.client.reads += len(rows)
        return [FakeDoc(k, v) for k, v in rows]


class FakeRef:
    def __init__(self, client, path):
        self.client, self.path = client, path

    def get(self):
        collection, _, doc_id = self.path.rpartition("/")
        data = self.client.data.get(collection, {}).get(doc_id)
        self.client.reads += 1
        doc = FakeDoc(doc_id, data or {})
        doc.exists = data is not None
        return doc

    def set(self, fields, merge=False):
        collection, _, doc_id = self.path.rpartition("/")
        bucket = self.client.data.setdefault(collection, {})
        bucket[doc_id] = {**(bucket.get(doc_id, {}) if merge else {}), **fields}
        self.client.writes += 1


class FakeClient:
    """Counts reads the way Firestore bills them: one per document handed back."""

    def __init__(self, data):
        self.data, self.reads, self.writes = data, 0, 0

    def collection(self, path):
        return FakeQuery(self, path)

    def document(self, path):
        return FakeRef(self, path)


def _tree(calls=3, turns=8):
    paths = BusinessPaths(BUSINESS)
    data = {
        paths.calls: {f"CA{i}": {"callId": f"CA{i}", "startedAt": f"2026-09-10T0{i}:00:00+00:00",
                                 "endedAt": f"2026-09-10T0{i}:10:00+00:00", "outcome": "answered"}
                      for i in range(calls)},
        paths.bookings: {"bk1": {"name": "Tony", "partySize": 2, "startsAt": "2026-09-10T02:00:00+00:00"}},
        paths.callbacks: {"cb1": {"name": "Jo", "phone": "+61400000000", "question": "gluten free?",
                                  "createdAt": "2026-09-10T01:00:00+00:00", "status": "open"}},
        paths.emails_sent: {},
        paths.unanswered: {},
        f"{paths.root}/settings": {"dashboard": {"packetConfirmed": True}},  # settings_doc
    }
    for i in range(calls):
        data[paths.turns(f"CA{i}")] = {
            str(t): {"turnIndex": t, "speaker": "caller" if t % 2 else "agent", "textFinal": f"line {t}"}
            for t in range(turns)
        }
    return data


@pytest.fixture
def source(tmp_path):
    client = FakeClient(_tree())
    packet = PacketStore(BUSINESS, directory=tmp_path, max_age_hours=24)
    src = FirestoreSource(client, BusinessPaths(BUSINESS), cache_seconds=0, clock=lambda: NOW, packet=packet)
    return src, client, packet


def test_the_tree_is_read_once_and_served_from_the_packet_after(source):
    src, client, packet = source
    first = src.load()
    spent = client.reads
    assert spent > 0 and len(first.calls) == 3
    assert first.calls[0].turns, "turns come back through the packet, not a second query"

    for _ in range(20):
        again = src.load()
    assert client.reads == spent, "20 further loads cost nothing"
    assert [c.call_id for c in again.calls] == [c.call_id for c in first.calls]
    assert packet.path.exists()


def test_a_stale_packet_is_re_fetched(tmp_path):
    client = FakeClient(_tree())
    packet = PacketStore(BUSINESS, directory=tmp_path, max_age_hours=24)
    clock = {"now": NOW}
    src = FirestoreSource(client, BusinessPaths(BUSINESS), cache_seconds=0,
                          clock=lambda: clock["now"], packet=packet)
    src.load()
    spent = client.reads
    clock["now"] = NOW + dt.timedelta(hours=23)
    src.load()
    assert client.reads == spent, "still inside the day"
    clock["now"] = NOW + dt.timedelta(hours=25)
    src.load()
    assert client.reads > spent, "past the day, pull again"


def test_a_write_patches_the_packet_instead_of_spending_a_re_fetch(source):
    src, client, packet = source
    src.load()
    spent, written = client.reads, client.writes  # the load already mirrored the packet: one write
    assert src.set_callback_status("cb1", "done", now=NOW) is True
    assert client.writes == written + 1, "one write for the callback, nothing else"
    assert client.reads == spent, "closing a callback costs no reads"
    assert [c.status for c in src.load().callbacks] == ["done"]
    assert packet.read()["callbacks"][0]["doc"]["status"] == "done"

    src.update_settings({"packetConfirmed": False})
    assert src.load().settings["packetConfirmed"] is False
    assert client.reads == spent


def test_refresh_pulls_again_on_demand(source):
    src, client, packet = source
    src.load()
    spent = client.reads
    client.data[BusinessPaths(BUSINESS).callbacks]["cb2"] = {
        "name": "Sam", "phone": "+61400000001", "question": "parking?",
        "createdAt": "2026-09-10T02:00:00+00:00", "status": "open"}
    assert len(src.load().callbacks) == 1, "the packet does not know about the new row yet"
    assert len(src.refresh().callbacks) == 2
    assert client.reads > spent


def test_an_unwritable_packet_directory_still_serves_data(tmp_path):
    blocked = tmp_path / "nope"
    blocked.write_text("not a directory", encoding="utf-8")  # mkdir here will fail
    client = FakeClient(_tree())
    packet = PacketStore(BUSINESS, directory=blocked / "sub", max_age_hours=24)
    src = FirestoreSource(client, BusinessPaths(BUSINESS), cache_seconds=0, clock=lambda: NOW, packet=packet)
    assert len(src.load().calls) == 3, "a read-only disk costs reads, never correctness"


def test_native_firestore_values_do_not_break_the_packet(tmp_path):
    """Firestore returns a `DatetimeWithNanoseconds` for any Timestamp field and bytes for a blob.
    json.dumps raises TypeError on both, and an exception here used to escape all the way out of
    load() and 500 the dashboard. Timestamps must survive as ISO strings the read side can parse."""

    class DatetimeWithNanoseconds(dt.datetime):
        pass

    store = PacketStore(BUSINESS, directory=tmp_path)
    started = DatetimeWithNanoseconds(2026, 9, 10, 1, tzinfo=UTC)
    assert store.write({"version": 1, "fetchedAt": NOW.isoformat(),
                        "calls": [{"id": "CA1", "doc": {"callId": "CA1", "startedAt": started,
                                                        "blob": b"raw"}, "turns": []}]}) is True
    doc = store.read()["calls"][0]["doc"]
    assert dt.datetime.fromisoformat(doc["startedAt"]) == started and doc["blob"] == "raw"


def test_upsert_merges_by_id_and_appends_when_new():
    rows = [{"id": "a", "doc": {"x": 1, "y": 2}}]
    upsert(rows, "a", {"y": 3})
    assert rows == [{"id": "a", "doc": {"x": 1, "y": 3}}]
    upsert(rows, "b", {"z": 1})
    assert rows[-1] == {"id": "b", "doc": {"z": 1}}
