"""Static case-file export tests.

The output is a file someone else will open, so the two things that matter are
that it renders the case without a server, and that untrusted post text cannot
become markup.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from socmint.analysis import assessment as assess_mod
from socmint.models import (
    AnalysisBundle,
    CaseTemplate,
    ClaimAssessment,
    DateRange,
    EarliestObserved,
    ImpersonationFinding,
    ImpersonationSignal,
    Indicator,
    IndicatorKind,
    IndicatorRole,
    Platform,
    PlatformReport,
    Post,
    Precision,
    PropagationEdge,
    Run,
    StageStatus,
)
from socmint.site import export_site, render

BASE = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)


def _run() -> Run:
    return Run(
        run_id="r1",
        topic="typhoon Philippines",
        window=DateRange(start=datetime(2026, 10, 20, tzinfo=UTC), end=datetime(2026, 11, 5, tzinfo=UTC)),
        platforms=[PlatformReport(platform=Platform.YOUTUBE, retrieval=StageStatus.OK)],
    )


def _post(text: str = "typhoon update", author: str = "acct") -> Post:
    return Post(
        platform=Platform.TELEGRAM,
        post_id="1",
        url="https://t.me/x/1",
        author_id=author,
        author_name=author,
        text=text,
        published_at=BASE,
        precision=Precision.SECOND,
    )


def _bundle() -> AnalysisBundle:
    bundle = AnalysisBundle()
    bundle.assessment = assess_mod.synthesise(bundle, [], template=CaseTemplate.CUSTOM)
    bundle.indicators = [
        Indicator(
            kind=IndicatorKind.DOMAIN,
            value="evil.example",
            defanged="evil[.]example",
            role=IndicatorRole.INDICATOR,
            role_reason="appears in a credential-lure sentence",
        ),
        Indicator(
            kind=IndicatorKind.DOMAIN,
            value="php.net",
            defanged="php[.]net",
            role=IndicatorRole.REFERENCE,
            role_reason="known documentation domain",
        ),
    ]
    return bundle


def test_render_is_a_standalone_document() -> None:
    html = render(_run(), [_post()], _bundle())
    assert html.startswith("<!doctype html>")
    # No external references: the file must work with no network at view time.
    assert "http://" not in html.replace("https://t.me/x/1", "")
    assert "<link" not in html
    assert "<script src" not in html


def test_render_includes_the_case_sections() -> None:
    html = render(_run(), [_post()], _bundle())
    for heading in ("Assessment", "Indicators", "Coverage", "Evidence"):
        assert heading in html


def test_indicator_roles_are_labelled() -> None:
    html = render(_run(), [_post()], _bundle())
    assert "evil[.]example" in html
    assert "php[.]net" in html
    assert "indicator" in html and "reference" in html


def test_post_text_is_escaped() -> None:
    """Post text is untrusted data and must never become markup."""
    hostile = '<script>alert("x")</script>'
    html = render(_run(), [_post(text=hostile)], _bundle())
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_export_writes_an_index_and_refuses_to_overwrite(tmp_path) -> None:
    out = tmp_path / "case"
    index = export_site(_run(), [_post()], _bundle(), out)
    assert index.exists()
    assert index.name == "index.html"
    with pytest.raises(FileExistsError):
        export_site(_run(), [_post()], _bundle(), out)


def test_an_empty_case_still_renders(tmp_path) -> None:
    index = export_site(_run(), [], AnalysisBundle(), tmp_path / "empty")
    html = index.read_text(encoding="utf-8")
    assert "Evidence · 0 posts" in html


# --- sections that only appear when the analysis produced something ----------


def test_claims_render_and_are_searchable() -> None:
    bundle = _bundle()
    bundle.claims = [
        ClaimAssessment(
            claim_id="q001",
            post_id="1",
            platform=Platform.TELEGRAM,
            claim_text="the bank collapsed overnight",
            verdict="unreviewed",
            rationale="Auto-proposed candidate. Not assessed.",
        )
    ]
    html = render(_run(), [_post()], bundle)
    assert "Claim review" in html
    assert "the bank collapsed overnight" in html
    assert 'data-search="the bank collapsed overnight unreviewed"' in html


def test_an_unreviewed_claim_does_not_repeat_the_auto_rationale() -> None:
    """The boilerplate is identical on every card; the section note covers it."""
    bundle = _bundle()
    bundle.claims = [
        ClaimAssessment(
            claim_id="q001",
            post_id="1",
            platform=Platform.TELEGRAM,
            claim_text="claim",
            verdict="unreviewed",
            rationale="Auto-proposed candidate. Not assessed.",
        )
    ]
    html = render(_run(), [_post()], bundle)
    assert "Auto-proposed candidate" not in html


def test_a_reviewed_claim_shows_its_rationale() -> None:
    bundle = _bundle()
    bundle.claims = [
        ClaimAssessment(
            claim_id="q001",
            post_id="1",
            platform=Platform.TELEGRAM,
            claim_text="claim",
            verdict="contradicted",
            rationale="PAGASA's 06:00 bulletin reports no such system.",
        )
    ]
    html = render(_run(), [_post()], bundle)
    assert "PAGASA&#x27;s 06:00 bulletin" in html or "PAGASA's 06:00 bulletin" in html
    assert "contradicted" in html


def test_spread_renders_earliest_and_propagation() -> None:
    bundle = _bundle()
    bundle.earliest = [
        EarliestObserved(
            narrative_id="n001",
            post_ids=["1"],
            accounts=["acct"],
            platforms=["telegram"],
            observed_at=BASE,
        )
    ]
    bundle.propagation = [
        PropagationEdge(
            edge_id="e001",
            narrative_id="n001",
            from_post="1",
            to_post="2",
            kind="inferred",
            signal="shared_url",
            seconds_apart=12.0,
        )
    ]
    html = render(_run(), [_post()], bundle)
    assert "Spread" in html
    assert "n001" in html and "e001" in html
    # An inferred edge must carry its caveat, not read as transmission.
    assert "not transmission" in html


def test_impersonation_renders_with_severity_and_caveats() -> None:
    bundle = _bundle()
    bundle.impersonation = [
        ImpersonationFinding(
            finding_id="imp001",
            subject="Acme Bank",
            post_ids=["1"],
            severity="medium",
            confidence="low",
            signals=[
                ImpersonationSignal(
                    kind="lookalike_domain",
                    detail="one edit from the real domain",
                    weight=2.0,
                    evidence=["acme-bank[.]example"],
                )
            ],
            caveats=["string distance only; no registration check"],
        )
    ]
    html = render(_run(), [_post()], bundle)
    assert "imp001" in html and "Acme Bank" in html
    # The reader sees the label, not the storage key.
    assert "lookalike domain" in html
    assert "lookalike_domain" not in html
    assert "medium severity" in html
    assert "no registration check" in html


def test_a_boilerplate_unknown_reason_is_not_repeated_on_every_row() -> None:
    bundle = _bundle()
    bundle.indicators = [
        Indicator(
            kind=IndicatorKind.DOMAIN,
            value="x.example",
            defanged="x[.]example",
            role=IndicatorRole.UNKNOWN,
            role_reason="no signal either way in the post text",
        )
    ]
    html = render(_run(), [_post()], bundle)
    assert "x[.]example" in html
    # The brief may state it once in prose; what must not happen is the same
    # sentence repeated under every indicator row.
    assert html.count("no signal either way") <= 1


def test_method_notes_and_degraded_state_are_surfaced() -> None:
    bundle = _bundle()
    bundle.degraded = True
    bundle.method_notes = ["embedding model unavailable; lexical grouping only"]
    html = render(_run(), [_post()], bundle)
    assert "Analysis degraded" in html
    assert "lexical grouping only" in html
