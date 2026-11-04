"""Provisional Admiralty grading.

The NATO/Admiralty system rates a *source* (A–F) and the *information* (1–6).
Analysts use it to tell a consumer how much weight a single line of reporting
deserves.

**What this module can and cannot do.** True source reliability requires history
we do not have: past accuracy, editorial standards, ownership, correction
behaviour. From public posts we can only observe *proxies* — whether the account
is an established newsroom, how much it posts, whether other accounts
independently carry the same claim. Every grade here is therefore marked
``provisional=True`` and carries the rule that produced it. An analyst must
confirm or downgrade it; the dashboard shows the rationale next to the badge.

This is deliberately conservative. Handing out "A1" to an anonymous channel would
be worse than handing out nothing.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..models import (
    NEWS_PLATFORMS,
    AdmiraltyInfo,
    AdmiraltyRating,
    AdmiraltySource,
    NarrativeCandidate,
    Post,
)

# Name patterns that suggest an established newsroom. This is a heuristic proxy,
# not verification: a channel can call itself "News" and be entirely fabricated.
_NEWSROOM_PATTERN = re.compile(
    r"\b(news|times|post|herald|tribune|bulletin|journal|reuters|associated press|"
    r"bbc|cnn|aljazeera|al jazeera|guardian|agency|wire|press)\b",
    re.IGNORECASE,
)

# Language that signals an unverified claim rather than reporting.
_HEDGE_PATTERN = re.compile(
    r"\b(allegedly|rumou?r|unconfirmed|reportedly|sources say|claim|claims|"
    r"viral|circulating|supposedly)\b",
    re.IGNORECASE,
)


def _source_grade(posts_by_account: dict[str, list[Post]]) -> dict[str, AdmiraltyRating]:
    grades: dict[str, AdmiraltyRating] = {}
    for account, posts in posts_by_account.items():
        # Channel handles are routinely underscore-separated ("manila_bulletin_news").
        # ``\b`` does not break on underscores, so normalise separators to spaces
        # before pattern matching or no such name would ever match.
        name = re.sub(r"[_.\-]+", " ", " ".join(filter(None, [posts[0].author_name, account])))
        count = len(posts)
        hedge_hits = sum(
            1 for p in posts if _HEDGE_PATTERN.search(f"{p.title or ''} {p.text}")
        )

        if _NEWSROOM_PATTERN.search(name) and count >= 3:
            grade, why = AdmiraltySource.C, (
                "name pattern suggests an established newsroom and the account posted "
                f"{count} times in the sample; not independently verified"
            )
        elif _NEWSROOM_PATTERN.search(name):
            grade, why = AdmiraltySource.D, (
                "newsroom-like name but too few posts in the sample to judge consistency"
            )
        elif count >= 5:
            grade, why = AdmiraltySource.D, (
                f"active account ({count} posts) with no verifiable editorial identity"
            )
        else:
            grade, why = AdmiraltySource.F, (
                f"anonymous or low-volume account ({count} post(s)); reliability cannot be judged"
            )

        if hedge_hits and grade in {AdmiraltySource.C, AdmiraltySource.D}:
            why += f"; {hedge_hits} post(s) used hedging/rumour language"

        grades[account] = AdmiraltyRating(
            source=grade, info=AdmiraltyInfo.SIX, provisional=True, rationale=why
        )
    return grades


def _information_grade(
    posts: list[Post],
    narratives: list[NarrativeCandidate],
) -> dict[str, AdmiraltyInfo]:
    """Rate the information from independent-account support for the framing.

    Important distinction: *identical text across accounts* is coordination, not
    corroboration. Independent corroboration means different accounts carrying
    the same underlying claim in their own words, which we approximate with
    narrative membership across distinct accounts.
    """
    posts_by_id = {p.identity: p for p in posts}
    account_of = {p.identity: (p.author_id or p.author_name or "") for p in posts}

    grades: dict[str, AdmiraltyInfo] = {}
    for post in posts:
        grades[post.identity] = AdmiraltyInfo.SIX  # default: cannot be judged

    # A cluster that swallows most of the corpus is not discriminating: every
    # account in the sample is "in" it, so counting its accounts as independent
    # support would upgrade the whole run for free. Skip such catch-all clusters.
    catch_all_limit = max(3, int(len(posts) * 0.6))

    for narrative in narratives:
        members = [posts_by_id[i] for i in narrative.member_ids if i in posts_by_id]
        if not members:
            continue
        if len(members) > catch_all_limit:
            continue
        # Corroboration means a second *account* carrying the claim. A news
        # record has a domain, not an account, and a headline that repeats the
        # topic is not independent support for a social post about it. Counting
        # news as an account would let one press mention upgrade a whole cluster.
        social_members = [m for m in members if m.platform not in NEWS_PLATFORMS]
        accounts = {account_of[m.identity] for m in social_members}
        platforms = {str(m.platform) for m in social_members}

        if not accounts:
            info = AdmiraltyInfo.SIX  # nothing social to judge support from
        elif len(accounts) >= 3 and len(platforms) >= 2:
            info = AdmiraltyInfo.TWO  # probably true: broad independent-ish support
        elif len(accounts) >= 2:
            info = AdmiraltyInfo.THREE  # possibly true
        elif len(social_members) >= 2:
            info = AdmiraltyInfo.FOUR  # doubtful: single account repeating itself
        else:
            info = AdmiraltyInfo.SIX

        for member in members:
            grades[member.identity] = info
    return grades


def grade(posts: list[Post], narratives: list[NarrativeCandidate]) -> None:
    """Assign provisional Admiralty ratings in place."""
    posts_by_account: dict[str, list[Post]] = defaultdict(list)
    for post in posts:
        posts_by_account[post.author_id or post.author_name or "unknown"].append(post)

    source_grades = _source_grade(posts_by_account)
    info_grades = _information_grade(posts, narratives)

    for post in posts:
        account = post.author_id or post.author_name or "unknown"
        base = source_grades.get(account)
        if base is None:
            continue
        post.admiralty = AdmiraltyRating(
            source=base.source,
            info=info_grades.get(post.identity, AdmiraltyInfo.SIX),
            provisional=True,
            rationale=base.rationale,
        )


def summary(posts: list[Post]) -> dict[str, int]:
    """Distribution of grades, for the case header."""
    return dict(Counter(p.admiralty.code for p in posts if p.admiralty))
