"""Guards that keep news records from being mistaken for social evidence.

A news article has a domain, not an account. It cannot coordinate with anyone,
and a headline that repeats the topic is not a second account corroborating a
social post. Both rules are enforced in code, so both are asserted here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from socmint.analysis import coordination, grading
from socmint.models import NarrativeCandidate, Platform, Post, Precision
from socmint.settings import AnalysisSettings

BASE = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)


def make_post(
    post_id: str,
    text: str,
    *,
    author: str,
    platform: Platform,
    minutes: int = 0,
) -> Post:
    return Post(
        platform=platform,
        post_id=post_id,
        url=f"https://example.test/{author}/{post_id}",
        author_id=author,
        author_name=author,
        text=text,
        published_at=BASE + timedelta(minutes=minutes),
        precision=Precision.SECOND,
    )


# ---------------------------------------------------------------------------
# Admiralty information grade
# ---------------------------------------------------------------------------


def test_news_article_does_not_count_as_a_second_account() -> None:
    """Two social accounts plus one news article is still two accounts."""
    posts = [
        make_post("1", "the bank collapsed", author="a", platform=Platform.TELEGRAM),
        make_post("2", "the bank collapsed", author="b", platform=Platform.YOUTUBE),
        make_post("3", "the bank collapsed", author="news.example", platform=Platform.GDELT),
        make_post("4", "filler one", author="c", platform=Platform.TELEGRAM),
        make_post("5", "filler two", author="d", platform=Platform.TELEGRAM),
        make_post("6", "filler three", author="e", platform=Platform.TELEGRAM),
        make_post("7", "filler four", author="f", platform=Platform.TELEGRAM),
    ]
    cluster = NarrativeCandidate(
        narrative_id="n1",
        label="collapse",
        member_ids=["telegram:1", "youtube:2", "gdelt:3"],
    )
    grading.grade(posts, [cluster])
    # Two accounts is "possibly true" (3), not "probably true" (2).
    assert posts[0].admiralty.info.value == "3"


def test_a_cluster_of_only_news_is_not_graded_on_social_support() -> None:
    posts = [
        make_post("1", "the bank collapsed", author="a.example", platform=Platform.GDELT),
        make_post("2", "the bank collapsed", author="b.example", platform=Platform.GDELT),
        make_post("3", "filler", author="c", platform=Platform.TELEGRAM),
    ]
    cluster = NarrativeCandidate(
        narrative_id="n1", label="collapse", member_ids=["gdelt:1", "gdelt:2"]
    )
    grading.grade(posts, [cluster])
    assert posts[0].admiralty.info.value == "6"
    assert posts[1].admiralty.info.value == "6"


# ---------------------------------------------------------------------------
# Coordination
# ---------------------------------------------------------------------------


def test_news_records_cannot_form_a_coordination_group() -> None:
    """Identical headlines from two outlets is syndication, not coordination."""
    posts = [
        make_post("1", "the bank collapsed today", author="a.example", platform=Platform.GDELT),
        make_post(
            "2",
            "the bank collapsed today",
            author="b.example",
            platform=Platform.GDELT,
            minutes=2,
        ),
    ]
    result = coordination.detect(posts, settings=AnalysisSettings())
    assert result.groups == []


_LONG = (
    "the bank collapsed today and all customer deposits are frozen effective immediately"
)


def test_news_records_do_not_join_a_social_group() -> None:
    social = [
        make_post("1", _LONG, author="acct_a", platform=Platform.TELEGRAM),
        make_post("2", _LONG, author="acct_b", platform=Platform.TELEGRAM, minutes=2),
        make_post("3", _LONG, author="acct_a", platform=Platform.TELEGRAM, minutes=40),
        make_post("4", _LONG, author="acct_b", platform=Platform.TELEGRAM, minutes=42),
    ]
    news = [
        make_post("5", _LONG, author="a.example", platform=Platform.GDELT),
        make_post("6", _LONG, author="b.example", platform=Platform.GDELT, minutes=3),
    ]
    result = coordination.detect(social + news, settings=AnalysisSettings())
    members = {member for group in result.groups for member in group.account_ids}
    assert "acct_a" in members and "acct_b" in members
    assert "a.example" not in members
    assert "b.example" not in members
