from __future__ import annotations

from socmint.analysis import coordination
from socmint.settings import AnalysisSettings


def test_exact_repeat_is_detected(coordinated_pair):
    result = coordination.detect(coordinated_pair, settings=AnalysisSettings())
    assert len(result.groups) == 1
    group = result.groups[0]
    assert set(group.account_ids) == {"acct_a", "acct_b"}
    assert len(group.episodes) >= 2
    assert group.confidence in {"candidate", "strong_candidate"}


def test_episodes_are_evidenced(coordinated_pair):
    result = coordination.detect(coordinated_pair, settings=AnalysisSettings())
    group = result.groups[0]
    for episode in group.episodes:
        assert episode.post_a != episode.post_b
        assert episode.seconds_apart is not None
        assert "time_proximity" in episode.signal_kinds


def test_organic_burst_is_not_flagged(organic_burst):
    """Different wording spread over hours must not become a coordination group."""
    result = coordination.detect(organic_burst, settings=AnalysisSettings())
    assert result.groups == []


def test_single_account_cannot_coordinate_with_itself():
    posts = [
        {
            "platform": "telegram",
            "post_id": f"x{i}",
            "url": "https://example.invalid/x",
            "author_id": "solo",
            "author_name": "solo",
            "text": "identical text repeated over and over again here",
            "published_at": None,
        }
        for i in range(3)
    ]
    from socmint.models import Post

    parsed = [Post.model_validate(p) for p in posts]
    result = coordination.detect(parsed, settings=AnalysisSettings())
    assert result.groups == []
    assert any("fewer than two accounts" in note for note in result.notes)


def test_same_account_reposts_do_not_pair():
    from tests.conftest import make_post

    posts = [
        make_post(post_id="s1", author_id="solo", text="same content shared here", minutes=0),
        make_post(post_id="s2", author_id="solo", text="same content shared here", minutes=2),
    ]
    result = coordination.detect(posts, settings=AnalysisSettings())
    assert result.groups == []


def test_undated_posts_are_excluded():
    from socmint.models import Post

    a = Post(platform="telegram", post_id="u1", url="https://e.invalid/1",
             author_id="a", text="identical text for both accounts", published_at=None)
    b = Post(platform="telegram", post_id="u2", url="https://e.invalid/2",
             author_id="b", text="identical text for both accounts", published_at=None)
    result = coordination.detect([a, b], settings=AnalysisSettings())
    assert result.groups == []


def test_generic_url_is_discounted():
    """A link shared by most accounts is a reference, not a coordination signal."""
    from tests.conftest import make_post

    posts = []
    for i in range(10):
        posts.append(
            make_post(
                post_id=f"g{i}",
                author_id=f"acct_{i}",
                text=f"unrelated commentary number {i} about the weather today",
                minutes=i * 30,
                urls=["https://news.example.com/world/earthquake-live"],
            )
        )
    result = coordination.detect(posts, settings=AnalysisSettings())
    assert result.groups == []
    assert any("discounted" in note for note in result.notes)


def test_canonical_url_strips_tracking():
    assert (
        coordination.canonical_url("https://www.Example.com/a/b/?utm_source=x&id=5")
        == "https://example.com/a/b?id=5"
    )
    assert coordination.canonical_url("javascript:alert(1)") is None


def test_benign_explanations_are_attached(coordinated_pair):
    result = coordination.detect(coordinated_pair, settings=AnalysisSettings())
    group = result.groups[0]
    assert group.benign_explanations
    assert any("does not establish intent" in lim for lim in group.limitations)
