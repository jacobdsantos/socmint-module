"""Tests for the cyber-threat-intelligence layer.

These assert the *guards*, not the happy path. Every case below corresponds to a
way the layer produced a wrong answer during development, so a regression here
would be a real regression in analyst-facing output.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from socmint.analysis import assessment as assess_mod
from socmint.analysis import grading, indicators, threat
from socmint.models import (
    TLP,
    AnalysisBundle,
    CaseTemplate,
    CoordinationGroup,
    Indicator,
    IndicatorKind,
    IndicatorRole,
    NarrativeCandidate,
    Platform,
    Post,
    Precision,
    ThreatFraming,
)
from socmint.presets import PRESETS, resolve

BASE = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def make_post(
    post_id: str,
    text: str,
    *,
    author: str = "acct",
    platform: Platform = Platform.TELEGRAM,
    minutes: int = 0,
) -> Post:
    return Post(
        platform=platform,
        post_id=post_id,
        url=f"https://t.me/{author}/{post_id}",
        author_id=author,
        author_name=author,
        text=text,
        published_at=BASE + timedelta(minutes=minutes),
        precision=Precision.SECOND,
    )


# ---------------------------------------------------------------------------
# Defanging and extraction
# ---------------------------------------------------------------------------


def test_defang_removes_every_live_form() -> None:
    out = indicators.defang("https://bad.example.com/a?b=1")
    assert out == "hxxps://bad[.]example[.]com/a?b=1"
    # No clickable scheme, and every remaining dot is bracketed (i.e. inert).
    assert not out.startswith("http")
    assert out.count("[.]") == out.count(".")
    assert indicators.defang("user@example.com") == "user[@]example[.]com"


def test_extract_from_text_finds_the_core_kinds() -> None:
    text = (
        "download at hxxp://x.test and visit evil-domain.com, hash "
        "9f2c4a7e1b3d5f8092a4c6e8b0d2f4a6c8e0b2d4f6a8c0e2b4d6f8a0c2e4b6d8 "
        "pay bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4 contact @somehandle"
    )
    found = indicators.extract_from_text(text)
    assert "evil-domain.com" in found[IndicatorKind.DOMAIN]
    assert len(found[IndicatorKind.SHA256]) == 1
    assert len(next(iter(found[IndicatorKind.SHA256]))) == 64
    assert "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4" in found[IndicatorKind.WALLET]
    assert "@somehandle" in found[IndicatorKind.TELEGRAM_HANDLE]
    assert indicators.defang("@somehandle") == "[@]somehandle"


def test_extract_ignores_reference_domains_and_private_ips() -> None:
    found = indicators.extract_from_text(
        "see youtube.com and t.me and 192.168.1.1 and 10.0.0.5"
    )
    assert IndicatorKind.DOMAIN not in found
    assert IndicatorKind.IPV4 not in found


def test_extract_aggregates_across_posts_and_never_truncates_hashes() -> None:
    digest = "9f2c4a7e1b3d5f8092a4c6e8b0d2f4a6c8e0b2d4f6a8c0e2b4d6f8a0c2e4b6d8"
    posts = [
        make_post("1", f"sample {digest}"),
        make_post("2", f"same sample {digest}", author="other"),
    ]
    result = indicators.extract(posts)
    sha = next(i for i in result if i.kind is IndicatorKind.SHA256)
    assert sha.value == digest
    assert len(sha.post_ids) == 2
    assert sha.platform_counts == {"telegram": 2}
    assert "not validated" in sha.context


def test_every_extracted_indicator_is_defanged() -> None:
    posts = [make_post("1", "go to evil.com or http://evil.com/x")]
    for indicator in indicators.extract(posts):
        assert "." not in indicator.defanged or "[.]" in indicator.defanged


# ---------------------------------------------------------------------------
# Threat framing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("this is a deepfake of the minister", ThreatFraming.DEEPFAKE),
        ("the leaked database is being sold", ThreatFraming.DATA_LEAK),
        ("killnet claims the ddos attack", ThreatFraming.HACKTIVISM),
        ("a coordinated disinformation campaign", ThreatFraming.DISINFORMATION),
        ("the weather was pleasant today", ThreatFraming.NONE),
    ],
)
def test_framing_matches_expected_pattern(text: str, expected: ThreatFraming) -> None:
    narrative = NarrativeCandidate(narrative_id="n1", label="x", member_ids=["telegram:1"])
    framing, evidence = threat.classify_framing(narrative, [make_post("1", text)])
    assert framing is expected
    if expected is not ThreatFraming.NONE:
        assert evidence, "a matched framing must show the terms that matched"


def test_framing_returns_none_for_empty_text() -> None:
    narrative = NarrativeCandidate(narrative_id="n1", label="x")
    assert threat.classify_framing(narrative, [make_post("1", "   ")]) == (ThreatFraming.NONE, [])


# ---------------------------------------------------------------------------
# Impersonation
# ---------------------------------------------------------------------------


def test_lookalike_domain_requires_most_brand_words() -> None:
    phrases, words = threat.brand_tokens("Acme Bank collapse")
    assert "acmebankcollapse" in phrases
    assert {"acme", "bank"}.issubset(set(words))

    post = make_post("1", "verify at acme-bank-secure.com now")
    domain = Indicator(
        kind=IndicatorKind.DOMAIN, value="acme-bank-secure.com", defanged="acme-bank-secure[.]com"
    )
    findings = threat.detect_impersonation([post], [domain], topic="Acme Bank collapse")
    assert findings
    kinds = {signal.kind for signal in findings[0].signals}
    assert "lookalike_domain" in kinds


def test_single_brand_word_does_not_flag_an_unrelated_domain() -> None:
    """A generic banking domain must not be called a lookalike of a two-word brand."""
    post = make_post("1", "read more at bankofamerica.com")
    domain = Indicator(
        kind=IndicatorKind.DOMAIN, value="bankofamerica.com", defanged="bankofamerica[.]com"
    )
    findings = threat.detect_impersonation([post], [domain], topic="Acme Bank collapse")
    kinds = {signal.kind for signal in findings[0].signals} if findings else set()
    assert "lookalike_domain" not in kinds


def test_severity_counts_distinct_kinds_not_repetitions() -> None:
    """Six posts saying 'urgent' must not outrank one post with three real signals."""
    noisy = [make_post(str(i), "urgent act now") for i in range(6)]
    noisy_findings = threat.detect_impersonation(noisy, [], topic="Acme Bank collapse")
    noisy_severity = noisy_findings[0].severity if noisy_findings else "info"

    targeted = [
        make_post(
            "9",
            "official notice: verify your account and password at acme-bank-secure.com urgently",
        )
    ]
    domain = Indicator(
        kind=IndicatorKind.DOMAIN, value="acme-bank-secure.com", defanged="acme-bank-secure[.]com"
    )
    targeted_findings = threat.detect_impersonation(
        targeted, [domain], topic="Acme Bank collapse"
    )
    assert targeted_findings[0].severity in {"medium", "high"}
    assert targeted_findings[0].severity != noisy_severity or noisy_severity == "low"


def test_no_signals_means_no_finding() -> None:
    posts = [make_post("1", "the weather was pleasant today")]
    assert threat.detect_impersonation(posts, [], topic="Acme Bank collapse") == []


# ---------------------------------------------------------------------------
# Admiralty grading
# ---------------------------------------------------------------------------


def test_unknown_account_defaults_to_f6() -> None:
    posts = [make_post("1", "one post only")]
    grading.grade(posts, [])
    assert posts[0].admiralty is not None
    assert posts[0].admiralty.code == "F6"
    assert posts[0].admiralty.provisional is True
    assert posts[0].admiralty.rationale


def test_newsroom_name_with_volume_grades_c() -> None:
    posts = [make_post(str(i), "reporting", author="manila_bulletin_news") for i in range(3)]
    grading.grade(posts, [])
    assert posts[0].admiralty.source.value == "C"


def test_catch_all_cluster_does_not_upgrade_information_grade() -> None:
    """A cluster spanning most of the corpus must not make every post 'probably true'."""
    posts = [
        make_post(str(i), "the bank collapsed", author=f"acct{i % 4}", platform=Platform.TELEGRAM)
        for i in range(10)
    ]
    for index, post in enumerate(posts):
        post.platform = Platform.YOUTUBE if index % 2 else Platform.TELEGRAM

    catch_all = NarrativeCandidate(
        narrative_id="n1", label="all", member_ids=[p.identity for p in posts]
    )
    grading.grade(posts, [catch_all])
    assert all(p.admiralty.info.value == "6" for p in posts)


def test_independent_cluster_does_upgrade_information_grade() -> None:
    posts = [
        make_post("1", "claim a", author="a", platform=Platform.TELEGRAM),
        make_post("2", "claim a", author="b", platform=Platform.YOUTUBE),
        make_post("3", "claim a", author="c", platform=Platform.TELEGRAM),
        make_post("4", "unrelated filler", author="d"),
        make_post("5", "unrelated filler two", author="e"),
    ]
    cluster = NarrativeCandidate(narrative_id="n1", label="c", member_ids=["telegram:1", "youtube:2", "telegram:3"])
    grading.grade(posts, [cluster])
    assert posts[0].admiralty.info.value == "2"


def test_grade_summary_reports_distribution() -> None:
    posts = [make_post("1", "x")]
    grading.grade(posts, [])
    assert grading.summary(posts) == {"F6": 1}


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


def _empty_bundle() -> AnalysisBundle:
    return AnalysisBundle()


def test_risk_score_is_itemised_and_capped() -> None:
    bundle = _empty_bundle()
    bundle.coordination = [
        CoordinationGroup(group_id=f"g{i}", account_ids=["a", "b"], confidence="strong_candidate")
        for i in range(4)
    ]
    bundle.indicators = [
        Indicator(kind=IndicatorKind.SHA256, value="a" * 64, defanged="a" * 64),
        Indicator(kind=IndicatorKind.WALLET, value="bc1x", defanged="bc1x"),
    ]
    result = assess_mod.synthesise(bundle, [], template=CaseTemplate.HACKTIVIST_LEAK)
    assert 0 <= result.risk_score <= 100
    assert result.risk_factors
    assert all(factor.startswith("+") for factor in result.risk_factors)


def test_custom_template_adds_no_framing_points() -> None:
    bundle = _empty_bundle()
    bundle.narratives = [
        NarrativeCandidate(narrative_id="n1", label="x", framing=ThreatFraming.DATA_LEAK, member_ids=["telegram:1"])
    ]
    custom = assess_mod.synthesise(bundle, [], template=CaseTemplate.CUSTOM)
    leak = assess_mod.synthesise(bundle, [], template=CaseTemplate.HACKTIVIST_LEAK)
    assert leak.risk_score > custom.risk_score


def test_assessment_always_states_that_risk_is_not_a_probability() -> None:
    result = assess_mod.synthesise(_empty_bundle(), [], template=CaseTemplate.CUSTOM)
    assert any("not a probability" in caveat.lower() for caveat in result.caveats)


def test_no_coordination_is_reported_as_a_negative_not_a_failure() -> None:
    result = assess_mod.synthesise(_empty_bundle(), [], template=CaseTemplate.CUSTOM)
    statement = " ".join(j.statement for j in result.key_judgements).lower()
    assert "no account group met the coordination threshold" in statement


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


def test_every_template_resolves_to_a_preset() -> None:
    for template in CaseTemplate:
        preset = resolve(template)
        assert preset.template is template
        assert preset.label

        assert preset.description


def test_unknown_template_falls_back_to_custom() -> None:
    assert resolve("not_a_template").template is CaseTemplate.CUSTOM
    assert resolve(None).template is CaseTemplate.CUSTOM


def test_hacktivist_case_defaults_to_amber_strict() -> None:
    assert PRESETS[CaseTemplate.HACKTIVIST_LEAK].default_tlp is TLP.AMBER_STRICT
    assert PRESETS[CaseTemplate.CUSTOM].default_tlp is TLP.AMBER


def test_templates_do_not_change_the_engine_only_the_lens() -> None:
    """Same evidence, same structural score; only in-focus framing points differ."""
    bundle = _empty_bundle()
    bundle.coordination = [
        CoordinationGroup(group_id="g1", account_ids=["a", "b"], confidence="strong_candidate")
    ]
    scores = {
        template: assess_mod.synthesise(bundle, [], template=template).risk_score
        for template in CaseTemplate
    }
    assert len(set(scores.values())) == 1


# ---------------------------------------------------------------------------
# Indicator role classification
# ---------------------------------------------------------------------------
#
# A topic search returns posts that cite things. Treating every cited domain as
# an indicator put php.net, huntress.com and a researcher's blog on the IOC list
# for a "php2shell" search. These tests pin the separation.


def _roles(texts: list[str]) -> dict[str, IndicatorRole]:
    posts = [make_post(str(i), text) for i, text in enumerate(texts)]
    return {
        item.defanged: item.role
        for item in indicators.extract(posts)
    }


def test_legitimate_documentation_domains_are_references() -> None:
    roles = _roles(["exploit details at https://www.php.net/manual/en/security.php"])
    assert roles["php[.]net"] is IndicatorRole.REFERENCE


def test_vendor_and_research_outlets_are_references() -> None:
    roles = _roles(
        [
            "read the write-up at https://www.huntress.com/blog/php2shell",
            "analysis on https://calif.io/blog/php2shell-analysis",
        ]
    )
    assert roles["huntress[.]com"] is IndicatorRole.REFERENCE
    assert roles["calif[.]io"] is IndicatorRole.REFERENCE


def test_article_path_makes_an_unknown_domain_a_reference() -> None:
    """calif.io is not on any allowlist; the article path is what identifies it."""
    roles = _roles(["see https://someblog.example.net/2026/03/php2shell-writeup"])
    assert roles["example[.]net"] is IndicatorRole.REFERENCE


def test_author_defanged_value_is_an_indicator() -> None:
    roles = _roles(["the kit phones home to evil-c2[.]xyz every 30s"])
    assert roles["evil-c2[.]xyz"] is IndicatorRole.INDICATOR


def test_defanged_value_is_still_extracted() -> None:
    """A plain domain regex cannot see 'evil[.]com'. The normalising pass must."""
    found = indicators.extract_from_text("payload from evil-c2[.]xyz")
    assert "evil-c2.xyz" in found[IndicatorKind.DOMAIN]


def test_url_inherits_the_role_of_its_host() -> None:
    posts = [make_post("1", "login here hxxps://login-verify[.]top/reset")]
    by_value = {i.defanged: i for i in indicators.extract(posts)}
    assert by_value["login-verify[.]top"].role is IndicatorRole.INDICATOR
    assert by_value["hxxps://login-verify[.]top/reset"].role is IndicatorRole.INDICATOR


def test_threat_context_marks_a_plain_domain_as_an_indicator() -> None:
    roles = _roles(["the C2 server badhost.top is still resolving"])
    assert roles["badhost[.]top"] is IndicatorRole.INDICATOR


def test_no_signal_leaves_role_unknown() -> None:
    roles = _roles(["someone mentioned example.org in passing"])
    assert roles["example[.]org"] is IndicatorRole.UNKNOWN


def test_actionable_excludes_references() -> None:
    posts = [
        make_post("1", "details at https://www.php.net/manual"),
        make_post("2", "payload from evil-c2[.]xyz"),
    ]
    result = indicators.extract(posts)
    actionable = indicators.actionable(result)
    assert [i.defanged for i in actionable] == ["evil-c2[.]xyz"]


def test_indicators_sort_ahead_of_references() -> None:
    posts = [
        make_post("1", "docs at https://www.php.net/manual"),
        make_post("2", "payload from evil-c2[.]xyz"),
    ]
    result = indicators.extract(posts)
    assert result[0].role is IndicatorRole.INDICATOR
    assert result[-1].role is IndicatorRole.REFERENCE


def test_risk_score_ignores_reference_domains() -> None:
    """A cited vendor advisory must not raise the case risk score."""
    bundle = _empty_bundle()
    bundle.indicators = [
        Indicator(
            kind=IndicatorKind.DOMAIN,
            value="php.net",
            defanged="php[.]net",
            role=IndicatorRole.REFERENCE,
        )
    ]
    result = assess_mod.synthesise(bundle, [], template=CaseTemplate.CUSTOM)
    assert not any("high-value indicator" in factor for factor in result.risk_factors)


def test_risk_score_counts_real_indicators() -> None:
    bundle = _empty_bundle()
    bundle.indicators = [
        Indicator(
            kind=IndicatorKind.SHA256,
            value="a" * 64,
            defanged="a" * 64,
            role=IndicatorRole.INDICATOR,
        )
    ]
    result = assess_mod.synthesise(bundle, [], template=CaseTemplate.CUSTOM)
    assert any("high-value indicator" in factor for factor in result.risk_factors)


def test_assessment_reports_excluded_references() -> None:
    bundle = _empty_bundle()
    bundle.indicators = [
        Indicator(kind=IndicatorKind.DOMAIN, value="php.net", defanged="php[.]net",
                  role=IndicatorRole.REFERENCE),
        Indicator(kind=IndicatorKind.DOMAIN, value="bad.top", defanged="bad[.]top",
                  role=IndicatorRole.INDICATOR),
    ]
    result = assess_mod.synthesise(bundle, [], template=CaseTemplate.CUSTOM)
    statement = " ".join(j.statement for j in result.key_judgements)
    assert "1 technical indicator(s)" in statement
    assert "1 cited reference(s) were classified as references" in statement


def test_assessment_with_only_references_says_so_plainly() -> None:
    """A CVE discussion yields references and no IOCs; the judgement must read
    as a finding, not as "0 indicator(s) were extracted: none"."""
    bundle = _empty_bundle()
    bundle.indicators = [
        Indicator(kind=IndicatorKind.DOMAIN, value="php.net", defanged="php[.]net",
                  role=IndicatorRole.REFERENCE),
        Indicator(kind=IndicatorKind.URL, value="https://huntress.com/blog/x",
                  defanged="hxxps://huntress[.]com/blog/x", role=IndicatorRole.REFERENCE),
    ]
    result = assess_mod.synthesise(bundle, [], template=CaseTemplate.CUSTOM)
    statement = " ".join(j.statement for j in result.key_judgements)
    assert "No technical indicators were extracted" in statement
    assert "2 cited reference(s)" in statement
    assert "0 technical indicator(s)" not in statement
