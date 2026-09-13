"""Proves the simulator cannot write to production Firestore (vr_plan.md §12.3, V12).

Deliberately hard to delete: if this file goes, Phase 2's gate is gone with it.
"""
import ast
import inspect
from pathlib import Path

import pytest

from api.index import build_deps, create_app
from api.sim.app import SimIsolationError, mount_sim
from services.common.config import load_config
from services.voice.pipeline import PlaceholderTurnEngine
from services.voice.sinks import FirestoreSink, LocalJsonlSink, make_sink
from tests.conftest import VOICE_ENV

REPO = Path(__file__).resolve().parents[1]
FIRESTORE_WORDS = ("google.cloud", "firestore", "FirestoreSink", "make_client", "get_client")


def _sim_config(tmp_path, **overrides):
    return load_config({**VOICE_ENV, "ENABLE_SIM": "1", "SESSION_SINK": "local", **overrides})


def test_local_sink_holds_no_firestore_client(tmp_path):
    sink = LocalJsonlSink(tmp_path / "runs", business_id="biz_test")
    for name, value in vars(sink).items():
        assert "firestore" not in type(value).__module__.lower(), name
    source = inspect.getsource(LocalJsonlSink)
    assert not any(word in source for word in FIRESTORE_WORDS), "LocalJsonlSink must not reference Firestore"
    assert not any(callable(getattr(sink, n)) and "firestore" in n.lower() for n in dir(sink))


def test_make_sink_local_never_touches_firestore(tmp_path):
    config = _sim_config(tmp_path, FIREBASE_SA_JSON="not-even-valid-json")  # would explode if a client were built
    sink = make_sink(config)
    assert isinstance(sink, LocalJsonlSink) and sink.kind == "local"


def test_sim_refuses_to_mount_with_firestore_sink(tmp_path):
    config = load_config({**VOICE_ENV, "ENABLE_SIM": "1", "SESSION_SINK": "firestore"})
    fake_firestore = FirestoreSink(client=object(), paths=None)  # type: ignore[arg-type]
    deps = build_deps(config, sink=fake_firestore, engine=PlaceholderTurnEngine(), greeting=b"")
    with pytest.raises(SimIsolationError):
        create_app(config, deps=deps)
    disabled = load_config({**VOICE_ENV, "ENABLE_SIM": "0", "SESSION_SINK": "local"})
    local_deps = build_deps(disabled, engine=PlaceholderTurnEngine(), greeting=b"")
    with pytest.raises(SimIsolationError):
        mount_sim(create_app(disabled, deps=local_deps), disabled, local_deps)


def test_sim_session_writes_only_to_jsonl_and_never_to_capacity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = _sim_config(tmp_path)
    deps = build_deps(config, engine=PlaceholderTurnEngine(), greeting=b"")
    app = create_app(config, deps=deps)
    sessions = app.state.sim_sessions
    session, recorder = sessions.get_or_create(None, "+61400000000")
    assert session.call_id.startswith("CAsim")
    assert deps.sink.write_booking("k1", {"callId": session.call_id, "partySize": 4}) == "created"
    assert deps.sink.write_booking("k1", {"callId": session.call_id, "partySize": 4}) == "already_exists"
    records = deps.sink.read(session.call_id)
    assert [r["kind"] for r in records] == ["call", "booking"]
    assert records[0]["doc"]["sessionType"] == "sim"
    assert not any("capacitySlots" in line for line in (tmp_path / ".testruns" / f"{session.call_id}.jsonl").read_text().splitlines())
    # No Firestore write method is reachable from the session's sink.
    assert not hasattr(deps.sink, "client")


def test_ws_module_has_no_simulator_branch():
    source = (REPO / "api" / "voice" / "ws.py").read_text()
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            assert "sim" not in node.value.lower(), f"simulator-specific string in ws.py: {node.value!r}"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
            assert not any("sim" in n.lower() for n in names), names
        if isinstance(node, ast.Name):
            assert "sim" not in node.id.lower(), f"simulator-specific name in ws.py: {node.id}"
    assert "ENABLE_SIM" not in source and "MZsim" not in source and "CAsim" not in source
