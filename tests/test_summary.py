"""The model summary is the one place a model writes prose.

These tests exist because the failure that matters is not a bad summary — it is
a summary that names something the evidence does not contain. That is a
fabrication in a case file, so the guard is tested harder than the happy path.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from socmint import summary as summary_mod
from socmint.models import (
    AnalysisBundle,
    CoordinationGroup,
    DateRange,
    Indicator,
    IndicatorKind,
    IndicatorRole,
    NarrativeCandidate,
    Platform,
    PlatformReport,
    Run,
    StageStatus,
)
from socmint.summary import SummaryUnavailable, build_summary

BASE = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)


def _run() -> Run:
    return Run(
        run_id="r1",
        topic="binance hack",
        window=DateRange(start=datetime(2026, 10, 20, tzinfo=UTC), end=datetime(2026, 11, 5, tzinfo=UTC)),
        platforms=[PlatformReport(platform=Platform.TELEGRAM, retrieval=StageStatus.OK)],
    )


def _bundle() -> AnalysisBundle:
    bundle = AnalysisBundle()
    bundle.narratives = [
        NarrativeCandidate(
            narrative_id="n001",
            label="exchange / drained / hotwallet",
            member_ids=["telegram:1", "telegram:2"],
            platform_counts={"telegram": 2},
        )
    ]
    bundle.coordination = [
        CoordinationGroup(group_id="c001", account_ids=["a", "b"], confidence="candidate")
    ]
    bundle.indicators = [
        Indicator(
            kind=IndicatorKind.DOMAIN,
            value="newistyl.com",
            defanged="newistyl[.]com",
            role=IndicatorRole.INDICATOR,
        )
    ]
    return bundle


# --- the guard ---------------------------------------------------------------


def test_a_grounded_summary_is_accepted() -> None:
    known = {"n001", "c001", "newistyl[.]com"}
    summary_mod._check_grounded(
        "One narrative (n001) and one account group (c001) were observed.", known
    )


def test_an_invented_domain_is_rejected() -> None:
    """A domain the findings do not contain is a fabrication, not a typo."""
    known = {"n001", "newistyl[.]com"}
    with pytest.raises(SummaryUnavailable) as exc:
        summary_mod._check_grounded("Posting linked to drainer[.]xyz throughout.", known)
    assert "absent from the findings" in str(exc.value)


def test_an_invented_identifier_is_rejected() -> None:
    known = {"n001"}
    with pytest.raises(SummaryUnavailable):
        summary_mod._check_grounded("Narrative n042 carries most of the volume.", known)


def test_an_invented_hash_is_rejected() -> None:
    known = {"n001"}
    with pytest.raises(SummaryUnavailable):
        summary_mod._check_grounded("Sample 9f2c4a7e1b3d5f8092a4c6e8b0d2f4a6", known)


# --- the digest never carries private values ---------------------------------


def test_the_digest_omits_indicator_values_and_handles() -> None:
    """Counts are enough to summarise; the values would leave the building."""
    digest = summary_mod._digest(_run(), _bundle(), 10)
    assert "newistyl" not in digest
    assert "newistyl[.]com" not in digest
    assert "1 domain" in digest


# --- transport ---------------------------------------------------------------


def test_no_key_means_no_call() -> None:
    with pytest.raises(SummaryUnavailable):
        build_summary(_run(), _bundle(), post_count=10, api_key="")


class _Response:
    def __init__(self, status: int, payload: dict) -> None:
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


def _patch(monkeypatch, response) -> None:
    monkeypatch.setattr(summary_mod.httpx, "post", lambda *a, **k: response)


def test_a_clean_response_becomes_a_summary(monkeypatch) -> None:
    payload = {"steps": [{"content": [{"type": "text", "text": "Two posts (n001) only."}]}]}
    _patch(monkeypatch, _Response(200, payload))
    result = build_summary(_run(), _bundle(), post_count=2, api_key="k")
    assert result.text == "Two posts (n001) only."
    assert result.model == summary_mod.DEFAULT_MODEL
    assert result.provenance


def test_an_http_error_degrades_rather_than_raising(monkeypatch) -> None:
    _patch(monkeypatch, _Response(429, {"error": "quota"}))
    with pytest.raises(SummaryUnavailable) as exc:
        build_summary(_run(), _bundle(), post_count=2, api_key="k")
    assert "429" in str(exc.value)


def test_markdown_and_line_breaks_are_stripped(monkeypatch) -> None:
    payload = {
        "steps": [{"content": [{"type": "text", "text": "**Summary:**\n\nOne narrative\n(n001)."}]}]
    }
    _patch(monkeypatch, _Response(200, payload))
    result = build_summary(_run(), _bundle(), post_count=2, api_key="k")
    assert "*" not in result.text
    assert "\n" not in result.text
    assert not result.text.lower().startswith("summary")
