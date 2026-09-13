import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

from marketing_radar.context import parse_context
from marketing_radar.packets import TrendPacket
from marketing_radar.scoring import (
    dedup_keep_best,
    niche_fit,
    recency_weight,
    relative_velocity,
    score_batch,
    select_lists,
)
from tests.conftest import FROZEN_NOW, USER_ID

AIOS_SCORE_IDEAS = (Path(__file__).resolve().parents[1] / "previous_work"
                    / "Acceleratoz-aios-feat-portable-install" / "tools" / "idea-scout" / "score-ideas.py")


def _packet(pid, platform="tiktok", likes=0, comments=0, shares=0, hours_ago=1.0, caption="", hashtags=()):
    return TrendPacket(platform=platform, post_id=f"{pid}", provider_id=pid.split("_", 1)[1],
                       published_at=FROZEN_NOW - dt.timedelta(hours=hours_ago), likes=likes, comments=comments,
                       shares=shares, caption=caption, hashtags=list(hashtags))


@pytest.fixture
def context(sample_context_map):
    return parse_context(sample_context_map, user_id=USER_ID, source_path="x", now=FROZEN_NOW)


def test_acceptance_5_midrank_matches_score_ideas():
    assert relative_velocity([10, 20, 20]) == [0.0, 0.75, 0.75]
    assert relative_velocity([5]) == [1.0]
    assert relative_velocity([]) == []


@pytest.mark.skipif(not AIOS_SCORE_IDEAS.exists(), reason="previous_work reference not present")
def test_golden_against_aios_score_ideas_relative_mode():
    spec = importlib.util.spec_from_file_location("aios_score_ideas", AIOS_SCORE_IDEAS)
    module = importlib.util.module_from_spec(spec)
    sys.modules["aios_score_ideas"] = module
    spec.loader.exec_module(module)

    now = FROZEN_NOW
    ideas = [
        {"source": "tiktok", "post_id": "a", "published_at": (now - dt.timedelta(hours=2)).isoformat(),
         "metrics": {"likes": 1000, "comments": 50, "shares": 10}, "higgsfield_producibility": 1.0},
        {"source": "tiktok", "post_id": "b", "published_at": (now - dt.timedelta(days=5)).isoformat(),
         "metrics": {"likes": 90000, "comments": 900, "shares": 4000}, "higgsfield_producibility": 1.0},
        {"source": "tiktok", "post_id": "c", "published_at": (now - dt.timedelta(days=20)).isoformat(),
         "metrics": {"likes": 500, "comments": 5, "shares": None}, "higgsfield_producibility": 1.0},
    ]
    scored = [module.score(i, now) for i in ideas]
    module.relativize_velocity(scored)
    expected = {s["post_id"]: s["scoring"] for s in scored}

    packets = [_packet("tt_a", likes=1000, comments=50, shares=10, hours_ago=2),
               _packet("tt_b", likes=90000, comments=900, shares=4000, hours_ago=5 * 24),
               _packet("tt_c", likes=500, comments=5, shares=0, hours_ago=20 * 24)]
    context = parse_context({"niche": ""}, user_id=USER_ID, source_path="x", now=now)
    ours = {p.post_id[3:]: p.model_copy(update={"niche_fit": 1.0}) for p in score_batch(packets, context, now)}
    for pid, exp in expected.items():
        mine = ours[pid]
        assert mine.velocity == pytest.approx(exp["engagement_velocity"], abs=1e-4)
        assert mine.recency == pytest.approx(exp["recency_weight"], abs=1e-4)
        assert mine.raw_per_hour == pytest.approx(exp["_raw_per_hour"], abs=0.02)
        assert mine.velocity * mine.recency * 1.0 * 1.0 == pytest.approx(exp["final_score"], abs=1e-3)


def test_acceptance_6_no_producibility_term(context):
    p = score_batch([_packet("tt_x", likes=100, caption="#homeworkout", hashtags=["homeworkout"])], context, FROZEN_NOW)[0]
    assert p.final == pytest.approx(p.velocity * p.niche_fit * p.recency * p.platform_weight, abs=1e-4)
    assert "producibility" not in TrendPacket.model_fields


def test_recency_and_platform_weight():
    assert recency_weight(None, FROZEN_NOW) == 0.5
    assert recency_weight(FROZEN_NOW - dt.timedelta(days=30), FROZEN_NOW) == 0.3
    assert recency_weight(FROZEN_NOW - dt.timedelta(days=7), FROZEN_NOW) == pytest.approx(0.5, abs=1e-3)
    ctx = parse_context({"niche": ""}, user_id=USER_ID, source_path="x", now=FROZEN_NOW)
    scored = {p.platform: p for p in score_batch([_packet("yt_1", "youtube", likes=10), _packet("tt_1", likes=10)], ctx, FROZEN_NOW)}
    assert (scored["youtube"].platform_weight, scored["tiktok"].platform_weight) == (0.85, 1.0)


def test_niche_fit_rules(context):
    assert niche_fit(_packet("tt_1"), context) == 0.0
    assert niche_fit(_packet("tt_2", caption="anything", hashtags=["fitmom"]), context) == 1.0
    assert niche_fit(_packet("tt_3", caption="my home workout is short"), context) == 1.0
    partial = niche_fit(_packet("tt_4", caption="parents who lift heavy"), context)
    assert 0.0 < partial < 1.0
    assert niche_fit(_packet("tt_5", caption="cat discovers printer"), context) == 0.0


def test_dedup_keeps_higher_final():
    a = _packet("tt_1").model_copy(update={"final": 0.2})
    b = _packet("tt_1").model_copy(update={"final": 0.9})
    assert dedup_keep_best([a, b])[0].final == 0.9


def test_select_lists_global_niche_film_this_and_dedup(context):
    packets = [
        _packet("tt_viral", likes=900000, comments=12000, shares=41000, hours_ago=12, caption="roommates #college"),
        _packet("ig_fit", "instagram", likes=88000, comments=1500, shares=6200, hours_ago=20, caption="#homeworkout #fitmom", hashtags=["homeworkout", "fitmom"]),
        _packet("tt_dad", likes=62000, comments=800, shares=5200, hours_ago=8, caption="#dadbod #homeworkout", hashtags=["dadbod", "homeworkout"]),
        _packet("yt_push", "youtube", likes=210000, comments=3800, shares=0, hours_ago=30, caption="pushups #homeworkout", hashtags=["homeworkout"]),
        _packet("fb_page", "facebook", likes=8400, comments=310, shares=1900, hours_ago=40, caption="15 min for busy parents #homeworkout", hashtags=["homeworkout"]),
        _packet("ig_dog", "instagram", likes=420000, comments=5600, shares=39000, hours_ago=36, caption="#dogs"),
    ]
    scored = score_batch(packets, context, FROZEN_NOW)
    lists = select_lists(scored)
    assert [p.post_id for p in lists.global_][:1] == ["tt_viral"]
    assert all(p.niche_fit >= 0.5 for p in lists.niche) and not lists.weak_niche
    assert lists.film_this.is_primary_platform
    assert len(lists.top_for_gemini) <= 15 and lists.film_this.post_id in {p.post_id for p in lists.top_for_gemini}

    lists2 = select_lists(scored, recent_post_ids={"tt_viral", "ig_dog"})
    assert "tt_viral" not in {p.post_id for p in lists2.global_}


def test_film_this_secondary_needs_a_clear_win(context):
    tt = _packet("tt_1").model_copy(update={"final": 0.50, "velocity": 0.5})
    yt_close = _packet("yt_1", "youtube").model_copy(update={"final": 0.60, "velocity": 0.9})
    yt_clear = _packet("yt_2", "youtube").model_copy(update={"final": 0.70, "velocity": 1.0})
    assert select_lists([tt, yt_close]).film_this.post_id == "tt_1"
    assert select_lists([tt, yt_clear]).film_this.post_id == "yt_2"
