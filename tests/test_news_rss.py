"""Google News RSS collector tests with the network mocked out."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from socmint.collection import news_rss
from socmint.models import EvidenceQuality, Platform
from socmint.security import FetchResult
from socmint.settings import Budgets

START = datetime(2026, 10, 20, tzinfo=UTC)
END = datetime(2026, 11, 5, tzinfo=UTC)

_FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>q - Google News</title>
<item>
  <title>Typhoon IN-FA monitored outside PAR</title>
  <link>https://news.google.com/rss/articles/AAA</link>
  <guid isPermaLink="false">AAA</guid>
  <pubDate>{in_window}</pubDate>
  <source url="https://www.pagasa.dost.gov.ph">PAGASA</source>
</item>
<item>
  <title>Older typhoon story</title>
  <link>https://news.google.com/rss/articles/BBB</link>
  <guid isPermaLink="false">BBB</guid>
  <pubDate>{too_old}</pubDate>
  <source url="https://example.com">Example</source>
</item>
</channel></rss>
"""

IN_WINDOW = "Wed, 04 Nov 2026 08:58:22 GMT"
TOO_OLD = "Mon, 01 Sep 2026 08:58:22 GMT"


def _feed(in_window: str = IN_WINDOW, too_old: str = TOO_OLD) -> str:
    return _FEED.format(in_window=in_window, too_old=too_old)


def _fake_get(body: str, status: int = 200):
    def _get(url, **kwargs):
        return FetchResult(status_code=status, text=body, url=url, headers={})

    return _get


# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------


def test_build_query_ors_the_aliases() -> None:
    assert news_rss.build_query("typhoon Philippines") == "typhoon Philippines"
    assert news_rss.build_query("a b", ["c"]) == "a b OR c"
    assert news_rss.build_query("   ") == ""


def test_build_query_adds_padded_date_operators() -> None:
    """Quoting plus a date operator returns zero items from Google News, so the
    query must stay unquoted and bracket the window generously."""
    query = news_rss.build_query("typhoon Philippines", start=START, end=END)
    assert '"' not in query
    assert "after:2026-10-19" in query  # one day before the window starts
    assert "before:2026-11-06" in query  # one day after it ends


def test_build_query_without_a_window_has_no_date_operators() -> None:
    assert "after:" not in news_rss.build_query("typhoon Philippines")


# ---------------------------------------------------------------------------
# Feed parsing
# ---------------------------------------------------------------------------


def test_parse_feed_maps_fields() -> None:
    posts = news_rss.parse_feed(_feed())
    assert len(posts) == 2
    post = posts[0]
    assert post.platform is Platform.GOOGLE_NEWS
    assert post.title == "Typhoon IN-FA monitored outside PAR"
    assert post.author_id == "www.pagasa.dost.gov.ph"
    assert post.author_name == "PAGASA"
    assert post.published_at == datetime(2026, 11, 4, 8, 58, 22, tzinfo=UTC)
    # Headline only: the article body is never fetched.
    assert post.evidence_quality is EvidenceQuality.PARTIAL


def test_parse_feed_rejects_a_dtd() -> None:
    """An entity declaration is refused before it reaches the parser."""
    hostile = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><rss/>'
    with pytest.raises(ValueError, match="DTD or entity"):
        news_rss.parse_feed(hostile)


def test_collect_surfaces_a_rejected_feed(monkeypatch) -> None:
    hostile = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><rss/>'
    monkeypatch.setattr(news_rss, "safe_get", _fake_get(hostile))
    result = news_rss.collect(
        "typhoon Philippines", window_start=START, window_end=END, budgets=Budgets()
    )
    assert result.posts == []
    assert result.status == "failed"
    assert any("rejected" in note for note in result.notes)


def test_parse_feed_tolerates_malformed_xml() -> None:
    assert news_rss.parse_feed("<rss><channel>") == []


def test_parse_feed_skips_items_without_a_title_or_link() -> None:
    xml = "<rss><channel><item><title></title><link>x</link></item></channel></rss>"
    assert news_rss.parse_feed(xml) == []


def test_parse_feed_marks_an_off_topic_headline_as_not_matching() -> None:
    """The unquoted query is loose, so relevance is decided per headline."""
    xml = (
        "<rss><channel>"
        "<item><title>Typhoon season explained</title>"
        "<link>https://news.google.com/rss/articles/A</link>"
        f"<pubDate>{IN_WINDOW}</pubDate></item>"
        "</channel></rss>"
    )
    posts = news_rss.parse_feed(xml, topic="typhoon Philippines")
    # Mentions "typhoon" but never "Philippines".
    assert posts[0].topic_match is False


def test_parse_feed_marks_an_on_topic_headline_as_matching() -> None:
    xml = (
        "<rss><channel>"
        "<item><title>Typhoon hits the Philippines</title>"
        "<link>https://news.google.com/rss/articles/B</link>"
        f"<pubDate>{IN_WINDOW}</pubDate></item>"
        "</channel></rss>"
    )
    posts = news_rss.parse_feed(xml, topic="typhoon Philippines")
    assert posts[0].topic_match is True


def test_parse_feed_falls_back_to_the_domain_when_source_is_absent() -> None:
    xml = (
        "<rss><channel><item><title>t</title>"
        "<link>https://news.google.com/rss/articles/X</link>"
        f"<pubDate>{IN_WINDOW}</pubDate></item></channel></rss>"
    )
    posts = news_rss.parse_feed(xml)
    assert posts[0].author_id == "news.google.com"


# ---------------------------------------------------------------------------
# Collection control flow
# ---------------------------------------------------------------------------


def test_collect_filters_to_the_window(monkeypatch) -> None:
    monkeypatch.setattr(news_rss, "safe_get", _fake_get(_feed()))
    result = news_rss.collect(
        "typhoon Philippines", window_start=START, window_end=END, budgets=Budgets()
    )
    assert [p.title for p in result.posts] == ["Typhoon IN-FA monitored outside PAR"]
    assert result.status == "ok"


def test_collect_reports_empty_rather_than_failure(monkeypatch) -> None:
    monkeypatch.setattr(news_rss, "safe_get", _fake_get(_feed(in_window=TOO_OLD)))
    result = news_rss.collect(
        "typhoon Philippines", window_start=START, window_end=END, budgets=Budgets()
    )
    assert result.posts == []
    assert result.status == "empty"


def test_collect_survives_a_transport_failure(monkeypatch) -> None:
    def _boom(url, **kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(news_rss, "safe_get", _boom)
    result = news_rss.collect(
        "typhoon Philippines", window_start=START, window_end=END, budgets=Budgets()
    )
    assert result.posts == []
    assert result.status == "failed"


def test_collect_reports_rate_limit(monkeypatch) -> None:
    monkeypatch.setattr(news_rss, "safe_get", _fake_get("", status=429))
    result = news_rss.collect(
        "typhoon Philippines", window_start=START, window_end=END, budgets=Budgets()
    )
    assert result.status == "rate_limited"
