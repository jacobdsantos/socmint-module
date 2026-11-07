"""Threat framing and impersonation / brand-abuse detection.

Two jobs:

* **Framing** — label a narrative cluster with the threat pattern it resembles
  (disinformation, hacktivism, impersonation, data leak, fraud, deepfake).
  Keyword-driven and explainable; a framing is a *lens for the analyst*, never a
  conclusion about intent.
* **Impersonation** — surface posts that show brand-abuse or executive-
  impersonation signals: lookalike domains, authority claims, urgency, credential
  requests, and deepfake claims.

What this does **not** do: it does not determine that a video is synthetic. Media
forensics needs the file and specialised tooling. The module tracks *claims about*
synthetic media and hands the analyst a verification checklist instead of a
fabricated authenticity score.
"""

from __future__ import annotations

import re
from collections import defaultdict

from ..models import (
    ImpersonationFinding,
    ImpersonationSignal,
    Indicator,
    IndicatorKind,
    NarrativeCandidate,
    Post,
    ThreatFraming,
)
from ..prose import plural
from ..text import normalise

_FRAMING_TERMS: dict[ThreatFraming, set[str]] = {
    ThreatFraming.DISINFORMATION: {
        "disinformation", "misinformation", "propaganda", "hoax", "fake news",
        "false claim", "fabricated", "manipulated", "psyop", "narrative",
    },
    ThreatFraming.HACKTIVISM: {
        "ddos", "deface", "defacement", "hacktivist", "killnet", "anonymous",
        "breach", "ransomware", "exploit", "attacked", "takedown", "leak site",
    },
    ThreatFraming.IMPERSONATION: {
        "impersonat", "fake account", "fake profile", "pretending to be",
        "clone account", "spoofed", "lookalike", "typosquat",
    },
    ThreatFraming.DATA_LEAK: {
        "leak", "leaked", "dumped", "database", "credentials", "combolist",
        "sold data", "data breach", "exfiltrat",
    },
    ThreatFraming.FRAUD: {
        "scam", "fraud", "investment scheme", "giveaway", "prize", "wire transfer",
        "crypto doubling", "phishing", "stolen funds",
    },
    ThreatFraming.DEEPFAKE: {
        "deepfake", "deep fake", "ai-generated", "ai generated", "synthetic media",
        "voice clone", "face swap", "face-swap", "manipulated video", "cheapfake",
    },
}

_URGENCY = re.compile(
    r"\b(urgent|immediately|act now|before it'?s too late|final warning|"
    r"last chance|within \d+ hours|expires?)\b",
    re.IGNORECASE,
)
_CREDENTIAL_REQUEST = re.compile(
    r"\b(password|passcode|otp|one[- ]time code|login|sign in|verify your account|"
    r"confirm your (identity|details)|click (here|this link)|enter your)\b",
    re.IGNORECASE,
)
_AUTHORITY = re.compile(
    r"\b(official|verified|authorized|authorised|headquarters|management|"
    r"the ceo|the cfo|the chairman|the bank|the ministry|the government)\b",
    re.IGNORECASE,
)
_DEEPFAKE_CLAIM = re.compile(
    r"\b(deepfake|deep fake|ai[- ]generated|synthetic media|voice clone|"
    r"face[- ]swap|manipulated video|cheapfake|not really (him|her|them))\b",
    re.IGNORECASE,
)


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            insert = current[j - 1] + 1
            delete = previous[j] + 1
            substitute = previous[j - 1] + (ca != cb)
            current.append(min(insert, delete, substitute))
        previous = current
    return previous[-1]


def brand_tokens(topic: str, aliases: list[str] | None = None) -> tuple[list[str], list[str]]:
    """Compact forms of the protected name, for lookalike comparison.

    Returns ``(phrases, words)``:

    * ``phrases`` — the whole topic and each alias compacted, e.g.
      ``"Acme Bank" -> "acmebank"``. Used for edit-distance typosquat checks.
    * ``words`` — the individual words of four or more characters, e.g.
      ``["acme", "bank"]``. Used for containment checks.

    Two separate forms because the comparison rules differ: a typo is close to
    the *phrase*, whereas a lookalike domain usually *embeds* the brand words.
    """
    phrases: list[str] = []
    words: list[str] = []
    seen: set[str] = set()

    for source in [topic, *(aliases or [])]:
        if not source or not source.strip():
            continue
        phrase = re.sub(r"[^a-z0-9]", "", normalise(source).lower())
        if len(phrase) >= 4 and phrase not in seen:
            seen.add(phrase)
            phrases.append(phrase)
        for word in re.split(r"[^\w]+", normalise(source)):
            compact = re.sub(r"[^a-z0-9]", "", word.lower())
            if len(compact) >= 4 and compact not in seen:
                seen.add(compact)
                words.append(compact)
    return phrases, words


def classify_framing(
    narrative: NarrativeCandidate, member_posts: list[Post]
) -> tuple[ThreatFraming, list[str]]:
    """Label a narrative cluster by the threat pattern its language resembles."""
    haystack = " ".join(
        f"{p.title or ''} {p.text}" for p in member_posts
    ).lower()
    if not haystack.strip():
        return ThreatFraming.NONE, []

    scores: dict[ThreatFraming, list[str]] = defaultdict(list)
    for framing, terms in _FRAMING_TERMS.items():
        for term in terms:
            if term in haystack:
                scores[framing].append(term)

    if not scores:
        return ThreatFraming.NONE, []
    framing, hits = max(scores.items(), key=lambda kv: (len(kv[1]), kv[0].value))
    return framing, sorted(set(hits))[:6]


def detect_impersonation(
    posts: list[Post],
    indicators: list[Indicator],
    *,
    topic: str,
    aliases: list[str] | None = None,
) -> list[ImpersonationFinding]:
    """Surface brand-abuse and executive-impersonation signals."""
    phrases, words = brand_tokens(topic, aliases)
    domains = [i for i in indicators if i.kind is IndicatorKind.DOMAIN]
    lookalikes: dict[str, list[str]] = defaultdict(list)
    # Require most of the brand's words, not just one: otherwise "bank" alone
    # would flag every unrelated banking domain in the corpus.
    words_needed = min(2, len(words))

    for indicator in domains:
        label = re.sub(r"[^a-z0-9]", "", indicator.value.split(".")[0].lower())
        for phrase in phrases:
            distance = _levenshtein(label, phrase)
            if 0 < distance <= 2 and abs(len(label) - len(phrase)) <= 2:
                lookalikes[indicator.value].append(
                    f"'{indicator.value}' is {plural(distance, 'edit')} from '{phrase}'"
                )
        embedded = [w for w in words if w in label and label != w]
        if words_needed and len(embedded) >= words_needed:
            lookalikes[indicator.value].append(
                f"'{indicator.value}' embeds brand words {embedded} with added text"
            )

    findings: list[ImpersonationFinding] = []
    by_post: dict[str, list[ImpersonationSignal]] = defaultdict(list)

    for post in posts:
        text = " ".join(filter(None, [post.title, post.text]))
        if not text.strip():
            continue
        signals: list[ImpersonationSignal] = []

        if _AUTHORITY.search(text):
            signals.append(
                ImpersonationSignal(
                    kind="authority_claim",
                    detail="post asserts official/authorised status",
                    weight=1.0,
                    evidence=[_snippet(text, _AUTHORITY)],
                )
            )
        if _URGENCY.search(text):
            signals.append(
                ImpersonationSignal(
                    kind="urgency",
                    detail="post uses time pressure",
                    weight=1.0,
                    evidence=[_snippet(text, _URGENCY)],
                )
            )
        if _CREDENTIAL_REQUEST.search(text):
            signals.append(
                ImpersonationSignal(
                    kind="credential_request",
                    detail="post asks for credentials or a click",
                    weight=2.0,
                    evidence=[_snippet(text, _CREDENTIAL_REQUEST)],
                )
            )
        if _DEEPFAKE_CLAIM.search(text):
            signals.append(
                ImpersonationSignal(
                    kind="deepfake_claim",
                    detail="post discusses synthetic or manipulated media",
                    weight=1.5,
                    evidence=[_snippet(text, _DEEPFAKE_CLAIM)],
                )
            )
        for domain, notes in lookalikes.items():
            if domain in text:
                signals.append(
                    ImpersonationSignal(
                        kind="lookalike_domain",
                        detail="; ".join(notes),
                        weight=2.5,
                        evidence=[domain],
                    )
                )
        # Deliberately no "brand_name" signal: in a topic search every post
        # mentions the topic, so it would fire on the whole corpus and inflate
        # the distinct-signal count without adding information.
        if signals:
            by_post[post.identity] = signals

    if not by_post:
        return []

    # Aggregate by signal kind. Repeating "urgency" six times tells the analyst
    # nothing that "urgency, 6 posts" does not, and buries the distinguishing
    # signals under volume.
    by_kind: dict[str, list[ImpersonationSignal]] = defaultdict(list)
    for signals in by_post.values():
        for signal in signals:
            by_kind[signal.kind].append(signal)

    aggregated: list[ImpersonationSignal] = []
    for _kind, signals in sorted(by_kind.items()):
        exemplar = max(signals, key=lambda s: s.weight)
        evidence: list[str] = []
        for signal in signals:
            for item in signal.evidence:
                if item and item not in evidence:
                    evidence.append(item)
        aggregated.append(
            ImpersonationSignal(
                kind=exemplar.kind,  # type: ignore[arg-type]
                detail=f"{exemplar.detail} (observed on {plural(len(signals), 'post')})",
                weight=exemplar.weight,
                evidence=evidence[:3],
            )
        )

    # Distinct signal kinds drive severity, not raw repetition: ten posts that
    # all say "urgent" are one weak signal, not ten.
    distinct = {signal.kind for signal in aggregated}
    kinds_present = len(distinct)
    if kinds_present >= 4:
        severity, confidence = "high", "medium"
    elif kinds_present >= 3:
        severity, confidence = "medium", "low"
    elif kinds_present >= 2:
        severity, confidence = "low", "low"
    else:
        severity, confidence = "info", "low"

    findings.append(
        ImpersonationFinding(
            finding_id="imp001",
            subject=topic,
            post_ids=sorted(by_post),
            signals=aggregated,
            severity=severity,
            confidence=confidence,
            caveats=[
                "Signal co-occurrence is not proof of impersonation; a legitimate "
                "brand post can contain urgency and authority language.",
                "Lookalike domains are string-distance leads only — ownership, "
                "hosting and content were not checked.",
                "No media authenticity analysis was performed; deepfake references "
                "are claims in text, not forensic findings.",
            ],
        )
    )
    return findings


def _snippet(text: str, pattern: re.Pattern[str], width: int = 60) -> str:
    match = pattern.search(text)
    if not match:
        return ""
    start = max(0, match.start() - width)
    end = min(len(text), match.end() + width)
    return text[start:end].strip()
