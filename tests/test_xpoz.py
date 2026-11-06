"""X collector tests with the Xpoz SDK faked out.

No network, no key, no credits. The behaviours that matter: the collector
refuses to run without a key or without spend permission, and a failed paid call
is refunded rather than silently kept.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from socmint.collection import xpoz
from socmint.models import EvidenceQuality, Platform
from socmint.settings import Budgets, CreditPolicy, XpozCredentials
from socmint.spend import CreditLedger
from socmint.store import Store

START = datetime(2026, 10, 20, tzinfo=UTC)
END = datetime(2026, 11, 5, tzinfo=UTC)
KEYED = XpozCredentials(api_key="test-key")


@pytest.fixture
def ledger(tmp_path):
    with Store(tmp_path / "s.db") as store:
        yield CreditLedger(store, CreditPolicy(allow_spend=True))


def _tweet(
    post_id: str,
    text: str,
    created: str,
    *,
    handle: str = "someone",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=post_id,
        text=text,
        author_username=handle,
        author_id="12345",
        lang="en",
        created_at=created,
        created_at_date=created[:10],
        like_count=5,
        retweet_count=2,
        reply_count=1,
        impression_count=100,
        is_retweet=False,
        reply_to_tweet_id=None,
        urls=[],
        media_urls=[],
    )


class _FakeTwitter:
    def __init__(self, posts):
        self._posts = posts
        self.calls: list[dict] = []

    def search_posts(self, query, **kwargs):
        self.calls.append({"query": query, **kwargs})
        return SimpleNamespace(data=self._posts)


class _FakeClient:
    def __init__(self, posts):
        self.twitter = _FakeTwitter(posts)
        self.closed = False

    def close(self):
        self.closed = True


class _ExplodingClient:
    def __init__(self):
        self.twitter = self
        self.closed = False

    def search_posts(self, *args, **kwargs):
        raise RuntimeError("upstream 500")

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# Refusals -- none of these may spend a credit
# ---------------------------------------------------------------------------


def test_missing_sdk_is_unsupported(monkeypatch, ledger) -> None:
    monkeypatch.setattr(xpoz, "_import_sdk", lambda: (None, "xpoz is not installed"))
    result = xpoz.collect(
        "typhoon Philippines",
        window_start=START,
        window_end=END,
        budgets=Budgets(),
        credentials=KEYED,
        ledger=ledger,
    )
    assert result.status == "unsupported"
    assert result.credits_spent == 0
    assert ledger.spent == 0


def test_missing_key_is_unsupported(monkeypatch, ledger) -> None:
    monkeypatch.setattr(xpoz, "_import_sdk", lambda: (SimpleNamespace(), None))
    result = xpoz.collect(
        "typhoon Philippines",
        window_start=START,
        window_end=END,
        budgets=Budgets(),
        credentials=XpozCredentials(),
        ledger=ledger,
    )
    assert result.status == "unsupported"
    assert any("XPOZ_API_KEY" in note for note in result.notes)
    assert ledger.spent == 0


def test_spending_disabled_blocks_the_run(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(xpoz, "_import_sdk", lambda: (SimpleNamespace(), None))
    with Store(tmp_path / "off.db") as store:
        ledger = CreditLedger(store, CreditPolicy(allow_spend=False))
        result = xpoz.collect(
            "typhoon Philippines",
            window_start=START,
            window_end=END,
            budgets=Budgets(),
            credentials=KEYED,
            ledger=ledger,
        )
        assert result.status == "blocked_policy"
        assert any("disabled" in note for note in result.notes)
        assert ledger.spent == 0


def test_exhausted_reserve_blocks_the_run(ledger) -> None:
    ledger.charge(300)  # eats everything testable, leaving the 200 reserve
    result = xpoz.collect(
        "typhoon Philippines",
        window_start=START,
        window_end=END,
        budgets=Budgets(),
        credentials=KEYED,
        ledger=ledger,
        client_factory=lambda: _FakeClient([]),
    )
    assert result.status == "blocked_policy"
    assert any("demo reserve" in note for note in result.notes)


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def test_to_post_maps_fields() -> None:
    post = xpoz.to_post(_tweet("111", "typhoon update", "2026-10-25T10:00:00Z"))
    assert post is not None
    assert post.platform is Platform.X
    assert post.post_id == "111"
    assert post.url == "https://x.com/someone/status/111"
    assert post.published_at == datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
    assert post.engagement is not None
    assert post.engagement.likes == 5
    assert post.engagement.shares == 2
    assert post.evidence_quality is EvidenceQuality.ORIGINAL


def test_to_post_falls_back_to_the_date_field() -> None:
    tweet = SimpleNamespace(id="9", text="x", author_username="a", created_at=None,
                            created_at_date="2026-10-24", lang="en")
    post = xpoz.to_post(tweet)
    assert post is not None
    assert post.published_at == datetime(2026, 10, 24, tzinfo=UTC)
    assert post.precision == "day"


def test_to_post_returns_none_without_an_id() -> None:
    assert xpoz.to_post(SimpleNamespace(id=None, text="x")) is None


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def test_collect_charges_once_and_maps_results(ledger) -> None:
    client = _FakeClient([_tweet("1", "typhoon Philippines update", "2026-10-25T10:00:00Z")])
    result = xpoz.collect(
        "typhoon Philippines",
        window_start=START,
        window_end=END,
        budgets=Budgets(),
        credentials=KEYED,
        ledger=ledger,
        run_id="r1",
        client_factory=lambda: client,
    )
    assert result.status == "ok"
    assert [p.post_id for p in result.posts] == ["1"]
    assert result.credits_spent == 2
    assert ledger.spent == 2
    assert client.closed is True
    # One billable call, bounded by the post budget.
    assert len(client.twitter.calls) == 1
    assert client.twitter.calls[0]["start_date"] == "2026-10-20"
    assert client.twitter.calls[0]["end_date"] == "2026-11-05"


def test_collect_filters_off_topic_and_out_of_window(ledger) -> None:
    client = _FakeClient(
        [
            _tweet("1", "typhoon Philippines update", "2026-10-25T10:00:00Z"),
            _tweet("2", "something unrelated entirely", "2026-10-25T10:00:00Z"),
            _tweet("3", "typhoon Philippines but too old", "2026-09-01T10:00:00Z"),
        ]
    )
    result = xpoz.collect(
        "typhoon Philippines",
        window_start=START,
        window_end=END,
        budgets=Budgets(),
        credentials=KEYED,
        ledger=ledger,
        client_factory=lambda: client,
    )
    assert [p.post_id for p in result.posts] == ["1"]


def test_a_failed_call_is_refunded(ledger) -> None:
    result = xpoz.collect(
        "typhoon Philippines",
        window_start=START,
        window_end=END,
        budgets=Budgets(),
        credentials=KEYED,
        ledger=ledger,
        client_factory=_ExplodingClient,
    )
    assert result.status == "failed"
    assert result.credits_spent == 0
    # Charged then refunded: the net must be zero, and both entries are kept.
    assert ledger.spent == 0
    assert len(ledger.history()) == 2


def test_empty_result_is_empty_not_failed(ledger) -> None:
    result = xpoz.collect(
        "typhoon Philippines",
        window_start=START,
        window_end=END,
        budgets=Budgets(),
        credentials=KEYED,
        ledger=ledger,
        client_factory=lambda: _FakeClient([]),
    )
    assert result.status == "empty"
    # The call succeeded, so the credit was spent even though nothing came back.
    assert ledger.spent == 2


# ---------------------------------------------------------------------------
# Per-query cap and call cooldown
# ---------------------------------------------------------------------------


def _many_tweets(n: int) -> list[SimpleNamespace]:
    return [
        _tweet(str(i), f"typhoon Philippines bulletin {i}", "2026-10-25T10:00:00Z")
        for i in range(n)
    ]


def test_results_are_capped_per_query(tmp_path) -> None:
    with Store(tmp_path / "cap.db") as store:
        ledger = CreditLedger(
            store, CreditPolicy(allow_spend=True, max_results_per_query=5)
        )
        client = _FakeClient(_many_tweets(25))
        result = xpoz.collect(
            "typhoon Philippines",
            window_start=START,
            window_end=END,
            budgets=Budgets(),
            credentials=KEYED,
            ledger=ledger,
            client_factory=lambda: client,
        )
    assert len(result.posts) == 5
    # The cap is also what we ask for, so the API is not asked for a corpus.
    assert client.twitter.calls[0]["limit"] == 5
    assert result.truncated is True


def test_the_run_budget_still_wins_when_it_is_lower(tmp_path) -> None:
    with Store(tmp_path / "cap2.db") as store:
        ledger = CreditLedger(
            store, CreditPolicy(allow_spend=True, max_results_per_query=50)
        )
        client = _FakeClient(_many_tweets(3))
        xpoz.collect(
            "typhoon Philippines",
            window_start=START,
            window_end=END,
            budgets=Budgets(max_posts_per_run=2),
            credentials=KEYED,
            ledger=ledger,
            client_factory=lambda: client,
        )
    assert client.twitter.calls[0]["limit"] == 2


def test_a_second_call_inside_the_cooldown_is_blocked(ledger) -> None:
    """The gap is read from the ledger, so a restart does not reset it."""
    first = xpoz.collect(
        "typhoon Philippines",
        window_start=START,
        window_end=END,
        budgets=Budgets(),
        credentials=KEYED,
        ledger=ledger,
        client_factory=lambda: _FakeClient(_many_tweets(1)),
    )
    assert first.status == "ok"

    second = xpoz.collect(
        "typhoon Philippines",
        window_start=START,
        window_end=END,
        budgets=Budgets(),
        credentials=KEYED,
        ledger=ledger,
        client_factory=lambda: _FakeClient(_many_tweets(1)),
    )
    assert second.status == "blocked_policy"
    assert any("cooldown" in note for note in second.notes)
    assert second.credits_spent == 0
    # The refused call must not have spent anything.
    assert ledger.spent == 2
