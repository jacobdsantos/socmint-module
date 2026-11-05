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
    DateRange,
    Indicator,
    IndicatorKind,
    IndicatorRole,
    Platform,
    PlatformReport,
    Post,
    Precision,
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
    assert "Evidence · 0 post(s)" in html
