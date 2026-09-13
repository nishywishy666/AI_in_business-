import datetime as dt

import pytest

from marketing_radar.agent import FakeGeminiTransport, GeminiExhausted, GeminiLadder, ModelUnavailable, RateLimited
from marketing_radar.config import parse_ladder_json
from marketing_radar.usage import build_snapshot

MIDNIGHT_PT = dt.datetime(2026, 9, 12, 7, 0, tzinfo=dt.timezone.utc)


def _ladder(store, settings, clock, transport):
    return GeminiLadder(store, settings, transport, clock)


def test_acceptance_9_429_on_best_model_falls_back_exactly_once(store, settings, clock):
    transport = FakeGeminiTransport(default='{"ok": true}', scripts={"gemini-3.8-flash": [RateLimited("429")]})
    ladder = _ladder(store, settings, clock, transport)
    result = ladder.generate("synthesize", system="s", prompt="p")
    assert [c.model_id for c in transport.calls] == ["gemini-3.8-flash", "gemini-2.5-flash"]
    assert result.model_used == "gemini-2.5-flash" and result.quality == "medium" and result.rung_index == 1
    assert "lower than Gemini 3 Flash" in result.quality_warning
    assert "gemini-3.8-flash" in ladder.daily().exhausted

    ladder.generate("chat", system="s", prompt="p")
    assert [c.model_id for c in transport.calls][2:] == ["gemini-2.5-flash"], "no retry loop on the exhausted rung"

    snapshot = build_snapshot(store, settings, clock)
    assert snapshot.gemini.active_model == "gemini-2.5-flash"
    assert snapshot.gemini.quality_warning and snapshot.gemini.resets_at == MIDNIGHT_PT
    assert snapshot.gemini.models[0].status == "exhausted" and snapshot.gemini.models[1].used_today == 2
    assert {a.alert_id for a in snapshot.alerts} >= {"gemini_warning", "gemini_info"}
    events = [e for _, e in store.list(store.paths.usage_events) if e["provider"] == "gemini"]
    assert len(events) == 2 and {e["purpose"] for e in events} == {"synthesize", "chat"}
    assert all(e["model"] == "gemini-2.5-flash" for e in events)


def test_daily_cap_skips_rung_before_calling(store, settings, clock):
    transport = FakeGeminiTransport()
    ladder = _ladder(store, settings, clock, transport)
    for _ in range(20):
        ladder.generate("chat", system="s", prompt="p")
    assert all(c.model_id == "gemini-3.8-flash" for c in transport.calls)
    result = ladder.generate("chat", system="s", prompt="p")
    assert result.model_used == "gemini-2.5-flash" and transport.calls[-1].model_id == "gemini-2.5-flash"


def test_ladder_empty_raises_with_reset_time(store, settings, clock):
    settings.ladder = parse_ladder_json('[{"ids":["a"],"quality":"high","daily_cap":1},{"ids":["b"],"quality":"low","daily_cap":1}]')
    transport = FakeGeminiTransport(scripts={"a": [RateLimited("429")], "b": [RateLimited("429")]})
    ladder = _ladder(store, settings, clock, transport)
    with pytest.raises(GeminiExhausted) as exc:
        ladder.generate("chat", system="s", prompt="p")
    assert exc.value.resets_at == MIDNIGHT_PT
    with pytest.raises(GeminiExhausted):
        ladder.generate("chat", system="s", prompt="p")
    assert len(transport.calls) == 2, "exhausted rungs are not called again the same Pacific day"
    assert build_snapshot(store, settings, clock).alerts[0].alert_id == "gemini_exhausted"


def test_unavailable_model_tries_next_id_in_same_rung(store, settings, clock):
    transport = FakeGeminiTransport(scripts={"gemini-3.8-flash": [ModelUnavailable("404")]})
    result = _ladder(store, settings, clock, transport).generate("chat", system="s", prompt="p")
    assert result.model_used == "gemini-3-flash" and result.quality == "high" and result.quality_warning is None


def test_counters_reset_at_pacific_midnight(store, settings, clock):
    transport = FakeGeminiTransport(scripts={"gemini-3.8-flash": [RateLimited("429")]})
    ladder = _ladder(store, settings, clock, transport)
    ladder.generate("chat", system="s", prompt="p")
    assert ladder.daily().exhausted == ["gemini-3.8-flash"]
    clock.advance(hours=6)  # 02:00Z -> 08:00Z, past 07:00Z midnight Pacific
    assert ladder.daily().exhausted == [] and ladder.daily().used == {}
    result = ladder.generate("chat", system="s", prompt="p")
    assert result.model_used == "gemini-3.8-flash"


def test_min_rung_forces_a_lower_rung(store, settings, clock):
    transport = FakeGeminiTransport()
    result = _ladder(store, settings, clock, transport).generate("synthesize", system="s", prompt="p", min_rung=2)
    assert result.model_used == "gemini-2.5-flash-lite" and result.quality == "low"


def test_ladder_override_from_env_is_honoured(store, settings, clock):
    settings.ladder = parse_ladder_json('[{"ids":["gemini-3-flash"],"quality":"high","daily_cap":1500,"label":"G3"}]')
    result = _ladder(store, settings, clock, FakeGeminiTransport()).generate("chat", system="s", prompt="p")
    assert result.model_used == "gemini-3-flash"
    assert build_snapshot(store, settings, clock).gemini.models[0].daily_cap == 1500
