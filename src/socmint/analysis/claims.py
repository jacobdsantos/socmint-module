"""Claim triage.

Automation does two things only: propose checkable claims and retrieve candidate
evidence. It never issues a truth verdict. A verdict requires a human to read
the source and record a rationale; until then the claim stays ``unreviewed``.

This is the failure mode that matters most in misinformation work -- an
automated "false" label on a true statement, or the reverse, is worse than no
label at all.
"""

from __future__ import annotations

import logging
import re
from collections import Counter

from ..models import ClaimAssessment, ClaimEvidence, Platform, Post
from ..text import normalise, truncate

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n+")
_FACTUAL_MARKERS = re.compile(
    r"\b(is|are|was|were|has|have|had|will|did|does|killed|died|confirmed|"
    r"reported|announced|banned|closed|arrested|raised|dropped|broke|struck|"
    r"caused|according to|percent|%)\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\b\d[\d,.]*\b")

_MAX_CLAIM_CHARS = 240
_MIN_CLAIM_CHARS = 25

# Promotional / engagement-bait phrasing. Never a checkable factual claim.
_BOILERPLATE = re.compile(
    r"(subscribe|like and share|i-like|i-share|mag-subscribe|follow us|"
    r"click here|download the app|join our channel|donate|"
    r"para sa mga pinakabagong update)",
    re.IGNORECASE,
)


def extract_claims(posts: list[Post], *, max_claims: int = 25) -> list[ClaimAssessment]:
    """Propose checkable claim candidates from post text.

    Heuristics are intentionally crude and transparent: a sentence must contain
    a factual verb and either a number or a proper-noun-ish token. Everything is
    marked ``unreviewed``.
    """
    candidates: list[ClaimAssessment] = []
    seen: set[str] = set()

    # Prefer posts from distinct accounts so one prolific account cannot fill the queue.
    ordered = sorted(posts, key=lambda p: (p.author_id or "", p.published_at or p.collected_at))

    for post in ordered:
        text = normalise(" ".join(filter(None, [post.title, post.text])))
        if not text:
            continue
        for sentence in _SENTENCE_SPLIT.split(text):
            sentence = sentence.strip()
            if not (_MIN_CLAIM_CHARS <= len(sentence) <= _MAX_CLAIM_CHARS):
                continue
            if _BOILERPLATE.search(sentence):
                continue
            # Require *both* a factual verb and a concrete figure. Either alone
            # produces too much noise (promos, opinions, vague statements).
            if not _FACTUAL_MARKERS.search(sentence):
                continue
            if not _NUMBER.search(sentence):
                continue
            key = sentence.lower()[:120]
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                ClaimAssessment(
                    claim_id=f"q{len(candidates) + 1:03d}",
                    post_id=post.identity,
                    platform=Platform(post.platform),
                    claim_text=sentence,
                    language=post.language,
                    verdict="unreviewed",
                    rationale=(
                        "Auto-proposed candidate. Not assessed. A reviewer must check the "
                        "original source before assigning a verdict."
                    ),
                )
            )
            if len(candidates) >= max_claims:
                return candidates
    return candidates


def search_evidence(
    claim: ClaimAssessment,
    *,
    max_results: int = 4,
    backend: str = "auto",
) -> list[ClaimEvidence]:
    """Retrieve candidate evidence pages for a claim. Retrieval is not verification."""
    try:
        from ddgs import DDGS
    except ImportError:  # pragma: no cover
        return []
    query = truncate(claim.claim_text, 200)
    try:
        with DDGS() as client:
            hits = client.text(query, max_results=max_results, backend=backend)
    except Exception as exc:
        if "no results" not in str(exc).lower():
            logger.warning("evidence search failed: %s", exc)
        return []
    evidence: list[ClaimEvidence] = []
    for hit in hits or []:
        url = hit.get("href") or hit.get("url")
        if not url:
            continue
        evidence.append(
            ClaimEvidence(
                url=url,
                title=hit.get("title"),
                excerpt=(hit.get("body") or "")[:400] or None,
                publisher=hit.get("source"),
            )
        )
    return evidence


def attach_factchecks(claims: list[ClaimAssessment], results: list[dict]) -> list[ClaimAssessment]:
    """Attach existing fact-check matches if a caller supplies them.

    ``results`` entries are expected to already be filtered to fact-checking
    publishers. The publisher's wording is preserved; it is never converted into
    our own verdict automatically.
    """
    for claim, hit in zip(claims, results, strict=False):
        claim.existing_factcheck = hit.get("url")
    return claims


def verdict_summary(claims: list[ClaimAssessment]) -> dict[str, int]:
    return dict(Counter(c.verdict for c in claims))
