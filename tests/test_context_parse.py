from marketing_radar.context import local_expand, parse_context
from tests.conftest import FROZEN_NOW, USER_ID


def _parse(source):
    return parse_context(source, user_id=USER_ID, source_path="users/x/context", now=FROZEN_NOW)


def test_markdown_and_map_produce_the_same_profile(sample_context_md, sample_context_map):
    md = _parse(sample_context_md)
    mp = _parse(sample_context_map)
    for field in ("niche", "keywords", "hashtags", "platforms", "format", "region", "language", "goal",
                  "facebook_page_urls", "facebook_group_urls", "competitor_handles", "audience_line"):
        assert getattr(md, field) == getattr(mp, field), field
    assert md.niche == "Home fitness for busy parents"
    assert md.keywords == ["home workout", "busy parents", "15 minute workout", "no equipment"]
    assert md.hashtags == ["homeworkout", "busyparents", "fitmom"]
    assert md.platforms == ["tiktok", "instagram"]
    assert md.goal == "followers"
    assert md.facebook_group_urls == ["https://www.facebook.com/groups/busyparentfitness"]


def test_caps_are_enforced(sample_context_map):
    profile = _parse(sample_context_map)
    assert len(profile.facebook_page_urls) == 2
    assert len(profile.facebook_group_urls) == 1
    assert profile.competitor_handles == ["fitmomdaily", "dadbodreset", "15minfit"]


def test_defaults_when_fields_missing():
    profile = _parse({"niche": "vegan meal prep"})
    assert profile.platforms == ["tiktok", "instagram"]
    assert profile.hashtags == [] and profile.facebook_page_urls == []
    assert profile.format is None and profile.goal is None


def test_text_wrapped_in_a_map_is_treated_as_markdown(sample_context_md):
    profile = _parse({"content": sample_context_md})
    assert profile.niche == "Home fitness for busy parents"
    assert profile.raw_text == sample_context_md


def test_local_expand_fills_hashtags():
    profile = _parse({"niche": "vegan meal prep", "keywords": "cheap dinners, batch cooking"})
    tags = local_expand(profile)
    assert tags[0] == "veganmealprep"
    assert {"vegan", "meal", "prep", "cheapdinners", "batchcooking"} <= set(tags)
