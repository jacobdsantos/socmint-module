"""Text normalisation, language hints, and near-duplicate detection.

Shared by discovery relevance filtering and analysis. Kept dependency-light so
the collection path works without the ML extras installed.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable

from langdetect import DetectorFactory, LangDetectException, detect_langs

DetectorFactory.seed = 0  # deterministic language detection

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MENTION_RE = re.compile(r"@\w+")
_WS_RE = re.compile(r"\s+")
_HASHTAG_RE = re.compile(r"#([\wÀ-ɏЀ-ӿ؀-ۿ぀-ヿ一-鿿]+)")
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

# Small, language-agnostic stop list. Not exhaustive by design.
_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "have", "has", "are",
    "was", "were", "you", "your", "they", "their", "not", "but", "all", "can",
    "will", "just", "about", "into", "than", "then", "them", "there", "here",
    "ang", "ng", "sa", "na", "at", "ay", "mga", "para", "hindi", "kung",
    "el", "la", "los", "las", "de", "del", "que", "en", "por", "con", "una",
    "und", "der", "die", "das", "ist", "nicht", "mit", "für",
}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = _URL_RE.sub(" ", text)
    text = _MENTION_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(normalise(text))]


def content_tokens(text: str, min_len: int = 3) -> list[str]:
    return [t for t in tokens(text) if len(t) >= min_len and t not in _STOPWORDS]


def hashtags(text: str) -> list[str]:
    return [h.lower() for h in _HASHTAG_RE.findall(text or "")]


def detect_language(text: str, min_chars: int = 20) -> tuple[str | None, float | None]:
    """Return (iso639-1, confidence) or (None, None) when undetermined."""
    cleaned = normalise(text)
    if len(cleaned) < min_chars:
        return None, None
    try:
        guesses = detect_langs(cleaned)
    except LangDetectException:
        return None, None
    if not guesses:
        return None, None
    top = guesses[0]
    confidence = float(top.prob)
    if confidence < 0.55:
        return None, confidence
    return top.lang, confidence


def shingles(text: str, k: int = 5) -> frozenset[str]:
    toks = content_tokens(text)
    if len(toks) < k:
        return frozenset(toks)
    return frozenset(" ".join(toks[i : i + k]) for i in range(len(toks) - k + 1))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def text_fingerprint(text: str) -> str:
    return hashlib.sha256(normalise(text).lower().encode("utf-8")).hexdigest()[:16]


def keywords(texts: Iterable[str], top_n: int = 6) -> list[str]:
    """Frequency-based extractive keywords. No generation, no invention."""
    counts: dict[str, int] = {}
    for text in texts:
        for tok in content_tokens(text):
            counts[tok] = counts.get(tok, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [word for word, _ in ranked[:top_n]]


def distinctive_keywords(
    member_texts: Iterable[str],
    corpus_texts: Iterable[str],
    top_n: int = 6,
) -> list[str]:
    """Terms over-represented in a cluster relative to the whole corpus.

    Plain frequency labels every cluster with the topic's own vocabulary
    ("earthquake", "news"). Comparing against corpus frequency surfaces what
    actually distinguishes one cluster from another.
    """
    corpus_counts: dict[str, int] = {}
    corpus_total = 0
    for text in corpus_texts:
        for tok in content_tokens(text):
            corpus_counts[tok] = corpus_counts.get(tok, 0) + 1
            corpus_total += 1

    member_counts: dict[str, int] = {}
    member_total = 0
    for text in member_texts:
        for tok in content_tokens(text):
            member_counts[tok] = member_counts.get(tok, 0) + 1
            member_total += 1

    if not member_total or not corpus_total:
        return []

    scored: list[tuple[float, str]] = []
    for tok, count in member_counts.items():
        if count < 2:
            continue
        member_rate = count / member_total
        corpus_rate = (corpus_counts.get(tok, 0) + 1) / (corpus_total + 1)
        scored.append((member_rate / corpus_rate, tok))
    scored.sort(key=lambda kv: (-kv[0], kv[1]))
    return [tok for _, tok in scored[:top_n]]


def truncate(text: str, limit: int = 180) -> str:
    text = normalise(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def topic_terms(topic: str, aliases: Iterable[str] = ()) -> list[str]:
    """Search/relevance terms derived from the topic without inventing synonyms."""
    terms: list[str] = []
    raw = [topic, *aliases]
    for item in raw:
        item = item.strip()
        if not item:
            continue
        terms.append(item)
        stripped = item.lstrip("#").strip()
        if stripped and stripped != item:
            terms.append(stripped)
    # de-duplicate preserving order, case-insensitive
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            out.append(term)
    return out


def matches_topic(haystack: str, terms: Iterable[str]) -> bool:
    """Case-insensitive substring match on normalised text.

    Deliberately literal: no stemming or synonym expansion, so a match is
    explainable as 'this string occurred'.
    """
    hay = normalise(haystack).lower()
    if not hay:
        return False
    return any(term.lower() in hay for term in terms if term.strip())


def topic_match_terms(haystack: str, topic: str, aliases: Iterable[str] = ()) -> list[str]:
    """Return which topic expressions a post actually contains.

    A multi-word topic requires **every** significant word (so "earthquake
    Philippines" does not match a post about a Japanese quake). An alias is an
    alternative, so any one alias phrase is sufficient.

    Matching is literal. We do not stem, translate, or expand synonyms, so the
    result is explainable as "this string occurred in the post".
    """
    hay = normalise(haystack).lower()
    if not hay:
        return []

    matched: list[str] = []
    phrase = normalise(topic).lower().strip()
    if phrase and phrase in hay:
        matched.append(topic)

    words = [w for w in content_tokens(topic) if len(w) >= 3]
    if words and all(word in hay for word in words):
        matched.append(topic)

    for alias in aliases:
        alias_phrase = normalise(alias).lower().strip()
        if alias_phrase and alias_phrase in hay:
            matched.append(alias)
            continue
        alias_words = [w for w in content_tokens(alias) if len(w) >= 3]
        if alias_words and all(word in hay for word in alias_words):
            matched.append(alias)

    seen: set[str] = set()
    out: list[str] = []
    for item in matched:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
