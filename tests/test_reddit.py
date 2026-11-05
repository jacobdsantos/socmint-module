"""Reddit collector tests with the network mocked out.

The important behaviours: an unconfigured client reports ``unsupported`` rather
than an empty result, the token is cached, and the window filter holds.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from socmint.collection import reddit
from socmint.models import EvidenceQuality, Platform
from socmint.security import FetchResult
from socmint.settings import Budgets, RedditCredentials

START = datetime(2026, 10, 20, tzinfo=UTC)
END = datetime(2026, 11, 5, tzinfo=UTC)

CREDS = RedditCredentials(client_id="id", client_secret="secret")


def _child(
    post_id: str,
    title: str,
    created: float,
    *,
    author: str = "someone",
    selftext: str = "",
    score: int = 10,
    comments: int = 3,
) -> dict:
    return {
        "kind": "t3",
        "data": {
            "id": post_id,
            "title": title,
            "selftext": selftext,
            "author": author,
            "permalink": f"/r/test/comments/{post_id}/x/",
            "created_utc": created,
            "score": score,
            "num_comments": comments,
            "subreddit": "test",
        },
    }


def _listing(*children: dict, after: str | None = None) -> dict:
    return {"data": {"children": list(children), "after": after}}


def _epoch(*args: int) -> float:
    return datetime(*args, tzinfo=UTC).timestamp()


def _fake(payload, status: int = 200):
    body = payload if isinstance(payload, str) else json.dumps(payload)

    def _request(method, url, **kwargs):
        return FetchResult(status_code=status, text=body, url=url, headers={})

    return _request


def _token_then_listing(listing: dict):
    """Token request returns a token; the search returns the listing."""

    def _request(method, url, **kwargs):
        if "access_token" in url:
            body = json.dumps({"access_token": "tok", "expires_in": 86400})
        else:
            body = json.dumps(listing)
        return FetchResult(status_code=200, text=body, url=url, headers={})

    return _request


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def test_unconfigured_client_is_unsupported_not_empty() -> None:
    """No key is not the same as no posts, and must not look like it."""
    result = reddit.collect(
        "typhoon Philippines",
        window_start=START,
        window_end=END,
        budgets=Budgets(),
        credentials=RedditCredentials(),
    )
    assert result.posts == []
    assert result.status == "unsupported"
    assert any("REDDIT_CLIENT_ID" in note for note in result.notes)


def test_token_is_cached_between_calls(monkeypatch) -> None:
    calls: list[str] = []

    def _request(method, url, **kwargs):
        calls.append(url)
        return FetchResult(
            status_code=200,
            text=json.dumps({"access_token": "tok", "expires_in": 86400}),
            url=url,
            headers={},
        )

    monkeypatch.setattr(reddit, "safe_request", _request)
    reddit._TOKEN_CACHE.clear()
    reddit.get_token(CREDS, Budgets())
    reddit.get_token(CREDS, Budgets())
    assert len(calls) == 1


def test_bad_credentials_report_the_http_status(monkeypatch) -> None:
    monkeypatch.setattr(reddit, "safe_request", _fake({"message": "Unauthorized"}, status=401))
    reddit._TOKEN_CACHE.clear()
    token, error = reddit.get_token(CREDS, Budgets())
    assert token is None
    assert error is not None and "401" in error


# ---------------------------------------------------------------------------
# Listing mapping
# ---------------------------------------------------------------------------


def test_parse_listing_maps_fields() -> None:
    posts = reddit.parse_listing(_listing(_child("abc", "Typhoon hits north", _epoch(2026, 10, 25))))
    assert len(posts) == 1
    post = posts[0]
    assert post.platform is Platform.REDDIT
    assert post.post_id == "abc"
    assert post.title == "Typhoon hits north"
    assert post.url == "https://www.reddit.com/r/test/comments/abc/x/"
    assert post.author_id == "someone"
    assert post.engagement is not None
    assert post.engagement.likes == 10
    assert post.engagement.comments == 3
    assert post.evidence_quality is EvidenceQuality.ORIGINAL


def test_parse_listing_skips_records_without_an_id_or_permalink() -> None:
    payload = {"data": {"children": [{"data": {"id": "", "permalink": "/x"}}]}}
    assert reddit.parse_listing(payload) == []


def test_parse_listing_tolerates_an_empty_body() -> None:
    assert reddit.parse_listing({}) == []
    assert reddit.parse_listing({"data": None}) == []


def test_parse_listing_marks_off_topic_posts() -> None:
    payload = _listing(_child("abc", "Something unrelated", _epoch(2026, 10, 25)))
    posts = reddit.parse_listing(payload, topic="typhoon Philippines")
    assert posts[0].topic_match is False


# ---------------------------------------------------------------------------
# Collection control flow
# ---------------------------------------------------------------------------


def test_collect_keeps_only_window_posts(monkeypatch) -> None:
    listing = _listing(
        _child("in", "typhoon Philippines update", _epoch(2026, 10, 25)),
        _child("old", "typhoon Philippines older", _epoch(2026, 9, 1)),
        _child("new", "typhoon Philippines future", _epoch(2026, 11, 10)),
    )
    monkeypatch.setattr(reddit, "safe_request", _token_then_listing(listing))
    reddit._TOKEN_CACHE.clear()
    result = reddit.collect(
        "typhoon Philippines",
        window_start=START,
        window_end=END,
        budgets=Budgets(),
        credentials=CREDS,
    )
    assert [p.post_id for p in result.posts] == ["in"]
    assert result.status == "ok"


def test_collect_survives_a_transport_failure(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(reddit, "safe_request", _boom)
    reddit._TOKEN_CACHE.clear()
    result = reddit.collect(
        "typhoon Philippines",
        window_start=START,
        window_end=END,
        budgets=Budgets(),
        credentials=CREDS,
    )
    assert result.posts == []
    assert any("token request failed" in note for note in result.notes)


def test_collect_reports_a_401_as_blocked_auth(monkeypatch) -> None:
    def _request(method, url, **kwargs):
        if "access_token" in url:
            body = json.dumps({"access_token": "tok", "expires_in": 86400})
            return FetchResult(status_code=200, text=body, url=url, headers={})
        return FetchResult(status_code=401, text="{}", url=url, headers={})

    monkeypatch.setattr(reddit, "safe_request", _request)
    reddit._TOKEN_CACHE.clear()
    result = reddit.collect(
        "typhoon Philippines",
        window_start=START,
        window_end=END,
        budgets=Budgets(),
        credentials=CREDS,
    )
    assert result.status == "blocked_auth"
