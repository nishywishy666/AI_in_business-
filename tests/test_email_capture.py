import pytest

from config import threshold
from services.voice import email_capture as ec


def test_spec_example_corrects_domain_and_scores_085():
    c = ec.capture("sarah dot chen at gmail dot con")
    assert c.local == "sarah.chen" and c.domain == "gmail.com"
    assert c.domain_score == 0.85 and c.local_score == 1.0 and c.confidence == 0.85
    assert c.needs_spelling is False
    assert ec.spoken(c.local, c.domain) == "sarah dot chen at gmail dot com"


def test_normalisation_rules_in_order():
    assert ec.normalise("Sarah Chen at the rate of gmail point com") == "sarahchen@gmail.com"
    assert ec.normalise("j underscore smith at outlook dot com") == "j_smith@outlook.com"
    assert ec.normalise("bob dash one two three at yahoo dot com dot au") == "bob-123@yahoo.com.au"
    assert ec.normalise("me plus tag at icloud dot com") == "me+tag@icloud.com"
    assert ec.normalise("a at at b dot dot com") == "a@b.com"
    assert ec.split("first.last@sub.example.org") == ("first.last", "sub.example.org")


def test_two_character_local_part_drops_below_threshold():
    c = ec.capture("jo at gmail dot com")
    assert c.local == "jo" and c.local_score == pytest.approx(0.6)
    assert c.confidence < threshold("EMAIL_CONFIDENCE_THRESHOLD") and c.needs_spelling is True


def test_domain_scores():
    assert ec.score_domain("gmail.com") == (1.0, "gmail.com")
    assert ec.score_domain("gmial.com") == (0.85, "gmail.com")
    assert ec.score_domain("mycompany.com.au") == (0.55, "mycompany.com.au")
    assert ec.score_domain("notadomain") == (0.0, "notadomain")
    assert ec.score_domain("") == (0.0, "")


def test_local_penalties_and_word_confidence():
    assert ec.score_local("sarah.chen") == 1.0
    assert ec.score_local("a" * 31) == pytest.approx(0.8)
    assert ec.score_local("x" * 31) == pytest.approx(0.55), "long AND a 4+ consonant run: both penalties apply"
    assert ec.score_local("bad!name") == pytest.approx(0.5)
    assert ec.score_local("strngth") == pytest.approx(0.75)
    assert ec.score_local("a..b") == pytest.approx(0.7)
    assert ec.score_local("sarah", min_word_confidence=0.5) == pytest.approx(0.35)
    assert ec.score_local("sarah", min_word_confidence=0.9) == pytest.approx(0.9)
    assert ec.capture("sarah at gmail dot com", word_confidences=[0.95, 0.6, 0.9]).confidence < 0.8


def test_spelling_rebuild_and_phonetic_checks():
    assert ec.letters_from_spelling("s a r a h") == "sarah"
    assert ec.letters_from_spelling("s-a-r-a-h dot c") == "sarah.c"
    assert ec.letters_from_spelling("b for bravo, e for echo, n for november") == "ben"
    assert ec.letters_from_spelling("sierra alpha romeo alpha hotel") == "sarah"
    assert ec.letters_from_spelling("j o underscore nine nine") == "jo_99"
    assert ec.phonetic_checks("ben") == ["B for bravo", "E for echo", "N for november"]
    assert ec.phonetic_checks("sarah") == ["S for sierra"]
    assert ec.phonetic_checks("jolly") == []
    rebuilt = ec.rebuild("sarah", "gmail.com")
    assert rebuilt.confidence == 1.0 and rebuilt.needs_spelling is False
