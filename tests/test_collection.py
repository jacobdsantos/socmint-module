from __future__ import annotations

from datetime import UTC, datetime

from socmint.collection import discovery, telegram, youtube

TELEGRAM_HTML = """
<html><body>
<div class="tgme_channel_info_header_title">Quake Watch PH</div>
<div class="tgme_widget_message" data-post="quakewatchph/101">
  <div class="tgme_widget_message_text">Earthquake felt in Davao del Sur, Philippines.
  Magnitude 4.2 reported by PHIVOLCS.</div>
  <a class="tgme_widget_message_photo_wrap" style="background-image:url('https://cdn.example/p1.jpg')"></a>
  <span class="tgme_widget_message_views">1.2K</span>
  <time class="time" datetime="2026-10-21T08:15:30+00:00"></time>
</div>
<div class="tgme_widget_message" data-post="quakewatchph/100">
  <div class="tgme_widget_message_text">Global magnitude 6.1 event near Chile.</div>
  <time class="time" datetime="2026-10-20T22:00:00+00:00"></time>
</div>
<div class="tgme_widget_message" data-post="quakewatchph/99">
  <div class="tgme_widget_message_text">Undated notice with no time element.</div>
</div>
</body></html>
"""


def test_parse_channel_page_extracts_fields():
    posts, title = telegram.parse_channel_page(TELEGRAM_HTML, "quakewatchph")
    assert title == "Quake Watch PH"
    assert len(posts) == 3

    first = posts[0]
    assert first.post_id == "101"
    assert first.url == "https://t.me/quakewatchph/101"
    assert first.published_at == datetime(2026, 10, 21, 8, 15, 30, tzinfo=UTC)
    assert first.precision == "second"
    assert first.engagement is not None and first.engagement.views == 1200
    assert len(first.media) == 1
    assert "Philippines" in first.text


def test_parse_channel_page_undated_post_has_no_timestamp():
    posts, _ = telegram.parse_channel_page(TELEGRAM_HTML, "quakewatchph")
    undated = posts[2]
    assert undated.published_at is None
    assert undated.precision == "unknown"
    assert undated.evidence_quality == "partial"


def test_parse_channel_page_ignores_unrelated_blocks():
    posts, _ = telegram.parse_channel_page("<html><body>nothing</body></html>", "x")
    assert posts == []


def test_youtube_entry_uses_timestamp():
    entry = {
        "id": "abc123",
        "title": "Philippines earthquake update",
        "description": "Details",
        "channel": "Chan",
        "channel_id": "UC123",
        "timestamp": 1793581210,
        "view_count": 100,
        "like_count": 5,
        "comment_count": 2,
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "thumbnail": "https://i.ytimg.com/vi/abc123/hq.jpg",
    }
    post = youtube.entry_to_post(entry)
    assert post is not None
    assert post.post_id == "abc123"
    assert post.author_id == "UC123"
    assert post.precision == "second"
    assert post.engagement.views == 100


def test_youtube_entry_falls_back_to_upload_date():
    post = youtube.entry_to_post(
        {"id": "x", "title": "t", "upload_date": "20261020", "view_count": None}
    )
    assert post is not None
    assert post.precision == "day"
    assert post.published_at.date().isoformat() == "2026-10-20"
    assert post.engagement.views is None


def test_youtube_entry_without_id_is_skipped():
    assert youtube.entry_to_post({"title": "no id"}) is None


def test_telegram_channel_url_parsing():
    assert discovery.parse_telegram_channel("https://t.me/s/foo_bar") == "foo_bar"
    assert discovery.parse_telegram_channel("https://t.me/foo_bar?before=10") == "foo_bar"
    assert discovery.parse_telegram_channel("https://t.me/joinchat/xyz") is None
    assert discovery.parse_telegram_channel("https://example.com/foo") is None


def test_youtube_queries_are_date_bounded():
    start = datetime(2026, 10, 20, tzinfo=UTC)
    end = datetime(2026, 11, 3, tzinfo=UTC)
    queries = discovery.youtube_queries("earthquake Philippines", [], start, end)
    assert queries
    assert "after:2026-10-19" in queries[0]
    assert "before:2026-11-03" in queries[0]
