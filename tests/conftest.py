from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from socmint.models import Engagement, Platform, Post, Precision

BASE = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def make_post(
    *,
    platform: Platform = Platform.TELEGRAM,
    post_id: str,
    author_id: str,
    text: str,
    minutes: float = 0,
    base: datetime = BASE,
    urls: list[str] | None = None,
    title: str | None = None,
    views: int | None = 10,
    precision: Precision = Precision.SECOND,
    language: str | None = "en",
    topic_match: bool = True,
) -> Post:
    return Post(
        platform=platform,
        post_id=post_id,
        url=f"https://example.invalid/{post_id}",
        author_id=author_id,
        author_name=author_id,
        text=text,
        title=title,
        language=language,
        published_at=base + timedelta(minutes=minutes),
        precision=precision,
        published_source="test",
        engagement=Engagement(views=views),
        referenced_urls=urls or [],
        topic_match=topic_match,
        provenance="fixture",
    )


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch):
    """Fail fast instead of reaching the internet from a test.

    ``pipeline.collect`` runs every enabled platform, so a test that mocks only
    the one it cares about would silently make live requests to the others --
    slow, flaky, and a lie about what the suite covers. This blocks the fetch
    entry point in each collector module; a test that wants a response patches
    it in its own body, which runs after this fixture and wins.
    """
    from socmint.collection import gdelt, mastodon, news_rss, reddit

    def _blocked(*args, **kwargs):
        raise RuntimeError("live network access is blocked in tests")

    for module in (gdelt, mastodon, news_rss, reddit):
        # Reddit goes through safe_request (it needs POST for the token), so
        # patching only safe_get would leave it free to reach the network.
        for name in ("safe_get", "safe_request"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, _blocked)


@pytest.fixture
def coordinated_pair() -> list[Post]:
    """Two accounts posting near-identical text within minutes, twice."""
    return [
        make_post(post_id="a1", author_id="acct_a", text="BREAKING: Acme Bank has collapsed and all deposits are frozen today", minutes=0),
        make_post(post_id="b1", author_id="acct_b", text="BREAKING: Acme Bank has collapsed and all deposits are frozen today", minutes=3),
        make_post(post_id="a2", author_id="acct_a", text="URGENT: Acme Bank customers cannot withdraw cash as branches shut down", minutes=600),
        make_post(post_id="b2", author_id="acct_b", text="URGENT: Acme Bank customers cannot withdraw cash as branches shut down", minutes=602),
    ]


@pytest.fixture
def organic_burst() -> list[Post]:
    """Many accounts, similar topic, but different wording and spread over hours."""
    return [
        make_post(post_id="o1", author_id="news_1", text="A magnitude 4.2 earthquake struck near Davao del Sur this morning, authorities said", minutes=0),
        make_post(post_id="o2", author_id="news_2", text="Residents in Davao felt shaking after a 4.2 quake; no damage reported so far", minutes=95),
        make_post(post_id="o3", author_id="news_3", text="Phivolcs recorded a 4.2 magnitude tremor in Davao del Sur on Tuesday", minutes=240),
        make_post(post_id="o4", author_id="news_4", text="Classes suspended in Davao after an earthquake was felt across the province", minutes=400),
    ]
