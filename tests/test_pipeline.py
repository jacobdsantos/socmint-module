from __future__ import annotations

from datetime import UTC, datetime

import pytest

from socmint import pipeline
from socmint.models import DateRange, Precision, Run
from socmint.settings import AnalysisSettings, AppConfig
from socmint.store import Store
from tests.conftest import make_post


def test_date_range_is_half_open():
    window = DateRange(
        start=datetime(2026, 10, 20, tzinfo=UTC),
        end=datetime(2026, 11, 3, tzinfo=UTC),
    )
    assert window.contains(datetime(2026, 10, 20, tzinfo=UTC))
    assert window.contains(datetime(2026, 11, 2, 23, 59, tzinfo=UTC))
    assert not window.contains(datetime(2026, 11, 3, tzinfo=UTC))
    assert not window.contains(None)


def test_date_range_rejects_inverted_bounds():
    with pytest.raises(ValueError):
        DateRange(
            start=datetime(2026, 11, 3, tzinfo=UTC),
            end=datetime(2026, 10, 20, tzinfo=UTC),
        )


def test_dedupe_keeps_richest_record():
    poor = make_post(post_id="p1", author_id="a", text="short")
    poor.published_at = None
    poor.precision = Precision.UNKNOWN
    rich = make_post(post_id="p1", author_id="a", text="a much longer body of text here")
    result = pipeline._dedupe([poor, rich])
    assert len(result) == 1
    assert result[0].has_timestamp
    assert "much longer" in result[0].text


def test_coverage_summary_reports_missing_dates():
    run = Run(
        run_id="r1",
        topic="t",
        window=DateRange(
            start=datetime(2026, 10, 20, tzinfo=UTC),
            end=datetime(2026, 11, 3, tzinfo=UTC),
        ),
    )
    dated = make_post(post_id="1", author_id="a", text="x")
    undated = make_post(post_id="2", author_id="a", text="y")
    undated.published_at = None
    coverage = pipeline.coverage_summary(run, [dated, undated])
    entry = coverage["platforms"]["telegram"]
    assert entry["posts"] == 2
    assert entry["dated"] == 1
    assert entry["undated"] == 1


def test_analyse_excludes_off_topic_posts():
    on_topic = make_post(post_id="1", author_id="a", text="earthquake in the Philippines today", topic_match=True)
    off_topic = make_post(post_id="2", author_id="b", text="unrelated cooking recipe", topic_match=False)
    config = AppConfig()
    config = AppConfig(analysis=AnalysisSettings(force_lexical=True))
    bundle = pipeline.analyse(
        [on_topic, off_topic], topic="earthquake Philippines", config=config, include_claims=False
    )
    assert any("did not match the topic terms" in note for note in bundle.method_notes)


def test_store_roundtrip_and_reviews_survive(tmp_path):
    store = Store(tmp_path / "t.db")
    posts = [make_post(post_id="1", author_id="a", text="earthquake Philippines update today")]
    run = Run(
        run_id="r1",
        topic="earthquake Philippines",
        window=DateRange(
            start=datetime(2026, 10, 20, tzinfo=UTC),
            end=datetime(2026, 11, 3, tzinfo=UTC),
        ),
    )
    store.save_run(run)
    store.save_posts("r1", posts)
    config = AppConfig(analysis=AnalysisSettings(force_lexical=True))
    bundle = pipeline.analyse(posts, topic=run.topic, config=config, include_claims=False)
    store.save_analysis("r1", bundle)

    assert store.load_run("r1") is not None
    assert len(store.load_posts("r1")) == 1
    assert store.load_analysis("r1") is not None

    if bundle.narratives:
        nid = bundle.narratives[0].narrative_id
        store.save_review("r1", "narrative", nid, "confirmed", "matches", "jacob")
        reloaded = store.apply_reviews("r1", store.load_analysis("r1"))
        assert reloaded.narratives[0].reviewer_label == "confirmed"
    store.close()


def test_claims_require_figure_and_factual_verb():
    from socmint.analysis import claims

    posts = [
        make_post(post_id="1", author_id="a",
                  text="The bridge collapsed and 12 people were killed in the incident today"),
        make_post(post_id="2", author_id="b",
                  text="Please like and share this video with 1000 friends right now"),
    ]
    result = claims.extract_claims(posts)
    texts = " ".join(c.claim_text for c in result)
    assert "bridge collapsed" in texts
    assert "like and share" not in texts


def test_claim_defaults_to_unreviewed():
    from socmint.analysis import claims

    posts = [make_post(post_id="1", author_id="a",
                       text="Officials confirmed that 5 schools were closed after the quake")]
    result = claims.extract_claims(posts)
    assert result
    assert all(c.verdict == "unreviewed" for c in result)
