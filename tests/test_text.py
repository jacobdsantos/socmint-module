from __future__ import annotations

from socmint import text


def test_normalise_strips_urls_and_mentions():
    out = text.normalise("Hello @user see https://example.com/x now")
    assert "@user" not in out
    assert "http" not in out
    assert "Hello" in out


def test_topic_match_requires_all_words():
    hay = "A magnitude 4.2 earthquake struck Japan today"
    assert text.topic_match_terms(hay, "earthquake Philippines") == []
    assert text.topic_match_terms("earthquake hits the Philippines", "earthquake Philippines")


def test_topic_match_accepts_alias():
    matched = text.topic_match_terms(
        "Malakas ang lindol sa Mindanao", "earthquake Philippines", ["lindol"]
    )
    assert "lindol" in matched


def test_topic_match_is_literal_not_semantic():
    """No stemming or synonyms: 'quake' must not match 'earthquake'."""
    assert text.topic_match_terms("a quake hit the country", "earthquake Philippines") == []


def test_distinctive_keywords_prefer_over_represented_terms():
    corpus = [
        "earthquake update one",
        "earthquake update two",
        "earthquake update three",
        "earthquake update four",
        "mayon volcano eruption",
        "mayon volcano eruption",
    ]
    members = ["mayon volcano eruption", "mayon volcano eruption"]
    kws = text.distinctive_keywords(members, corpus, top_n=3)
    assert "mayon" in kws


def test_detect_language_short_text_is_undetermined():
    lang, conf = text.detect_language("hi")
    assert lang is None


def test_detect_language_filipino():
    lang, _ = text.detect_language("Malakas ang lindol na tumama sa Mindanao kaninang umaga")
    assert lang == "tl"


def test_jaccard_identical():
    a = text.shingles("the quick brown fox jumps over the lazy dog")
    assert text.jaccard(a, a) == 1.0


def test_jaccard_disjoint():
    a = text.shingles("alpha beta gamma delta epsilon")
    b = text.shingles("one two three four five")
    assert text.jaccard(a, b) == 0.0
