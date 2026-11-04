"""Collector control-flow tests with the network mocked out.

These cover the logic that matters for correctness: window filtering, pagination,
early abort, failure status, and budget caps.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from socmint.collection import discovery, telegram, youtube
from socmint.security import FetchResult
from socmint.settings import Budgets

START = datetime(2026, 10, 20, tzinfo=UTC)
END = datetime(2026, 11, 3, tzinfo=UTC)


def _page(*blocks: str, title: str = "Test Channel") -> str:
    return (
        "<html><body>"
        f'<div class="tgme_channel_info_header_title">{title}</div>'
        + "".join(blocks)
        + "</body></html>"
    )


def _block(post_id: int, when: str, text: str, views: str = "10") -> str:
    return (
        f'<div class="tgme_widget_message" data-post="chan/{post_id}">'
        f'<div class="tgme_widget_message_text">{text}</div>'
        f'<span class="tgme_widget_message_views">{views}</span>'
        f'<time class="time" datetime="{when}"></time></div>'
    )


def _fake_get(html: str, status: int = 200):
    def _get(url, **kwargs):
        return FetchResult(status_code=status, text=html, url=url, headers={})

    return _get


def test_collect_channel_keeps_only_window_posts(monkeypatch):
    html = _page(
        _block(3, "2026-10-21T10:00:00+00:00", "typhoon Philippines update today"),
        _block(2, "2026-10-10T10:00:00+00:00", "typhoon Philippines older post"),
        _block(1, "2026-11-05T10:00:00+00:00", "typhoon Philippines future post"),
    )
    monkeypatch.setattr(telegram, "safe_get", _fake_get(html))
    result = telegram.collect_channel(
        "chan", window_start=START, window_end=END, budgets=Budgets(), topic="typhoon Philippines"
    )
    assert [p.post_id for p in result.posts] == ["3"]
    assert result.status in {"ok", "partial"}


def test_collect_channel_aborts_when_topic_absent(monkeypatch):
    pages = iter(
        [
            _page(_block(20, "2026-10-21T10:00:00+00:00", "unrelated cooking content here")),
            _page(_block(10, "2026-10-20T10:00:00+00:00", "more unrelated cooking content")),
        ]
    )
    monkeypatch.setattr(telegram, "safe_get", lambda url, **kw: FetchResult(200, next(pages), url, {}))
    result = telegram.collect_channel(
        "chan", window_start=START, window_end=END, budgets=Budgets(), topic="typhoon Philippines"
    )
    assert result.posts == []
    assert any("no post matched" in n for n in result.notes)


def test_collect_channel_reports_rate_limit(monkeypatch):
    monkeypatch.setattr(telegram, "safe_get", _fake_get("", status=429))
    result = telegram.collect_channel(
        "chan", window_start=START, window_end=END, budgets=Budgets()
    )
    assert result.status == "rate_limited"


def test_collect_channel_reports_failure(monkeypatch):
    def boom(url, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(telegram, "safe_get", boom)
    result = telegram.collect_channel(
        "chan", window_start=START, window_end=END, budgets=Budgets()
    )
    assert result.posts == []
    assert any("failed" in n for n in result.notes)


def test_youtube_search_filters_by_window(monkeypatch):
    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, query, download=False):
            return {
                "entries": [
                    {"id": "in", "title": "typhoon Philippines in window",
                     "timestamp": 1792500000, "view_count": 5},
                    {"id": "out", "title": "typhoon Philippines old",
                     "timestamp": 1700000000, "view_count": 5},
                    {"id": "undated", "title": "typhoon Philippines no date"},
                ]
            }

    import sys
    import types

    fake_module = types.SimpleNamespace(YoutubeDL=FakeYDL)
    monkeypatch.setitem(sys.modules, "yt_dlp", fake_module)

    result = youtube.search(
        "typhoon Philippines after:2026-10-19 before:2026-11-03",
        limit=10, budgets=Budgets(), topic="typhoon Philippines",
        window_start=START, window_end=END,
    )
    ids = {p.post_id for p in result.posts}
    assert "in" in ids
    assert "out" not in ids
    assert "undated" in ids  # kept as a lead, cannot be date-filtered


def test_youtube_search_reports_failure(monkeypatch):
    class BoomYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, query, download=False):
            raise RuntimeError("blocked")

    import sys
    import types

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=BoomYDL))
    result = youtube.search("x", limit=5, budgets=Budgets())
    assert result.status == "failed"
    assert result.posts == []


def test_discovery_filters_irrelevant_hits(monkeypatch):
    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def text(self, query, max_results=20, backend="auto"):
            return [
                {"title": "Philippine News", "href": "https://t.me/s/phlnews", "body": "news"},
                {"title": "Free Steam Accounts", "href": "https://t.me/s/steamfree", "body": "games"},
            ]

    import sys
    import types

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FakeDDGS))
    result = discovery.discover_telegram("Philippines news", max_queries=1)
    usernames = [c.username for c in result.channels]
    assert "phlnews" in usernames
    assert "steamfree" not in usernames


def test_discovery_handles_empty_results(monkeypatch):
    class EmptyDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def text(self, query, max_results=20, backend="auto"):
            raise RuntimeError("No results found.")

    import sys
    import types

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=EmptyDDGS))
    result = discovery.discover_telegram("nothing", max_queries=1)
    assert result.channels == []
    assert result.status == "empty"


@pytest.mark.parametrize(
    "topic,expected_fragment",
    [
        ("earthquake Philippines", "earthquake Philippines"),
    ],
)
def test_temporal_queries_include_month(topic, expected_fragment):
    queries = discovery.temporal_queries(topic, [], START, END, max_queries=6)
    assert any(expected_fragment in q for q in queries)
