"""Observed spread, with an explicit ceiling on what it can claim.

We report the earliest post *in the collected sample*, never an origin. Edges are
split into:

* ``explicit`` -- the platform itself states the relationship (repost/forward,
  reply target, or a shared canonical URL that one post cites).
* ``inferred`` -- same content feature and plausible chronology only. This is an
  association, not evidence of transmission.

Cross-platform identity is never merged. A YouTube channel and a Telegram
channel are different accounts unless a source explicitly links them.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from ..models import (
    NEWS_PLATFORMS,
    EarliestObserved,
    NarrativeCandidate,
    Post,
    PropagationEdge,
)
from ..text import normalise

ORIGIN_CAVEAT = (
    "Earliest observed in the collected sample within the requested window; "
    "not established as the true origin. Posts outside the collected sources, "
    "deleted posts, or undated posts may predate it."
)


def _ordered(posts: list[Post]) -> list[Post]:
    dated = [p for p in posts if p.published_at]
    return sorted(dated, key=lambda p: p.published_at)  # type: ignore[arg-type,return-value]


def earliest_observed(
    narratives: list[NarrativeCandidate], posts_by_id: dict[str, Post]
) -> list[EarliestObserved]:
    results: list[EarliestObserved] = []
    for narrative in narratives:
        members = _ordered(
            [posts_by_id[i] for i in narrative.member_ids if i in posts_by_id]
        )
        if not members:
            continue
        first_ts = members[0].published_at
        firsts = [p for p in members if p.published_at == first_ts]
        results.append(
            EarliestObserved(
                narrative_id=narrative.narrative_id,
                post_ids=[p.identity for p in firsts],
                accounts=sorted({p.author_id or p.author_name or "unknown" for p in firsts}),
                platforms=sorted({str(p.platform) for p in firsts}),
                observed_at=first_ts,
                caveat=ORIGIN_CAVEAT,
            )
        )
    return results


def build_edges(
    narratives: list[NarrativeCandidate],
    posts_by_id: dict[str, Post],
    *,
    max_edges_per_narrative: int = 200,
) -> list[PropagationEdge]:
    edges: list[PropagationEdge] = []
    for narrative in narratives:
        members = _ordered(
            [posts_by_id[i] for i in narrative.member_ids if i in posts_by_id]
        )
        if len(members) < 2:
            continue

        # Index canonical text per post for inferred matching.
        by_text: dict[str, list[Post]] = defaultdict(list)
        for post in members:
            key = normalise(" ".join(filter(None, [post.title, post.text]))).lower()
            if key:
                by_text[key].append(post)

        narrative_edges: list[PropagationEdge] = []
        seen: set[tuple[str, str]] = set()

        for post in members:
            # Explicit: platform-stated reply/forward target inside the sample.
            for target_id in (post.reply_to,):
                if target_id and target_id in posts_by_id:
                    key = (target_id, post.identity)
                    if key not in seen:
                        seen.add(key)
                        narrative_edges.append(
                            PropagationEdge(
                                edge_id=f"e{len(edges) + len(narrative_edges) + 1:05d}",
                                narrative_id=narrative.narrative_id,
                                from_post=target_id,
                                to_post=post.identity,
                                kind="explicit",
                                signal="platform reply reference",
                            )
                        )

        for group in by_text.values():
            if len(group) < 2:
                continue
            group = sorted(group, key=lambda p: p.published_at)  # type: ignore[arg-type,return-value]
            for earlier, later in zip(group, group[1:], strict=False):
                if earlier.identity == later.identity:
                    continue
                edge_key = (earlier.identity, later.identity)
                if edge_key in seen:
                    continue
                seen.add(edge_key)
                seconds = None
                if earlier.published_at and later.published_at:
                    seconds = (later.published_at - earlier.published_at).total_seconds()
                narrative_edges.append(
                    PropagationEdge(
                        edge_id=f"e{len(edges) + len(narrative_edges) + 1:05d}",
                        narrative_id=narrative.narrative_id,
                        from_post=earlier.identity,
                        to_post=later.identity,
                        kind="inferred",
                        signal="identical normalised text; chronology only",
                        seconds_apart=seconds,
                        note=(
                            "Association, not transmission. Both may copy a third source "
                            "that is outside the collected sample."
                        ),
                    )
                )

        narrative_edges.sort(key=lambda e: e.edge_id)
        edges.extend(narrative_edges[:max_edges_per_narrative])
    return edges


def spread_timeline(edges: list[PropagationEdge], posts_by_id: dict[str, Post]) -> list[dict]:
    """Per-narrative first/last observed times and observed platform reach."""
    per_narrative: dict[str, list[Post]] = defaultdict(list)
    for edge in edges:
        for pid in (edge.from_post, edge.to_post):
            post = posts_by_id.get(pid)
            if post and post.published_at:
                per_narrative[edge.narrative_id].append(post)
    rows: list[dict] = []
    for narrative_id, posts in per_narrative.items():
        times = [p.published_at for p in posts if p.published_at]
        if not times:
            continue
        # A news record has a publisher domain, not an account. Counting it as
        # one would report "5 accounts" for a story carried by two people and
        # three outlets.
        social = [p for p in posts if p.platform not in NEWS_PLATFORMS]
        rows.append(
            {
                "narrative_id": narrative_id,
                "first_observed": min(times),
                "last_observed": max(times),
                "platforms": sorted({str(p.platform) for p in posts}),
                "accounts": len({p.author_id or p.author_name for p in social}),
                "news_records": len(posts) - len(social),
                "linked_posts": len({p.identity for p in posts}),
            }
        )
    rows.sort(key=lambda r: r["first_observed"])
    return rows


def _ts(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
