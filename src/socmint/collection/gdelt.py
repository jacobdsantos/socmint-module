"""GDELT DOC 2.0 news collector.

GDELT indexes global online news in more than 60 languages and serves a public,
keyless search API. It is the one source here that answers a different question
from the social platforms: not "what are people saying" but "is this being
reported, in which countries, in which languages".

That distinction is enforced elsewhere: ``models.NEWS_PLATFORMS`` keeps these
records out of coordination and out of account-based corroboration, so a press
mention can never be counted as an independent social account.

Known limits, surfaced rather than hidden:
* Only the headline and metadata are retrieved. The article body is not fetched
  (previews and links only), so ``evidence_quality`` is ``partial``.
* GDELT's own indexing lag and source list bound coverage; an absent article is
  not evidence that no outlet covered the topic.
* The API rate-limits aggressively (HTTP 429) and returns that limit without a
  ``Retry-After``, so retries use a fixed backoff.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlencode

from ..models import (
    EvidenceQuality,
    Platform,
    Post,
    Precision,
    QueryLog,
    StageStatus,
)
from ..security import safe_get
from ..settings import Budgets
from ..text import topic_match_terms

logger = logging.getLogger(__name__)

_API = "https://api.gdeltproject.org/api/v2/doc/doc"
_HOSTS = frozenset({"api.gdeltproject.org"})
_GMT_FMT = "%Y%m%d%H%M%S"


@dataclass
class NewsFetch:
    posts: list[Post] = field(default_factory=list)
    queries: list[QueryLog] = field(default_factory=list)
    status: StageStatus = StageStatus.EMPTY
    notes: list[str] = field(default_factory=list)
    truncated: bool = False


def build_query(topic: str, aliases: list[str] | None = None) -> str:
    """Phrase-quote the topic and OR the aliases, which is GDELT's own syntax."""
    terms = [topic.strip(), *(a.strip() for a in (aliases or []))]
    terms = [t for t in terms if t]
    if not terms:
        return ""
    if len(terms) == 1:
        return f'"{terms[0]}"'
    return "(" + " OR ".join(f'"{t}"' for t in terms) + ")"


def _parse_seendate(raw: str | None) -> tuple[datetime | None, Precision]:
    """GDELT sends ``YYYYMMDDTHHMMSSZ``; some mirrors drop the ``T``."""
    if not raw:
        return None, Precision.UNKNOWN
    cleaned = str(raw).strip().replace("Z", "")
    dt: datetime | None = None
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%d%H%M%S"):
        try:
            dt = datetime.strptime(cleaned, fmt).replace(tzinfo=UTC)
            break
        except ValueError:
            continue
    if dt is None:
        return None, Precision.UNKNOWN
    return dt, Precision.SECOND


def _article_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()[:16]


def parse_articles(
    payload: dict,
    *,
    topic: str | None = None,
    aliases: list[str] | None = None,
) -> list[Post]:
    """Map one DOC 2.0 response to posts. Pure function -- testable offline."""
    articles = payload.get("articles") if isinstance(payload, dict) else None
    if not isinstance(articles, list):
        return []

    posts: list[Post] = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        url = str(article.get("url") or "").strip()
        title = str(article.get("title") or "").strip()
        if not url or not title:
            continue
        published, precision = _parse_seendate(article.get("seendate"))
        domain = str(article.get("domain") or "").strip() or None
        if not domain:
            # Fall back to the URL host so the source is never silently blank.
            domain = url.split("://")[-1].split("/")[0] or None

        post = Post(
            platform=Platform.GDELT,
            post_id=_article_id(url),
            url=url,
            author_id=domain,
            author_name=domain,
            title=title,
            text=title,
            language=str(article.get("language") or "").strip() or None,
            published_at=published,
            precision=precision,
            published_source="seendate" if published else None,
            evidence_quality=(
                EvidenceQuality.PARTIAL if published else EvidenceQuality.LEAD
            ),
            provenance="api.gdeltproject.org/v2/doc",
            referenced_urls=[url],
        )
        if topic:
            # Relevance is inherited from the query, as with YouTube: the search
            # was the topic, so a hit is a hit. Literal term matches are still
            # recorded where the headline happens to carry them.
            post.topic_terms_matched = topic_match_terms(
                " ".join(filter(None, [post.title, post.text])), topic, aliases or []
            )
            post.topic_match = True
        posts.append(post)
    return posts


def collect(
    topic: str,
    *,
    aliases: list[str] | None = None,
    window_start: datetime,
    window_end: datetime,
    budgets: Budgets,
    max_queries: int | None = None,
) -> NewsFetch:
    """Query GDELT for the topic inside the requested window."""
    result = NewsFetch()
    query = build_query(topic, aliases)
    if not query:
        result.notes.append("no query could be built from the topic")
        return result

    # GDELT's published limit is one request every five seconds, enforced by IP
    # without a Retry-After. A 2-second backoff just burns an attempt and still
    # gets a 429, so the floor is above their window and grows per attempt.
    attempts = max(1, min(3, budgets.max_retries + 1))
    backoff_floor = max(6.0, budgets.retry_backoff_seconds)
    for attempt in range(attempts):
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": str(budgets.gdelt_max_records),
            "sort": "datedesc",
            "startdatetime": window_start.strftime(_GMT_FMT),
            "enddatetime": window_end.strftime(_GMT_FMT),
        }
        url = f"{_API}?{urlencode(params)}"
        log = QueryLog(platform=Platform.GDELT, query=url, kind="search")

        try:
            response = safe_get(
                url,
                timeout=budgets.request_timeout_seconds,
                max_bytes=budgets.max_response_bytes,
                user_agent=budgets.user_agent,
                allowed_hosts=_HOSTS,
            )
        except Exception as exc:  # noqa: BLE001 - a source failure must not abort the run
            log.status = StageStatus.FAILED
            log.detail = f"{type(exc).__name__}: {exc}"
            result.queries.append(log)
            result.notes.append(f"GDELT query failed ({exc})")
            result.status = StageStatus.FAILED
            return result

        if response.status_code == 200:
            log.status = StageStatus.OK
        elif response.status_code == 429:
            log.status = StageStatus.RATE_LIMITED
        else:
            log.status = StageStatus.FAILED
        log.detail = f"HTTP {response.status_code}"
        result.queries.append(log)

        if response.status_code == 429 and attempt + 1 < attempts:
            # GDELT sends no Retry-After; wait out their five-second window.
            time.sleep(backoff_floor * (attempt + 1))
            continue
        if response.status_code != 200:
            result.status = (
                StageStatus.RATE_LIMITED
                if response.status_code == 429
                else StageStatus.FAILED
            )
            note = f"GDELT returned HTTP {response.status_code}"
            if response.status_code == 429:
                note += " (one request per five seconds, per IP)"
            result.notes.append(note)
            return result

        try:
            payload = response.json()
        except ValueError:
            result.notes.append("GDELT returned a non-JSON body")
            result.status = StageStatus.FAILED
            return result

        posts = parse_articles(payload, topic=topic, aliases=aliases)
        kept = [
            p
            for p in posts
            if p.published_at is not None
            and window_start <= p.published_at < window_end
        ]
        result.posts.extend(kept)
        result.status = StageStatus.OK if kept else StageStatus.EMPTY
        if not kept:
            result.notes.append(
                "GDELT returned no article inside the window for this query"
            )
        result.truncated = len(kept) >= budgets.gdelt_max_records
        if result.truncated:
            result.notes.append(
                f"GDELT result cap reached ({budgets.gdelt_max_records} articles)"
            )
        return result

    return result


__all__ = ["NewsFetch", "build_query", "collect", "parse_articles"]
