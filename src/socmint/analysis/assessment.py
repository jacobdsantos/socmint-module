"""Case-level synthesis: key judgements and a transparent risk heuristic.

The risk score is **additive and itemised**. Every point comes with a string
saying where it came from, and the total is capped at 100. There is no learned
model, no hidden weighting, and no probability interpretation — an analyst can
recompute it by hand from the factor list and disagree with any line.

What the score is for: triage. It answers "which of these runs deserves my
attention first", not "how dangerous is this". A score of 70 is not a 70%
chance of anything.
"""

from __future__ import annotations

from ..models import (
    TLP,
    AnalysisBundle,
    Assessment,
    CaseTemplate,
    IndicatorRole,
    KeyJudgement,
    Post,
    ThreatFraming,
)
from ..presets import resolve
from ..prose import agreement, kind_label, listing, plural

# Point values. Deliberately coarse: false precision in a heuristic invites
# over-reading.
_COORD_STRONG = 25
_COORD_CANDIDATE = 10
_FRAMING_IN_FOCUS = 10
_IMPERSONATION = {"high": 25, "medium": 15, "low": 5, "info": 0}
_INDICATOR_KINDS = ("sha256", "md5", "wallet")
_INDICATOR_BONUS = 5


def _risk(
    bundle: AnalysisBundle, focus: tuple[ThreatFraming, ...]
) -> tuple[int, list[str]]:
    score = 0
    factors: list[str] = []

    strong = [g for g in bundle.coordination if g.confidence == "strong_candidate"]
    candidate = [g for g in bundle.coordination if g.confidence == "candidate"]
    if strong:
        points = min(_COORD_STRONG * len(strong), 50)
        score += points
        factors.append(
            f"+{points}: {plural(len(strong), 'coordination group')} met the strong-candidate "
            "threshold with repeated matched episodes"
        )
    if candidate:
        points = min(_COORD_CANDIDATE * len(candidate), 20)
        score += points
        factors.append(
            f"+{points}: {plural(len(candidate), 'coordination group')} met the weaker "
            "candidate threshold only"
        )

    framed = {
        n.framing for n in bundle.narratives if n.framing is not ThreatFraming.NONE
    }
    in_focus = sorted(framed & set(focus), key=lambda f: f.value)
    if in_focus:
        points = min(_FRAMING_IN_FOCUS * len(in_focus), 20)
        score += points
        factors.append(
            f"+{points}: narrative language matched in-focus framings: "
            + ", ".join(f.value for f in in_focus)
        )

    for finding in bundle.impersonation:
        points = _IMPERSONATION.get(finding.severity, 0)
        if points:
            score += points
            factors.append(
                f"+{points}: impersonation finding '{finding.finding_id}' rated "
                f"{finding.severity} severity"
            )

    # Only values classified as indicators count. A cited documentation domain
    # is not evidence of compromise and must not move the score.
    present = {
        i.kind.value for i in bundle.indicators if i.role is IndicatorRole.INDICATOR
    }
    risky = sorted(present & set(_INDICATOR_KINDS))
    if risky:
        points = min(_INDICATOR_BONUS * len(risky), 15)
        score += points
        factors.append(
            f"+{points}: high-value indicator kinds present: "
            + listing([kind_label(kind, 1) for kind in risky])
        )

    if bundle.degraded:
        factors.append(
            "+0: analysis ran in degraded (lexical) mode, so framing and coordination "
            "signals are weaker than the score implies"
        )

    return min(score, 100), factors


def _judgements(
    bundle: AnalysisBundle, posts: list[Post], focus: tuple[ThreatFraming, ...]
) -> list[KeyJudgement]:
    judgements: list[KeyJudgement] = []
    counter = 0

    def add(statement: str, confidence: str, evidence: list[str], caveats: list[str]):
        nonlocal counter
        counter += 1
        judgements.append(
            KeyJudgement(
                judgement_id=f"kj{counter:03d}",
                statement=statement,
                confidence=confidence,  # type: ignore[arg-type]
                evidence_ids=evidence,
                caveats=caveats,
            )
        )

    in_scope = [p for p in posts if p.topic_match]
    if bundle.narratives:
        # One line per framing, not one per narrative: three hacktivist clusters
        # are one observation about the corpus, and repeating the word three
        # times in a row reads like a rendering bug.
        by_framing: dict[str, int] = {}
        for narrative in bundle.narratives:
            if narrative.framing is ThreatFraming.NONE:
                continue
            key = narrative.framing.value
            by_framing[key] = by_framing.get(key, 0) + len(narrative.member_ids)
        detail = listing(
            [f"{name} ({plural(count, 'post')})" for name, count in
             sorted(by_framing.items(), key=lambda kv: -kv[1])]
        )
        add(
            f"{plural(len(bundle.narratives), 'narrative cluster')} "
            f"{agreement(len(bundle.narratives), 'was', 'were')} identified "
            f"across {plural(len(in_scope), 'in-scope post')}"
            + (f"; framings observed: {detail}" if detail else "; no threat framing matched"),
            "moderate" if by_framing else "low",
            [n.narrative_id for n in bundle.narratives[:5]],
            [
                "A cluster is a semantic grouping, not a confirmed narrative or a "
                "common stance; opposing framings can cluster together."
            ],
        )

    if bundle.coordination:
        strongest = max(
            bundle.coordination,
            key=lambda g: (len(g.episodes), len(g.account_ids)),
        )
        baseline = strongest.baseline_episodes
        add(
            f"{plural(len(bundle.coordination), 'account group')} showed repeated "
            f"matched behaviour; the largest has {plural(len(strongest.account_ids), 'account')} "
            f"over {plural(len(strongest.episodes), 'matched episode')}"
            + (
                f", against a time-shuffle baseline of {baseline:.1f}"
                if isinstance(baseline, (int, float))
                else ""
            ),
            "moderate" if strongest.confidence == "strong_candidate" else "low",
            [strongest.group_id],
            [
                "Coordination is not intent, automation, or shared ownership.",
                "Organised but benign campaigns (newsrooms, advocacy) look similar.",
            ],
        )
    else:
        add(
            "No account group met the coordination threshold in the collected sample",
            "moderate",
            [],
            [
                "This is a negative result for this sample only; it does not show that "
                "coordination was absent on the platform."
            ],
        )

    if bundle.indicators:
        actionable = [i for i in bundle.indicators if i.role is IndicatorRole.INDICATOR]
        references = [i for i in bundle.indicators if i.role is IndicatorRole.REFERENCE]
        kinds: dict[str, int] = {}
        for indicator in actionable:
            kinds[indicator.kind.value] = kinds.get(indicator.kind.value, 0) + 1
        if actionable:
            count = len(actionable)
            by_kind = listing(
                [f"{v} {kind_label(k, v)}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])]
            )
            statement = (
                f"{plural(count, 'technical indicator')} "
                f"{agreement(count, 'was', 'were')} extracted: {by_kind}"
            )
        elif references:
            statement = "No technical indicators were extracted"
        else:
            statement = "No value in this sample carried enough context to be judged an indicator"
        if references:
            refs = len(references)
            statement += (
                f"; {plural(refs, 'cited reference')} "
                f"{'was' if refs == 1 else 'were'} classified as references and "
                "excluded from the indicator set"
            )
        add(
            statement,
            "low",
            [i.defanged for i in actionable[:5]],
            [
                "Extraction is not validation. No indicator was checked against "
                "reputation, registration, or hosting data.",
                "All indicators in the report are defanged.",
                "Domains cited as documentation, vendor advisories, or research "
                "outlets are classified as references, not indicators.",
            ],
        )

    if bundle.impersonation:
        finding = bundle.impersonation[0]
        add(
            f"Impersonation signals appeared on {plural(len(finding.post_ids), 'post')} "
            f"({finding.severity} severity, {finding.confidence} confidence)",
            "low",
            finding.post_ids[:5],
            finding.caveats,
        )

    if focus:
        add(
            "In-focus framings for this case template: "
            + ", ".join(f.value for f in focus),
            "high",
            [],
            ["Template choice frames the assessment; it does not change the evidence."],
        )

    return judgements


def synthesise(
    bundle: AnalysisBundle,
    posts: list[Post],
    *,
    template: CaseTemplate = CaseTemplate.CUSTOM,
    tlp: TLP | None = None,
    topic: str = "",
) -> Assessment:
    """Build the case assessment from the analysis bundle."""
    preset = resolve(template)
    score, factors = _risk(bundle, preset.focus_framings)
    judgements = _judgements(bundle, posts, preset.focus_framings)

    if score >= 50 and len(bundle.coordination) and bundle.narratives:
        confidence = "moderate"
    elif bundle.narratives:
        confidence = "low"
    else:
        confidence = "low"

    headline = topic.strip() or "untitled case"
    if bundle.narratives:
        headline = f"{headline} — {plural(len(bundle.narratives), 'narrative cluster')}"
    if bundle.coordination:
        headline += f", {plural(len(bundle.coordination), 'coordination candidate')}"

    return Assessment(
        case_template=preset.template,
        tlp=tlp or preset.default_tlp,
        headline=headline,
        key_judgements=judgements,
        risk_score=score,
        risk_factors=factors,
        confidence=confidence,
        caveats=[
            "Risk score is an additive triage heuristic, not a probability.",
            "All Admiralty grades are provisional and require analyst confirmation.",
            "Coverage is sample-based; absence of a signal is not absence of activity.",
            *preset.analyst_prompts,
        ],
    )
