"""GDELT news collector tests with the network mocked out.

The point of these is that a news record is *metadata only*: headline, domain,
publication time. Nothing fetches the article body, and nothing here may behave
as though it had.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from socmint.collection import gdelt
from socmint.models import EvidenceQuality, Platform
from socmint.security import FetchResult
from socmint.settings import Budgets

START = datetime(2026, 10, 20, tzinfo=UTC)
END = datetime(2026, 11, 3, tzinfo=UTC)


def _article(url: str, title: str, seendate: str, domain: str = "example.com") -> dict:
    return {
        "url": url,
        "title": title,
        "seendate": seendate,
        "domain": domain,
        "language": "English",
        "sourcecountry": "Philippines",
    }


def _fake_get(payload, status: int = 200):
    body = payload if isinstance(payload, str) else json.dumps(payload)

    def _get(url, **kwargs):
        return FetchResult(status_code=status, text=body, url=url, headers={})

    return _get


# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------


def test_build_query_quotes_a_single_topic() -> None:
    assert gdelt.build_query("typhoon Philippines") == '"typhoon Philippines"'


def test_build_query_ors_the_aliases() -> None:
    query = gdelt.build_query("typhoon Philippines", ["bagyo", "lindol"])
    assert query.startswith("(") and query.endswith(")")
    assert '"typhoon Philippines"' in query
    assert '"bagyo"' in query and '"lindol"' in query
    assert " OR " in query


def test_build_query_returns_empty_for_blank_input() -> None:
    assert gdelt.build_query("   ") == ""


# ---------------------------------------------------------------------------
# Article mapping
# ---------------------------------------------------------------------------


def test_parse_articles_maps_fields() -> None:
    posts = gdelt.parse_articles(
        {"articles": [_article("https://news.example/a", "Typhoon hits north", "20261025T100000Z")]}
    )
    assert len(posts) == 1
    post = posts[0]
    assert post.platform is Platform.GDELT
    assert post.title == "Typhoon hits north"
    assert post.text == "Typhoon hits north"
    assert post.author_id == "example.com"
    assert post.url == "https://news.example/a"
    assert post.published_at == datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
    # Metadata only: the article body was never retrieved.
    assert post.evidence_quality is EvidenceQuality.PARTIAL


def test_parse_articles_is_stable_for_the_same_url() -> None:
    payload = {"articles": [_article("https://news.example/a", "t", "20261025T100000Z")]}
    first = gdelt.parse_articles(payload)[0]
    second = gdelt.parse_articles(payload)[0]
    assert first.post_id == second.post_id


def test_parse_articles_skips_records_without_url_or_title() -> None:
    payload = {"articles": [{"url": "", "title": "x"}, {"url": "https://a.b/c", "title": ""}]}
    assert gdelt.parse_articles(payload) == []


def test_parse_articles_tolerates_a_missing_body() -> None:
    assert gdelt.parse_articles({}) == []
    assert gdelt.parse_articles({"articles": None}) == []


# ---------------------------------------------------------------------------
# Collection control flow
# ---------------------------------------------------------------------------


def test_collect_filters_to_the_window(monkeypatch) -> None:
    payload = {
        "articles": [
            _article("https://news.example/in", "typhoon Philippines in window", "20261025T100000Z"),
            _article("https://news.example/old", "typhoon Philippines too old", "20260901T100000Z"),
            _article("https://news.example/new", "typhoon Philippines too new", "20261110T100000Z"),
        ]
    }
    monkeypatch.setattr(gdelt, "safe_get", _fake_get(payload))
    result = gdelt.collect(
        "typhoon Philippines",
        window_start=START,
        window_end=END,
        budgets=Budgets(),
    )
    assert [p.url for p in result.posts] == ["https://news.example/in"]
    assert result.status == "ok"


def test_collect_reports_empty_rather_than_failure(monkeypatch) -> None:
    monkeypatch.setattr(gdelt, "safe_get", _fake_get({"articles": []}))
    result = gdelt.collect(
        "typhoon Philippines", window_start=START, window_end=END, budgets=Budgets()
    )
    assert result.posts == []
    assert result.status == "empty"
    assert any("no article inside the window" in note for note in result.notes)


def test_collect_retries_a_429_then_reports_rate_limited(monkeypatch) -> None:
    calls: list[str] = []

    def _get(url, **kwargs):
        calls.append(url)
        return FetchResult(status_code=429, text="", url=url, headers={})

    slept: list[float] = []
    monkeypatch.setattr(gdelt, "safe_get", _get)
    monkeypatch.setattr(gdelt.time, "sleep", slept.append)
    budgets = Budgets(retry_backoff_seconds=0.0)
    result = gdelt.collect(
        "typhoon Philippines", window_start=START, window_end=END, budgets=budgets
    )
    assert len(calls) == 3  # two retries
    # Every wait must clear GDELT's five-second window, even with a 0 backoff.
    assert all(delay >= 6.0 for delay in slept)
    assert result.status == "rate_limited"
    assert all(q.status == "rate_limited" for q in result.queries)


def test_collect_survives_a_transport_failure(monkeypatch) -> None:
    def _boom(url, **kwargs):
        raise RuntimeError("dns failure")

    monkeypatch.setattr(gdelt, "safe_get", _boom)
    result = gdelt.collect(
        "typhoon Philippines", window_start=START, window_end=END, budgets=Budgets()
    )
    assert result.posts == []
    assert result.status == "failed"
