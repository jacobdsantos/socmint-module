"""Mastodon collector tests with the network mocked out.

Covers the parts that decide correctness: tag derivation from a topic, status
mapping, window filtering, and the instance whose public preview is disabled.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from socmint.collection import mastodon
from socmint.models import Platform
from socmint.security import FetchResult
from socmint.settings import Budgets

START = datetime(2026, 10, 20, tzinfo=UTC)
END = datetime(2026, 11, 3, tzinfo=UTC)


def _status(
    status_id: str,
    when: str,
    text: str,
    *,
    acct: str = "alice",
    reblog: dict | None = None,
) -> dict:
    return {
        "id": status_id,
        "created_at": when,
        "content": f"<p>{text}</p>",
        "url": f"https://mastodon.social/@{acct}/{status_id}",
        "uri": f"https://mastodon.social/users/{acct}/statuses/{status_id}",
        "language": "en",
        "account": {"acct": acct, "username": acct, "display_name": acct.title()},
        "favourites_count": 3,
        "reblogs_count": 1,
        "replies_count": 0,
        "reblog": reblog,
        "in_reply_to_id": None,
        "media_attachments": [],
    }


def _fake_get(payload, status: int = 200):
    body = payload if isinstance(payload, str) else json.dumps(payload)

    def _get(url, **kwargs):
        return FetchResult(status_code=status, text=body, url=url, headers={})

    return _get


# ---------------------------------------------------------------------------
# Tag derivation
# ---------------------------------------------------------------------------


def test_candidate_tags_tries_joined_form_before_words() -> None:
    tags = mastodon.candidate_tags("typhoon Philippines")
    assert tags[0] == "typhoonphilippines"
    assert "typhoon" in tags and "philippines" in tags


def test_candidate_tags_accepts_an_explicit_hashtag() -> None:
    assert mastodon.candidate_tags("#WestPhilippineSea")[0] == "westphilippinesea"


def test_candidate_tags_rejects_unusable_input() -> None:
    assert mastodon.candidate_tags("  ") == []
    # A single character is not a valid tag.
    assert mastodon.candidate_tags("x") == []


def test_candidate_tags_respects_the_limit() -> None:
    assert len(mastodon.candidate_tags("one two three four five", limit=3)) == 3


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------


def test_parse_statuses_maps_fields() -> None:
    posts = mastodon.parse_statuses(
        [_status("111", "2026-10-25T10:00:00.000Z", "Typhoon update for the north")],
        "mastodon.social",
    )
    assert len(posts) == 1
    post = posts[0]
    assert post.platform is Platform.MASTODON
    # The canonical uri, not the instance-local id, so federated copies collapse.
    assert post.post_id == "https://mastodon.social/users/alice/statuses/111"
    assert post.text == "Typhoon update for the north"
    assert post.author_id == "alice"
    assert post.author_name == "Alice"
    assert post.published_at == datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
    assert post.engagement is not None
    assert post.engagement.likes == 3
    assert post.engagement.shares == 1
    assert post.provenance == "mastodon.social/api/v1/timelines/tag"


def test_parse_statuses_marks_a_boost_as_a_repost() -> None:
    original = _status("222", "2026-10-24T09:00:00.000Z", "original words", acct="bob")
    boost = _status("333", "2026-10-25T09:00:00.000Z", "", acct="alice", reblog=original)
    posts = mastodon.parse_statuses([boost], "mastodon.social")
    assert posts[0].is_repost is True
    # The text comes from the boosted status, not the empty wrapper.
    assert posts[0].text == "original words"


def test_parse_statuses_skips_records_without_an_id() -> None:
    assert mastodon.parse_statuses([{"content": "<p>x</p>"}], "mastodon.social") == []


def test_federated_copies_of_one_status_share_an_identity() -> None:
    """The same status seen on two instances must not become two posts."""
    status = _status("111", "2026-10-25T10:00:00.000Z", "typhoon update")
    remote = dict(status, id="999")  # different local id, same canonical uri
    local_post = mastodon.parse_statuses([status], "mastodon.social")[0]
    remote_post = mastodon.parse_statuses([remote], "mastodon.online")[0]
    assert local_post.identity == remote_post.identity


# ---------------------------------------------------------------------------
# Collection control flow
# ---------------------------------------------------------------------------


def test_collect_tag_keeps_only_window_posts(monkeypatch) -> None:
    page = [
        _status("3", "2026-10-25T10:00:00.000Z", "typhoon Philippines update"),
        _status("2", "2026-10-01T10:00:00.000Z", "typhoon Philippines older"),
        _status("1", "2026-11-10T10:00:00.000Z", "typhoon Philippines future"),
    ]
    monkeypatch.setattr(mastodon, "safe_get", _fake_get(page))
    result = mastodon.collect_tag(
        "typhoonphilippines",
        instances=("mastodon.social",),
        window_start=START,
        window_end=END,
        budgets=Budgets(),
        topic="typhoon Philippines",
    )
    assert [p.url.rsplit("/", 1)[-1] for p in result.posts] == ["3"]


def test_collect_tag_skips_an_instance_that_requires_auth(monkeypatch) -> None:
    monkeypatch.setattr(mastodon, "safe_get", _fake_get({"error": "unauthorized"}, status=401))
    result = mastodon.collect_tag(
        "typhoonphilippines",
        instances=("mastodon.social",),
        window_start=START,
        window_end=END,
        budgets=Budgets(),
    )
    assert result.posts == []
    assert result.queries[0].status == "blocked_auth"
    assert any("instance skipped" in note for note in result.notes)


def test_collect_tag_reports_rate_limit(monkeypatch) -> None:
    monkeypatch.setattr(mastodon, "safe_get", _fake_get([], status=429))
    result = mastodon.collect_tag(
        "typhoonphilippines",
        instances=("mastodon.social",),
        window_start=START,
        window_end=END,
        budgets=Budgets(),
    )
    assert result.status == "rate_limited"


def test_collect_tag_survives_a_transport_failure(monkeypatch) -> None:
    def _boom(url, **kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(mastodon, "safe_get", _boom)
    result = mastodon.collect_tag(
        "typhoonphilippines",
        instances=("mastodon.social",),
        window_start=START,
        window_end=END,
        budgets=Budgets(),
    )
    assert result.posts == []
    assert any("failed" in note for note in result.notes)


def test_collect_reports_when_no_tag_can_be_derived() -> None:
    result = mastodon.collect(
        "  ",
        window_start=START,
        window_end=END,
        budgets=Budgets(),
    )
    assert result.posts == []
    assert any("no usable hashtag" in note for note in result.notes)
