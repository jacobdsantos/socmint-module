"""Export a run to JSON (authoritative) plus flat CSV tables for spreadsheets.

Never overwrites an existing export directory: a re-run gets a new timestamped
directory, so earlier evidence is preserved.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .models import AnalysisBundle, Post, Run
from .security import write_csv

SCHEMA_VERSION = "2.0"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


POST_COLUMNS = [
    "platform", "post_id", "url", "author_id", "author_name", "title", "text",
    "language", "published_at", "precision", "collected_at", "views", "likes",
    "comments", "engagement_missing", "media_count", "media_urls", "is_repost",
    "evidence_quality", "provenance", "topic_match", "admiralty", "admiralty_source",
    "admiralty_info", "admiralty_provisional", "admiralty_rationale",
]


def _post_row(post: Post) -> dict:
    engagement = post.engagement
    rating = post.admiralty
    return {
        "platform": str(post.platform),
        "post_id": post.post_id,
        "url": post.url,
        "author_id": post.author_id,
        "author_name": post.author_name,
        "title": post.title,
        "text": post.text,
        "language": post.language,
        "published_at": _iso(post.published_at),
        "precision": str(post.precision),
        "collected_at": _iso(post.collected_at),
        "views": engagement.views if engagement else None,
        "likes": engagement.likes if engagement else None,
        "comments": engagement.comments if engagement else None,
        "engagement_missing": engagement.missing_reason if engagement else "no engagement block",
        "media_count": len(post.media),
        "media_urls": " | ".join(m.url for m in post.media),
        "is_repost": post.is_repost,
        "evidence_quality": str(post.evidence_quality),
        "provenance": post.provenance,
        "topic_match": post.topic_match,
        "admiralty": rating.code if rating else None,
        "admiralty_source": rating.source.value if rating else None,
        "admiralty_info": rating.info.value if rating else None,
        "admiralty_provisional": rating.provisional if rating else None,
        "admiralty_rationale": rating.rationale if rating else None,
    }


def _narrative_rows(bundle: AnalysisBundle) -> tuple[list[dict], list[dict]]:
    narratives = []
    members = []
    for narrative in bundle.narratives:
        narratives.append(
            {
                "narrative_id": narrative.narrative_id,
                "label": narrative.label,
                "keywords": ", ".join(narrative.keywords),
                "member_count": len(narrative.member_ids),
                "platforms": json.dumps(narrative.platform_counts),
                "first_seen": _iso(narrative.first_seen),
                "method": narrative.method,
                "framing": narrative.framing.value,
                "framing_evidence": ", ".join(narrative.framing_evidence),
                "representative_ids": " | ".join(narrative.representative_ids),
                "reviewer_label": narrative.reviewer_label,
            }
        )
        for member_id in narrative.member_ids:
            members.append({"narrative_id": narrative.narrative_id, "post_id": member_id})
    return narratives, members


def _coordination_rows(bundle: AnalysisBundle) -> tuple[list[dict], list[dict]]:
    groups = []
    episodes = []
    for group in bundle.coordination:
        groups.append(
            {
                "group_id": group.group_id,
                "accounts": " | ".join(group.account_ids),
                "account_count": len(group.account_ids),
                "episode_count": len(group.episodes),
                "confidence": group.confidence,
                "baseline_pairs": group.baseline_episodes,
                "signals": "; ".join(f"{s.kind}:{s.detail}" for s in group.signals),
                "limitations": " | ".join(group.limitations),
                "reviewer_decision": group.reviewer_decision,
            }
        )
        for episode in group.episodes:
            episodes.append(
                {
                    "group_id": group.group_id,
                    "account_a": episode.account_a,
                    "account_b": episode.account_b,
                    "post_a": episode.post_a,
                    "post_b": episode.post_b,
                    "signals": ", ".join(episode.signal_kinds),
                    "seconds_apart": episode.seconds_apart,
                    "detail": episode.detail,
                }
            )
    return groups, episodes


def export(
    run: Run,
    posts: list[Post],
    bundle: AnalysisBundle,
    *,
    output_root: Path,
    coverage: dict | None = None,
) -> Path:
    directory = output_root / run.run_id
    directory.mkdir(parents=True, exist_ok=False)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run.run_id,
        "topic": run.topic,
        "aliases": run.aliases,
        "languages": run.languages,
        "case_template": run.case_template.value,
        "tlp": run.tlp.value,
        "window": {"start": run.window.start.isoformat(), "end": run.window.end.isoformat()},
        "created_at": _iso(run.created_at),
        "finished_at": _iso(run.finished_at),
        "status": str(run.status),
        "platforms": [p.model_dump(mode="json") for p in run.platforms],
        "coverage": coverage,
        "analysis_degraded": bundle.degraded,
        "method_notes": bundle.method_notes,
        "counts": {
            "posts": len(posts),
            "dated_posts": sum(1 for p in posts if p.published_at),
            "narratives": len(bundle.narratives),
            "coordination_groups": len(bundle.coordination),
            "propagation_edges": len(bundle.propagation),
            "claims": len(bundle.claims),
            "indicators": len(bundle.indicators),
            "indicators_actionable": sum(
                1 for i in bundle.indicators if i.role.value == "indicator"
            ),
            "indicator_references": sum(
                1 for i in bundle.indicators if i.role.value == "reference"
            ),
            "impersonation_findings": len(bundle.impersonation),
        },
        "risk_score": bundle.assessment.risk_score if bundle.assessment else None,
        "generated_at": datetime.now(UTC).isoformat(),
        "caveats": [
            "Coordination candidates do not establish intent, automation, or ownership.",
            "Earliest observed is not the true origin.",
            "Coverage is sample-based and not an estimate of platform-wide activity.",
            "Admiralty grades are provisional and require analyst confirmation.",
            "Indicators are extracted, not validated; all are defanged.",
            "Values classified as 'reference' are cited documentation, vendor "
            "advisories, or research outlets and are not indicators.",
            "Risk score is an additive triage heuristic, not a probability.",
        ],
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    results = {
        "run": json.loads(run.model_dump_json()),
        "coverage": coverage,
        "narratives": [n.model_dump(mode="json") for n in bundle.narratives],
        "coordination": [g.model_dump(mode="json") for g in bundle.coordination],
        "propagation": [e.model_dump(mode="json") for e in bundle.propagation],
        "earliest": [e.model_dump(mode="json") for e in bundle.earliest],
        "claims": [c.model_dump(mode="json") for c in bundle.claims],
        "indicators": [i.model_dump(mode="json") for i in bundle.indicators],
        "impersonation": [f.model_dump(mode="json") for f in bundle.impersonation],
        "assessment": bundle.assessment.model_dump(mode="json") if bundle.assessment else None,
    }
    (directory / "results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    write_csv(directory / "posts.csv", POST_COLUMNS, [_post_row(p) for p in posts])

    narratives, members = _narrative_rows(bundle)
    write_csv(
        directory / "narratives.csv",
        ["narrative_id", "label", "keywords", "member_count", "platforms", "first_seen",
         "method", "framing", "framing_evidence", "representative_ids", "reviewer_label"],
        narratives,
    )
    write_csv(directory / "narrative_members.csv", ["narrative_id", "post_id"], members)

    groups, episodes = _coordination_rows(bundle)
    write_csv(
        directory / "coordination_groups.csv",
        ["group_id", "accounts", "account_count", "episode_count", "confidence",
         "baseline_pairs", "signals", "limitations", "reviewer_decision"],
        groups,
    )
    write_csv(
        directory / "coordination_episodes.csv",
        ["group_id", "account_a", "account_b", "post_a", "post_b", "signals",
         "seconds_apart", "detail"],
        episodes,
    )

    write_csv(
        directory / "propagation_edges.csv",
        ["edge_id", "narrative_id", "from_post", "to_post", "kind", "signal",
         "seconds_apart", "note"],
        [e.model_dump() for e in bundle.propagation],
    )
    write_csv(
        directory / "earliest_observed.csv",
        ["narrative_id", "post_ids", "accounts", "platforms", "observed_at", "caveat"],
        [
            {
                "narrative_id": e.narrative_id,
                "post_ids": " | ".join(e.post_ids),
                "accounts": " | ".join(e.accounts),
                "platforms": " | ".join(e.platforms),
                "observed_at": _iso(e.observed_at),
                "caveat": e.caveat,
            }
            for e in bundle.earliest
        ],
    )
    write_csv(
        directory / "claims.csv",
        ["claim_id", "post_id", "platform", "claim_text", "language", "verdict",
         "rationale", "evidence_urls", "reviewer", "reviewed_at"],
        [
            {
                "claim_id": c.claim_id,
                "post_id": c.post_id,
                "platform": str(c.platform),
                "claim_text": c.claim_text,
                "language": c.language,
                "verdict": c.verdict,
                "rationale": c.rationale,
                "evidence_urls": " | ".join(e.url for e in c.evidence),
                "reviewer": c.reviewer,
                "reviewed_at": _iso(c.reviewed_at),
            }
            for c in bundle.claims
        ],
    )

    # --- Cyber-threat-intelligence artefacts ---------------------------------
    write_csv(
        directory / "indicators.csv",
        ["kind", "role", "value", "defanged", "role_reason", "post_count", "platforms",
         "first_seen", "context"],
        [
            {
                "kind": i.kind.value,
                "role": i.role.value,
                "value": i.value,
                "defanged": i.defanged,
                "role_reason": i.role_reason,
                "post_count": len(i.post_ids),
                "platforms": json.dumps(i.platform_counts),
                "first_seen": _iso(i.first_seen),
                "context": i.context,
            }
            for i in bundle.indicators
        ],
    )
    write_csv(
        directory / "impersonation.csv",
        ["finding_id", "subject", "signal_kind", "detail", "weight", "evidence",
         "severity", "confidence", "post_ids"],
        [
            {
                "finding_id": finding.finding_id,
                "subject": finding.subject,
                "signal_kind": signal.kind,
                "detail": signal.detail,
                "weight": signal.weight,
                "evidence": " | ".join(signal.evidence),
                "severity": finding.severity,
                "confidence": finding.confidence,
                "post_ids": " | ".join(finding.post_ids),
            }
            for finding in bundle.impersonation
            for signal in finding.signals
        ],
    )
    if bundle.assessment is not None:
        (directory / "assessment.json").write_text(
            json.dumps(bundle.assessment.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return directory
