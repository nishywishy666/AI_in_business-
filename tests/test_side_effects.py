"""The /sim toggle that makes a local-sink booking send a real email and write a real Calendar event.

Default is OFF: the safe behaviour of vr_plan.md §12.3 is unchanged unless someone opts in.
"""
import pytest
from fastapi.testclient import TestClient

from api.index import build_deps, create_app
from services.booking.side_effects import SideEffectSwitch, SwitchableCalendar, SwitchableMailer
from services.common.config import load_config
from tests.conftest import VOICE_ENV

LIVE_ENV = {**VOICE_ENV, "ENABLE_SIM": "1", "SESSION_SINK": "local",
            "GMAIL_USER": "tony@example.com", "GMAIL_APP_PASSWORD": "x" * 16,
            "GOOGLE_CALENDAR_ID": "cal@group.calendar.google.com"}


def _fakes():
    """Offline router/answerer so building the engine never reaches Groq or Gemini."""
    from services.voice.answerer import FakeAnswerTransport
    from services.voice.router import FakeRouterTransport

    return {"router": FakeRouterTransport(), "answerer": FakeAnswerTransport()}


class Recorder:
    def __init__(self) -> None:
        self.calls: list = []

    def send(self, **kwargs) -> None:
        self.calls.append(kwargs)

    def upsert(self, booking) -> str:
        self.calls.append(booking)
        return "evt_1"


def test_switch_defaults_to_off_and_routes_to_the_safe_provider():
    off, on, switch = Recorder(), Recorder(), SideEffectSwitch(available=True)
    mailer = SwitchableMailer(off, on, switch)
    mailer.send(to="a@b.c", subject="s", body="b")
    assert len(off.calls) == 1 and on.calls == []
    assert switch.sent[-1]["live"] is False

    switch.enabled = True
    mailer.send(to="a@b.c", subject="s", body="b")
    assert len(on.calls) == 1 and switch.sent[-1]["live"] is True


def test_enabled_switch_is_still_inert_without_credentials():
    """`available` is False when the env lacks Gmail/Calendar config — enabling must not send."""
    off, on = Recorder(), Recorder()
    switch = SideEffectSwitch(enabled=True, available=False, reason="missing GOOGLE_CALENDAR_ID")
    calendar = SwitchableCalendar(off, on, switch)
    calendar.upsert({"startsAt": "2026-09-18T12:00:00+10:00"})
    assert on.calls == [] and len(off.calls) == 1
    assert switch.state()["enabled"] is False  # enabled but unavailable reads as off


def test_sim_exposes_the_toggle_and_refuses_to_enable_without_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bare = {**VOICE_ENV, "ENABLE_SIM": "1", "SESSION_SINK": "local",
            "GMAIL_USER": "", "GMAIL_APP_PASSWORD": "", "GOOGLE_CALENDAR_ID": "", "GOOGLE_SA_JSON": ""}
    config = load_config(bare, required=())
    client = TestClient(create_app(config, deps=build_deps(config, greeting=b"", **_fakes())))
    state = client.get("/sim/side-effects").json()
    assert state["enabled"] is False and state["available"] is False
    assert client.post("/sim/side-effects", json={"enabled": True}).status_code == 409


def test_sim_toggle_turns_on_when_credentials_are_present(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config(LIVE_ENV, required=())
    client = TestClient(create_app(config, deps=build_deps(config, greeting=b"", **_fakes())))
    assert client.get("/sim/side-effects").json() == {"enabled": False, "available": True, "reason": "ready", "sent": []}
    assert client.post("/sim/side-effects", json={"enabled": True}).json()["enabled"] is True
    assert client.post("/sim/side-effects", json={"enabled": False}).json()["enabled"] is False


def test_firestore_sink_gets_no_switch(tmp_path):
    """Production already sends real mail and writes the real calendar — there is nothing to toggle."""
    from services.voice.pipeline import PlaceholderTurnEngine
    from services.voice.sinks import FirestoreSink

    config = load_config({**VOICE_ENV, "SESSION_SINK": "firestore"})
    deps = build_deps(config, sink=FirestoreSink(client=object(), paths=None),  # type: ignore[arg-type]
                      engine=PlaceholderTurnEngine(), greeting=b"")
    assert deps.side_effects is None


@pytest.mark.parametrize("enabled", [True, False])
def test_booking_row_never_leaves_the_local_sink(tmp_path, monkeypatch, enabled):
    """Whatever the toggle says, no real seat is consumed and Firestore is never written."""
    monkeypatch.chdir(tmp_path)
    config = load_config(LIVE_ENV, required=())
    deps = build_deps(config, greeting=b"", **_fakes())
    deps.side_effects.enabled = enabled
    assert deps.sink.kind == "local" and not hasattr(deps.sink, "client")
