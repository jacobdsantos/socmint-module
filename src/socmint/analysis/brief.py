"""The written case brief.

Every finding in this module is derived from the stored bundle; nothing is
generated, summarised by a model, or inferred beyond what the records support.
The brief exists because a list of cards is not an assessment: an analyst reads
prose that connects the findings, then opens the evidence behind a sentence.

The same brief feeds the dashboard overview and the published case file, so the
two cannot drift apart.

Editorial rule: the brief states findings. It does not explain the tool's own
design, its trade-offs, or why a number is provisional. Those belong in
``docs/methodology.md``, which is where a reader who wants them should look.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..models import AnalysisBundle, Post, Run
from ..prose import kind_label, platform_label
from ..prose import listing as _listing
from ..prose import plural as _plural

_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


@dataclass(frozen=True)
class BriefSection:
    heading: str
    paragraphs: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Brief:
    standfirst: str
    sections: list[BriefSection]

    @property
    def is_empty(self) -> bool:
        return not self.sections


def _day(value: datetime) -> str:
    return f"{value.day} {_MONTHS[value.month - 1]}"


def _day_time(value: datetime) -> str:
    return f"{_day(value)} {value:%H:%M} UTC"


def _platform_breakdown(counts: dict[str, int]) -> str:
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])
    return _listing([f"{platform_label(name)} ({count})" for name, count in ordered])


def _window_end(run: Run) -> datetime:
    """The window is half-open in storage; the report speaks in inclusive days."""
    return run.window.end - timedelta(microseconds=1)


def _situation(run: Run, posts: list[Post], in_scope: list[Post]) -> BriefSection:
    dated = [p for p in in_scope if p.published_at]
    per_platform = Counter(str(p.platform) for p in in_scope)

    paragraphs = [
        f"{_plural(len(in_scope), 'post')} about “{run.topic}” were collected between "
        f"{_day(run.window.start)} and {_day(_window_end(run))} UTC from "
        f"{_plural(len(per_platform), 'platform')}: {_platform_breakdown(dict(per_platform))}."
    ]

    if dated:
        by_day = Counter(p.published_at.strftime("%Y-%m-%d") for p in dated)
        peak_day, peak = by_day.most_common(1)[0]
        peak_date = datetime.strptime(peak_day, "%Y-%m-%d")
        if len(dated) == len(in_scope):
            timing = "Every post carried a verifiable publication time."
        else:
            timing = (
                f"{len(dated)} of {_plural(len(in_scope), 'post')} carried a "
                "verifiable publication time."
            )
        paragraphs.append(
            f"Activity ran across {_plural(len(by_day), 'day')}, peaking on "
            f"{_day(peak_date)} with {_plural(peak, 'post')}. {timing}"
        )

    unmatched = len(posts) - len(in_scope)
    if unmatched:
        paragraphs.append(
            f"A further {_plural(unmatched, 'post')} were retrieved and excluded for "
            "not matching the topic."
        )

    return BriefSection("Situation", paragraphs)


def _narratives(bundle: AnalysisBundle, posts_by_id: dict[str, Post]) -> BriefSection:
    if not bundle.narratives:
        return BriefSection(
            "What is being said",
            ["No narrative candidates were produced; the corpus was too small or too "
             "uniform to group."],
        )

    total = len(bundle.narratives)
    paragraphs = [
        f"The corpus divides into {_plural(total, 'narrative candidate')}. Each is a "
        "semantic grouping of posts that share vocabulary, not a confirmed stance."
    ]

    bullets = []
    for narrative in bundle.narratives[:6]:
        members = [posts_by_id[i] for i in narrative.member_ids if i in posts_by_id]
        first = narrative.first_seen
        opened = f", first observed {_day_time(first)}" if first else ""
        if len(narrative.platform_counts) == 1:
            only = next(iter(narrative.platform_counts))
            spread = f" on {platform_label(only)}"
        else:
            spread = f" ({_platform_breakdown(narrative.platform_counts)})"
        bullets.append(
            f"{narrative.narrative_id} — {_plural(len(members), 'post')}{spread}"
            f"{opened}. Vocabulary: {narrative.label}."
        )
    if total > 6:
        bullets.append(f"{total - 6} further candidates are listed under Narratives.")
    return BriefSection("What is being said", paragraphs, bullets)


def _coordination(bundle: AnalysisBundle) -> BriefSection:
    if not bundle.coordination:
        return BriefSection(
            "Account activity",
            ["No group of accounts met the repeated-evidence threshold in this sample."],
        )

    paragraphs = [
        f"{_plural(len(bundle.coordination), 'group')} of accounts showed repeated, "
        "time-aligned posting of related content."
    ]
    bullets = []
    for group in bundle.coordination[:4]:
        signals = _listing([s.kind.replace("_", " ") for s in group.signals[:4]])
        line = (
            f"{group.group_id} — {_listing(group.account_ids)} across "
            f"{_plural(len(group.episodes), 'matching episode')}"
        )
        if signals:
            line += f", on {signals}"
        if group.baseline_episodes is not None:
            line += (
                f". A time-shuffle baseline produced "
                f"{_plural(int(round(group.baseline_episodes)), 'qualifying pair')}"
            )
        bullets.append(line + ".")
    return BriefSection("Account activity", paragraphs, bullets)


def _indicators(bundle: AnalysisBundle) -> BriefSection:
    if not bundle.indicators:
        return BriefSection("Technical indicators", ["No technical values were extracted."])

    actionable = [i for i in bundle.indicators if i.role.value == "indicator"]
    references = [i for i in bundle.indicators if i.role.value == "reference"]
    kinds = Counter(i.kind.value for i in actionable)

    if actionable:
        summary = (
            f"{_plural(len(actionable), 'value')} were presented as artefacts of "
            f"compromise: {_listing([f'{n} {kind_label(k, n)}' for k, n in kinds.most_common()])}."
        )
    else:
        summary = (
            "No value was presented as an artefact of compromise. "
            f"The {_plural(len(bundle.indicators), 'value')} extracted were cited "
            "documentation or gave no signal either way."
        )

    paragraphs = [summary]
    if references:
        paragraphs.append(
            f"{_plural(len(references), 'cited reference')} were classified separately "
            "and are not indicators."
        )

    ranked = sorted(actionable, key=lambda i: -len(i.post_ids))
    bullets = [
        f"{i.defanged} — {kind_label(i.kind.value, 1)}, "
        f"in {_plural(len(i.post_ids), 'post')}"
        for i in ranked[:8]
    ]
    return BriefSection("Technical indicators", paragraphs, bullets)


def _claims(bundle: AnalysisBundle) -> BriefSection:
    if not bundle.claims:
        return BriefSection("Claims", ["No checkable claims were proposed from this sample."])

    pending = sum(1 for c in bundle.claims if c.verdict == "unreviewed")
    if pending == len(bundle.claims):
        text = (
            f"{_plural(len(bundle.claims), 'claim')} were proposed for review; none has "
            "been assessed yet."
        )
    else:
        verdicts = Counter(c.verdict.replace("_", " ") for c in bundle.claims)
        text = (
            f"{_plural(len(bundle.claims), 'claim')} were proposed: "
            f"{_listing([f'{n} {v}' for v, n in verdicts.most_common()])}."
        )
    return BriefSection("Claims", [text])


def _coverage(run: Run) -> BriefSection:
    if not run.platforms:
        return BriefSection("Collection", [])
    partial = [platform_label(str(r.platform)) for r in run.platforms if str(r.retrieval) != "ok"]
    if partial:
        paragraph = (
            f"Retrieval was incomplete on {_listing(partial)}; the remaining platforms "
            "returned results within their limits."
        )
    elif len(run.platforms) == 1:
        paragraph = "The platform returned results within its configured limits."
    else:
        paragraph = (
            f"All {len(run.platforms)} platforms returned results within their "
            "configured limits."
        )
    return BriefSection("Collection", [paragraph])


def build_brief(run: Run, posts: list[Post], bundle: AnalysisBundle) -> Brief:
    """Assemble the written brief for one run."""
    in_scope = [p for p in posts if p.topic_match]
    posts_by_id = {p.identity: p for p in posts}

    sections = [
        _situation(run, posts, in_scope),
        _narratives(bundle, posts_by_id),
        _coordination(bundle),
        _indicators(bundle),
        _claims(bundle),
        _coverage(run),
    ]

    platforms = len({str(p.platform) for p in in_scope})
    standfirst = (
        f"{_plural(len(in_scope), 'post')} from {_plural(platforms, 'platform')}, "
        f"{_plural(len(bundle.narratives), 'narrative')}, "
        f"{_plural(len(bundle.coordination), 'account group')}, "
        f"{_plural(len(bundle.claims), 'claim')} for review."
    )
    return Brief(standfirst=standfirst, sections=[s for s in sections if s.paragraphs or s.bullets])


__all__ = ["Brief", "BriefSection", "build_brief"]
