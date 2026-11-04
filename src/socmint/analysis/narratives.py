"""Multilingual narrative candidates.

Pipeline: compose a comparable text field -> deduplicate exact repeats ->
embed (or fall back to TF-IDF) -> average-linkage cosine clustering -> describe
each cluster extractively.

A cluster is a *topic/framing candidate*, not a verified narrative and never a
statement about stance. Opposing posts frequently share vocabulary; reviewers
split them. The cluster label is extracted from member text, never generated.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from ..models import NarrativeCandidate, Post
from ..settings import AnalysisSettings
from ..text import distinctive_keywords, keywords, truncate

logger = logging.getLogger(__name__)


@dataclass
class NarrativeResult:
    narratives: list[NarrativeCandidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    degraded: bool = False
    method: str = "embedding"
    # Centred vectors for the clustered posts, aligned with ``vector_ids``.
    # Reused by coordination so the model runs once per process.
    vectors: np.ndarray | None = None
    vector_ids: list[str] = field(default_factory=list)


_MODEL_CACHE: dict[str, object] = {}


def _load_model(settings: AnalysisSettings):
    key = f"{settings.embedding_model}@{settings.embedding_revision}"
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None
    try:
        model = SentenceTransformer(
            settings.embedding_model,
            revision=settings.embedding_revision,
            device="cpu",
        )
    except Exception as exc:  # model unavailable / no cache / offline
        logger.warning("embedding model unavailable: %s", exc)
        return None
    _MODEL_CACHE[key] = model
    return model


def post_text(post: Post) -> str:
    """The field used for semantic comparison, composed deterministically."""
    parts = [post.title or "", post.text or ""]
    return " ".join(p.strip() for p in parts if p and p.strip())


def _embed(texts: list[str], settings: AnalysisSettings) -> np.ndarray | None:
    model = _load_model(settings)
    if model is None:
        return None
    # e5 models expect the "query: " prefix when embeddings are features.
    prefixed = [f"query: {t}" for t in texts]
    vectors = model.encode(
        prefixed,
        batch_size=16,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return _center(np.asarray(vectors, dtype=np.float32))


def _center(vectors: np.ndarray) -> np.ndarray:
    """Remove the shared component of e5 embeddings.

    Raw e5 cosine sits in a narrow 0.7-1.0 band, so unrelated text scores ~0.76
    and related text ~0.87 -- distributions that overlap. Centring on the batch
    mean widens the gap to the point where a single threshold separates them.
    Thresholds in ``AnalysisSettings`` therefore refer to *centred* cosine.
    """
    if len(vectors) < 2:
        return vectors
    centred = vectors - vectors.mean(axis=0, keepdims=True)
    norms = np.linalg.norm(centred, axis=1, keepdims=True)

    # A vector identical to the batch mean centres to zero. That happens when
    # every post is the same text, and zero vectors break cosine clustering.
    # Fall back to the original direction for those rows: identical posts then
    # have similarity 1.0 with each other, which is the correct answer.
    degenerate = norms.ravel() < 1e-8
    if degenerate.any():
        original = vectors[degenerate]
        original_norms = np.linalg.norm(original, axis=1, keepdims=True)
        original_norms[original_norms == 0] = 1.0
        centred[degenerate] = original / original_norms
        norms[degenerate] = 1.0

    norms[norms < 1e-8] = 1.0
    return centred / norms


def _tfidf(texts: list[str]) -> np.ndarray:
    from sklearn.feature_extraction.text import TfidfVectorizer

    vectorizer = TfidfVectorizer(
        lowercase=True,
        ngram_range=(1, 2),
        min_df=1,
        max_features=20_000,
        sublinear_tf=True,
    )
    matrix = vectorizer.fit_transform(texts)
    dense = matrix.toarray().astype(np.float32)
    norms = np.linalg.norm(dense, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return _center(dense / norms)


def _cluster(vectors: np.ndarray, similarity: float) -> np.ndarray:
    """Average-linkage cosine clustering; avoids the chaining of connected components."""
    from sklearn.cluster import AgglomerativeClustering

    if len(vectors) == 1:
        return np.zeros(1, dtype=int)
    model = AgglomerativeClustering(
        n_clusters=None,
        metric="cosine",
        linkage="average",
        distance_threshold=max(0.0, 1.0 - similarity),
    )
    return model.fit_predict(vectors)


def _label(member_texts: list[str], corpus_texts: list[str], topic: str) -> str:
    kws = distinctive_keywords(member_texts, corpus_texts, top_n=5)
    if not kws:
        kws = keywords(member_texts, top_n=5)
    if kws:
        return " / ".join(kws[:4])
    return truncate(member_texts[0], 60) if member_texts else topic


def build_narratives(
    posts: list[Post],
    *,
    topic: str,
    settings: AnalysisSettings,
    force_lexical: bool = False,
) -> NarrativeResult:
    result = NarrativeResult()
    eligible = [p for p in posts if len(post_text(p)) >= settings.min_text_chars]
    skipped = len(posts) - len(eligible)
    if skipped:
        result.notes.append(
            f"{skipped} post(s) below the {settings.min_text_chars}-character minimum were "
            "not clustered (kept as singleton evidence)"
        )
    if not eligible:
        result.notes.append("no posts had enough text for narrative grouping")
        result.degraded = True
        return result

    texts = [post_text(p) for p in eligible]

    vectors: np.ndarray | None = None
    if not force_lexical:
        vectors = _embed(texts, settings)
    if vectors is None:
        result.degraded = True
        result.method = "tfidf_lexical"
        result.notes.append(
            "multilingual embedding model unavailable; used TF-IDF lexical grouping. "
            "Cross-language grouping is NOT reliable in this mode."
        )
        vectors = _tfidf(texts)
    else:
        result.method = "embedding"

    labels = _cluster(vectors, settings.narrative_similarity)
    result.vectors = vectors
    result.vector_ids = [p.identity for p in eligible]

    groups: dict[int, list[int]] = {}
    for idx, label in enumerate(labels):
        groups.setdefault(int(label), []).append(idx)

    narratives: list[NarrativeCandidate] = []
    for order, (_, members) in enumerate(
        sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ):
        member_posts = [eligible[i] for i in members]
        member_texts = [texts[i] for i in members]
        centroid = vectors[members].mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm:
            centroid = centroid / norm
        sims = vectors[members] @ centroid
        representative = [
            member_posts[i].identity
            for i in np.argsort(-sims)[: min(3, len(member_posts))]
        ]
        timestamps = [p.published_at for p in member_posts if p.published_at]
        platform_counts = Counter(str(p.platform) for p in member_posts)
        narratives.append(
            NarrativeCandidate(
                narrative_id=f"n{order + 1:03d}",
                label=_label(member_texts, texts, topic),
                keywords=distinctive_keywords(member_texts, texts, top_n=8) or keywords(member_texts, top_n=8),
                member_ids=[p.identity for p in member_posts],
                representative_ids=representative,
                platform_counts=dict(platform_counts),
                first_seen=min(timestamps) if timestamps else None,
                method=result.method,
                notes=[
                    "Cluster is a semantic/framing candidate; confirm label and stance manually.",
                ],
            )
        )
        if len(narratives) >= settings.max_narratives:
            result.notes.append(f"narrative cap reached ({settings.max_narratives})")
            break

    result.narratives = narratives
    return result


def volume_series(posts: list[Post], bucket: str = "day") -> list[dict]:
    """Posts per bucket per platform. Uses verified publication dates only."""
    counts: dict[tuple[str, str], int] = Counter()
    for post in posts:
        if not post.published_at:
            continue
        if bucket == "hour":
            key = post.published_at.strftime("%Y-%m-%dT%H")
        else:
            key = post.published_at.strftime("%Y-%m-%d")
        counts[(key, str(post.platform))] += 1
    rows = [
        {"bucket": key, "platform": platform, "count": count}
        for (key, platform), count in sorted(counts.items())
    ]
    return rows


def earliest_by_narrative(
    narratives: list[NarrativeCandidate], posts_by_id: dict[str, Post]
) -> list[dict]:
    out = []
    for narrative in narratives:
        dated = [
            posts_by_id[i]
            for i in narrative.member_ids
            if i in posts_by_id and posts_by_id[i].published_at
        ]
        if not dated:
            continue
        first = min(dated, key=lambda p: p.published_at)  # type: ignore[arg-type,return-value]
        out.append(
            {
                "narrative_id": narrative.narrative_id,
                "observed_at": first.published_at,
                "post_ids": [p.identity for p in dated if p.published_at == first.published_at],
                "accounts": sorted({p.author_id or p.author_name or "" for p in dated if p.published_at == first.published_at}),
                "platforms": sorted({str(p.platform) for p in dated if p.published_at == first.published_at}),
            }
        )
    return out


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
