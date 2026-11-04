from __future__ import annotations

from socmint.analysis import narratives, propagation
from socmint.models import NarrativeCandidate
from socmint.settings import AnalysisSettings
from tests.conftest import make_post


def test_narratives_lexical_groups_similar_posts():
    posts = [
        make_post(post_id="1", author_id="a", text="flood control project funds missing in district audit"),
        make_post(post_id="2", author_id="b", text="missing flood control funds found in district audit report"),
        make_post(post_id="3", author_id="c", text="basketball team wins championship game last night"),
    ]
    result = narratives.build_narratives(
        posts, topic="flood control", settings=AnalysisSettings(), force_lexical=True
    )
    assert result.degraded is True
    assert result.method == "tfidf_lexical"
    assert result.narratives
    assert any("embedding model unavailable" in n for n in result.notes)


def test_narratives_skip_short_text():
    posts = [make_post(post_id="1", author_id="a", text="hi")]
    result = narratives.build_narratives(
        posts, topic="x", settings=AnalysisSettings(), force_lexical=True
    )
    assert result.narratives == []
    assert any("minimum" in n for n in result.notes)


def test_narrative_members_are_evidence_linked():
    posts = [
        make_post(post_id=str(i), author_id=f"a{i}", text="same flood control corruption story repeated")
        for i in range(4)
    ]
    result = narratives.build_narratives(
        posts, topic="flood control", settings=AnalysisSettings(), force_lexical=True
    )
    total_members = sum(len(n.member_ids) for n in result.narratives)
    assert total_members == 4
    for narrative in result.narratives:
        assert narrative.representative_ids
        assert set(narrative.representative_ids) <= set(narrative.member_ids)


def test_earliest_observed_is_not_origin():
    first = make_post(post_id="1", author_id="a", text="original claim here", minutes=0)
    second = make_post(post_id="2", author_id="b", text="copy of claim here", minutes=30)
    posts_by_id = {p.identity: p for p in [first, second]}
    narrative = NarrativeCandidate(
        narrative_id="n1", label="x", member_ids=[second.identity, first.identity]
    )
    result = propagation.earliest_observed([narrative], posts_by_id)
    assert result[0].post_ids == [first.identity]
    assert "not established as the true origin" in result[0].caveat


def test_inferred_edge_requires_identical_text():
    a = make_post(post_id="1", author_id="a", text="the same exact sentence appears twice", minutes=0)
    b = make_post(post_id="2", author_id="b", text="the same exact sentence appears twice", minutes=20)
    c = make_post(post_id="3", author_id="c", text="a completely different sentence about weather", minutes=40)
    posts_by_id = {p.identity: p for p in [a, b, c]}
    narrative = NarrativeCandidate(narrative_id="n1", label="x", member_ids=list(posts_by_id))
    edges = propagation.build_edges([narrative], posts_by_id)
    assert len(edges) == 1
    assert edges[0].kind == "inferred"
    assert edges[0].from_post == a.identity
    assert edges[0].to_post == b.identity
    assert "not transmission" in edges[0].note


def test_explicit_reply_edge():
    a = make_post(post_id="1", author_id="a", text="first post", minutes=0)
    b = make_post(post_id="2", author_id="b", text="reply post", minutes=5)
    b.reply_to = a.identity
    posts_by_id = {p.identity: p for p in [a, b]}
    narrative = NarrativeCandidate(narrative_id="n1", label="x", member_ids=list(posts_by_id))
    edges = propagation.build_edges([narrative], posts_by_id)
    assert any(e.kind == "explicit" for e in edges)


def test_spread_timeline_reports_range():
    a = make_post(post_id="1", author_id="a", text="same text here", minutes=0)
    b = make_post(post_id="2", author_id="b", text="same text here", minutes=60)
    posts_by_id = {p.identity: p for p in [a, b]}
    narrative = NarrativeCandidate(narrative_id="n1", label="x", member_ids=list(posts_by_id))
    edges = propagation.build_edges([narrative], posts_by_id)
    rows = propagation.spread_timeline(edges, posts_by_id)
    assert rows and rows[0]["linked_posts"] == 2


def test_volume_series_uses_only_dated_posts():
    dated = make_post(post_id="1", author_id="a", text="x", minutes=0)
    undated = make_post(post_id="2", author_id="a", text="y", minutes=0)
    undated.published_at = None
    rows = narratives.volume_series([dated, undated])
    assert sum(r["count"] for r in rows) == 1
