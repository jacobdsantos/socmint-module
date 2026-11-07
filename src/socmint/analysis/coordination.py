"""Explainable coordination candidates.

Implements a conservative subset of the behavioural-trace / similarity-network
approach (Pacheco et al., ICWSM 2021): link accounts that repeatedly perform the
same content action close in time, discount features that are simply popular,
and report the individual episodes that justify each group.

Deliberate non-goals:
* No probability score, no "bot" label, no intent or ownership inference.
* Coordination is not disinformation. A hashtag campaign, a newsroom network,
  or a fan community can be genuinely coordinated and entirely benign.
* Every group must be explainable by citing the pairs of posts that matched.
"""

from __future__ import annotations

import hashlib
import itertools
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import numpy as np

from ..models import (
    NEWS_PLATFORMS,
    CoordinationEpisode,
    CoordinationGroup,
    CoordinationSignal,
    Post,
)
from ..prose import agreement, plural
from ..settings import AnalysisSettings
from ..text import content_tokens, jaccard, normalise

logger = logging.getLogger(__name__)

_TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_", "ref", "igshid", "si")


@dataclass
class CoordinationResult:
    groups: list[CoordinationGroup] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    baselines: dict[str, int] = field(default_factory=dict)


def canonical_url(url: str) -> str | None:
    """Normalise a URL for comparison, dropping tracking parameters."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    return urlunparse(("https", host, path, "", urlencode(sorted(query)), ""))


def _account_of(post: Post) -> str:
    return post.author_id or post.author_name or f"unknown:{post.platform}"


def _features(post: Post) -> dict[str, str]:
    """Map feature-key -> human-readable detail for one post."""
    features: dict[str, str] = {}
    for url in post.referenced_urls:
        canon = canonical_url(url)
        if canon:
            features[f"url:{canon}"] = canon
    text = normalise(" ".join(filter(None, [post.title, post.text])))
    toks = content_tokens(text)
    if len(toks) >= 6:
        digest = hashlib.sha256(" ".join(toks).encode("utf-8")).hexdigest()[:16]
        features[f"text:{digest}"] = "identical text"
    return features


def _time_ok(a: Post, b: Post, window_seconds: float) -> bool:
    if not a.published_at or not b.published_at:
        return False
    if a.precision == "unknown" or b.precision == "unknown":
        return False
    return abs((a.published_at - b.published_at).total_seconds()) <= window_seconds


def _seconds_apart(a: Post, b: Post) -> float | None:
    if not a.published_at or not b.published_at:
        return None
    return abs((a.published_at - b.published_at).total_seconds())


def _build_episodes(
    posts: list[Post], settings: AnalysisSettings
) -> tuple[dict[frozenset[str], list[CoordinationEpisode]], dict[str, int], list[str]]:
    """Return pair->episodes, feature prevalence, and notes."""
    notes: list[str] = []
    by_feature: dict[str, list[Post]] = defaultdict(list)
    for post in posts:
        for key in _features(post):
            by_feature[key].append(post)

    account_counts = len({_account_of(p) for p in posts}) or 1
    # A feature used by most accounts is a shared reference, not a signal.
    max_prevalence = max(4, int(account_counts * 0.25))

    prevalence: dict[str, int] = {}
    episodes: dict[frozenset[str], list[CoordinationEpisode]] = defaultdict(list)
    discounted = 0

    for key, group in by_feature.items():
        accounts = {_account_of(p) for p in group}
        prevalence[key] = len(accounts)
        if len(accounts) < 2:
            continue
        if len(accounts) > max_prevalence:
            discounted += 1
            continue

        for a, b in itertools.combinations(group, 2):
            acc_a, acc_b = _account_of(a), _account_of(b)
            if acc_a == acc_b:
                continue
            if not _time_ok(a, b, settings.coordination_time_window_seconds):
                continue
            # Near-duplicate check for text features guards against token collisions.
            if key.startswith("text:") and jaccard(
                frozenset(content_tokens(normalise(a.text + " " + (a.title or "")))),
                frozenset(content_tokens(normalise(b.text + " " + (b.title or "")))),
            ) < 0.7:
                continue
            pair = frozenset({acc_a, acc_b})
            kind = "shared_url" if key.startswith("url:") else "near_duplicate_text"
            detail = (
                f"both posted {key.split(':', 1)[1]}"
                if kind == "shared_url"
                else "near-identical text"
            )
            episodes[pair].append(
                CoordinationEpisode(
                    account_a=acc_a,
                    account_b=acc_b,
                    post_a=a.identity,
                    post_b=b.identity,
                    signal_kinds=[kind, "time_proximity"],
                    seconds_apart=_seconds_apart(a, b),
                    detail=detail,
                )
            )

    if discounted:
        notes.append(
            f"{plural(discounted, 'feature')} used by more than {max_prevalence} accounts "
            f"{agreement(discounted, 'was', 'were')} discounted as generic "
            "(e.g. widely shared links or boilerplate)."
        )
    return episodes, prevalence, notes


def _independent_episodes(episodes: list[CoordinationEpisode]) -> list[CoordinationEpisode]:
    """Keep one episode per distinct pair-of-posts evidence unit."""
    seen: set[tuple[str, str]] = set()
    kept: list[CoordinationEpisode] = []
    for ep in episodes:
        key = tuple(sorted((ep.post_a, ep.post_b)))
        if key in seen:
            continue
        seen.add(key)
        kept.append(ep)
    return kept


def _components(pairs: list[frozenset[str]]) -> list[list[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for pair in pairs:
        items = list(pair)
        for other in items[1:]:
            union(items[0], other)
    groups: dict[str, list[str]] = defaultdict(list)
    for node in list(parent):
        groups[find(node)].append(node)
    return [sorted(members) for members in groups.values() if len(members) > 1]


def _semantic_episodes(
    posts: list[Post],
    settings: AnalysisSettings,
    vectors: np.ndarray,
    vector_ids: list[str],
) -> dict[frozenset[str], list[CoordinationEpisode]]:
    """Pairs whose text means nearly the same thing and which landed close in time.

    This catches paraphrase and template variation that exact-text matching
    misses (e.g. two feeds reformatting the same bulletin). The similarity bar is
    high on purpose: same-topic posts must not become coordination evidence.
    """
    index = {pid: i for i, pid in enumerate(vector_ids)}
    usable = [p for p in posts if p.identity in index and p.has_timestamp]
    usable.sort(key=lambda p: p.published_at)  # type: ignore[arg-type,return-value]
    window = settings.coordination_time_window_seconds
    threshold = settings.coordination_semantic_similarity

    episodes: dict[frozenset[str], list[CoordinationEpisode]] = defaultdict(list)
    for i, first in enumerate(usable):
        vec_a = vectors[index[first.identity]]
        for second in usable[i + 1 :]:
            seconds = (second.published_at - first.published_at).total_seconds()  # type: ignore[operator]
            if seconds > window:
                break
            if _account_of(first) == _account_of(second):
                continue
            similarity = float(vec_a @ vectors[index[second.identity]])
            if similarity < threshold:
                continue
            pair = frozenset({_account_of(first), _account_of(second)})
            episodes[pair].append(
                CoordinationEpisode(
                    account_a=_account_of(first),
                    account_b=_account_of(second),
                    post_a=first.identity,
                    post_b=second.identity,
                    signal_kinds=["semantic_similarity", "time_proximity"],
                    seconds_apart=seconds,
                    detail=f"near-identical meaning (centred cosine {similarity:.2f})",
                )
            )
    return episodes


def _baseline_count(posts: list[Post], settings: AnalysisSettings, seed: int) -> float:
    """Activity-preserving time shuffle: how many pairs still qualify by chance?"""
    rng = np.random.default_rng(seed)
    by_account: dict[str, list[Post]] = defaultdict(list)
    for post in posts:
        by_account[_account_of(post)].append(post)

    shuffled: list[Post] = []
    for _, group in by_account.items():
        times = [p.published_at for p in group]
        if all(t is None for t in times):
            continue
        order = rng.permutation(len(group))
        for post, idx in zip(group, order, strict=False):
            shuffled.append(post.model_copy(update={"published_at": times[int(idx)]}))

    episodes, _, _ = _build_episodes(shuffled, settings)
    return sum(1 for eps in episodes.values() if len(_independent_episodes(eps)) >= 2)


def _baseline_mean(posts: list[Post], settings: AnalysisSettings) -> float:
    """Average qualifying pairs across several independent shuffles.

    One shuffle on a small corpus is noisy; averaging makes the chance level
    stable enough to compare against the observed count.
    """
    trials = max(1, settings.baseline_shuffles)
    values = [_baseline_count(posts, settings, seed=1000 + i) for i in range(trials)]
    return sum(values) / len(values)


def detect(
    posts: list[Post],
    *,
    settings: AnalysisSettings,
    platform: str | None = None,
    vectors: np.ndarray | None = None,
    vector_ids: list[str] | None = None,
) -> CoordinationResult:
    """Find account groups with repeated, time-aligned shared-content evidence."""
    result = CoordinationResult()
    # News records have no posting account and no posting behaviour, so they can
    # never evidence coordination between accounts. Their "author" is a domain,
    # which would otherwise be treated as an account and paired.
    social = [p for p in posts if p.platform not in NEWS_PLATFORMS]
    usable = [p for p in social if p.has_timestamp and _features(p)]
    if len({_account_of(p) for p in usable}) < 2:
        result.notes.append(
            "fewer than two accounts with timestamped, feature-bearing posts; "
            "coordination cannot be assessed"
        )
        return result

    episodes, prevalence, notes = _build_episodes(usable, settings)
    result.notes.extend(notes)

    if vectors is not None and vector_ids:
        semantic = _semantic_episodes(usable, settings, vectors, vector_ids)
        for pair, eps in semantic.items():
            episodes.setdefault(pair, []).extend(eps)
        if semantic:
            result.notes.append(
                f"{plural(len(semantic), 'account pair')} matched on semantic similarity within "
                f"{settings.coordination_time_window_seconds / 60:.0f} minutes"
            )

    qualifying: dict[frozenset[str], list[CoordinationEpisode]] = {}
    for pair, eps in episodes.items():
        distinct = _independent_episodes(eps)
        if len(distinct) >= settings.coordination_min_episodes:
            qualifying[pair] = distinct

    if not qualifying:
        result.notes.append(
            "no account pair met the repeated-evidence threshold "
            f"({settings.coordination_min_episodes} independent episodes)"
        )
        return result

    # Popularity control: drop pairs whose volume is mostly generic.
    accounts_posts = Counter(_account_of(p) for p in usable)

    components = _components(list(qualifying))
    components.sort(key=lambda members: -len(members))

    baseline = _baseline_mean(usable, settings)
    result.baselines["shuffled_pairs"] = round(baseline, 1)

    for index, members in enumerate(components, start=1):
        member_set = set(members)
        group_eps: list[CoordinationEpisode] = []
        for pair, eps in qualifying.items():
            if pair <= member_set:
                group_eps.extend(eps)
        if not group_eps:
            continue

        signals = Counter()
        for ep in group_eps:
            for kind in ep.signal_kinds:
                signals[kind] += 1

        prolific = [acc for acc in members if accounts_posts[acc] >= 50]
        confidence = "candidate"
        limitations: list[str] = []
        if len(members) >= 3 and len(group_eps) >= 4:
            confidence = "strong_candidate"
        if prolific:
            limitations.append(
                "high-volume accounts present "
                f"({', '.join(prolific)}); repeated posting alone can create matches"
            )
        limitations.append(
            "shared content plus timing is consistent with coordination but does not "
            "establish intent, automation, or common ownership"
        )

        result.groups.append(
            CoordinationGroup(
                group_id=f"c{index:03d}",
                account_ids=members,
                platform=platform,
                episodes=group_eps[:40],
                signals=[
                    CoordinationSignal(kind=kind, detail=plural(count, "episode"), weight=count)
                    for kind, count in signals.items()
                ],
                confidence=confidence,
                baseline_episodes=baseline,
                benign_explanations=[
                    "coordinated campaign by genuine supporters or an advocacy group",
                    "newsroom or aggregator network sharing the same source link",
                    "automated scheduled posting by the same publisher",
                    "organic reaction to a breaking-news link",
                ],
                limitations=limitations,
            )
        )

    if baseline:
        verdict = (
            "observed overlap is not clearly above chance"
            if baseline >= len(qualifying) * 0.5
            else "observed overlap exceeds the chance baseline"
        )
        result.notes.append(
            f"time-shuffle baseline averaged {baseline:.1f} qualifying pairs vs "
            f"{plural(len(qualifying), 'observed pair')}: {verdict}."
        )
    return result
