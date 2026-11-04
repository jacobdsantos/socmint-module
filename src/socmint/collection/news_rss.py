"""Google News RSS collector.

A free, keyless topic search over a global news index. It answers the question
the social platforms cannot: "is this being reported, by whom, and when".

Why this rather than GDELT as the default news source: GDELT enforces one
request every five seconds per IP with no ``Retry-After``, and behind a shared
corporate NAT that quota is routinely already spent -- every call returns 429
even after a minute idle. Google News RSS answered immediately from the same
network. The GDELT adapter is kept and can be enabled where the network allows.

Known limits, surfaced rather than hidden:
* Only the headline, publisher and publication time are retrieved. The article
  body is never fetched, so ``evidence_quality`` is ``partial``.
* The RSS ``link`` is a Google redirect that resolves to the article; the
  publisher's own URL is not exposed, only its domain via ``<source>``.
* Google's index includes user-generated sources (Facebook pages, blogs), so a
  hit is evidence of *publication*, not of editorial standards.
* The feed returns at most ~100 items regardless of the requested window.
"""

from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
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

_FEED = "https://news.google.com/rss/search"
_HOSTS = frozenset({"news.google.com"})


@dataclass
class NewsFetch:
    posts: list[Post] = field(default_factory=list)
    queries: list[QueryLog] = field(default_factory=list)
    status: StageStatus = StageStatus.EMPTY
    notes: list[str] = field(default_factory=list)
    truncated: bool = False


def build_query(
    topic: str,
    aliases: list[str] | None = None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> str:
    """Topic terms plus Google News date operators, deliberately unquoted.

    Quoting is the obvious way to ask for a phrase and it is the wrong one here:
    ``"typhoon Philippines" when:11d`` and ``"typhoon Philippines"
    after:... before:...`` both return **zero** items, while the unquoted form
    of either returns 30-50. Google applies its date operators only to unquoted
    queries, so a quoted phrase silently produces an empty feed that looks like
    "no coverage" rather than "bad query".

    The date bounds are padded by a day on each side and the exact window is
    applied locally afterwards, so the boundary is inclusive regardless of how
    ``after:`` and ``before:`` treat their own edges.
    """
    terms = [topic.strip(), *(a.strip() for a in (aliases or []))]
    terms = [t for t in terms if t]
    if not terms:
        return ""
    query = " OR ".join(terms)
    if start and end:
        query += (
            f" after:{(start - timedelta(days=1)).date().isoformat()}"
            f" before:{(end + timedelta(days=1)).date().isoformat()}"
        )
    return query


def _reject_unsafe_xml(text: str) -> None:
    """Refuse entity declarations before parsing.

    ``xml.etree`` does not resolve external entities, but an internal entity
    expansion ("billion laughs") is still a denial of service. The feed is
    third-party content, so the cheap guard goes in front of the parser.
    """
    lowered = text[:4096].lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise ValueError("feed contains a DTD or entity declaration")


def _parse_pubdate(raw: str | None) -> tuple[datetime | None, Precision]:
    if not raw:
        return None, Precision.UNKNOWN
    try:
        dt = parsedate_to_datetime(str(raw).strip())
    except (TypeError, ValueError):
        return None, Precision.UNKNOWN
    if dt is None:
        return None, Precision.UNKNOWN
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC), Precision.SECOND


def parse_feed(
    xml_text: str,
    *,
    topic: str | None = None,
    aliases: list[str] | None = None,
) -> list[Post]:
    """Map one RSS document to posts. Pure function -- testable offline."""
    _reject_unsafe_xml(xml_text)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    posts: list[Post] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        published, precision = _parse_pubdate(item.findtext("pubDate"))

        source = item.find("source")
        domain = ""
        publisher = ""
        if source is not None:
            domain = (source.get("url") or "").strip()
            publisher = (source.text or "").strip()
        domain = domain.split("://")[-1].split("/")[0] or "news.google.com"

        guid = (item.findtext("guid") or "").strip()
        post_id = guid or hashlib.sha1(link.encode("utf-8", "ignore")).hexdigest()[:16]

        post = Post(
            platform=Platform.GOOGLE_NEWS,
            post_id=post_id,
            url=link,
            author_id=domain,
            author_name=publisher or domain,
            title=title,
            text=title,
            published_at=published,
            precision=precision,
            published_source="pubDate" if published else None,
            evidence_quality=(
                EvidenceQuality.PARTIAL if published else EvidenceQuality.LEAD
            ),
            provenance="news.google.com/rss/search",
            referenced_urls=[link],
        )
        if topic:
            # Unlike YouTube, the query here cannot be a phrase (see
            # build_query), so an unquoted AND match is loose enough to return
            # a 2013 explainer page and a surf contest for a 2026 typhoon.
            # Headlines are short and factual, so literal post-level matching
            # removes that noise cheaply.
            matched = topic_match_terms(
                " ".join(filter(None, [post.title, post.text])), topic, aliases or []
            )
            post.topic_terms_matched = matched
            post.topic_match = bool(matched)
        posts.append(post)
    return posts


def collect(
    topic: str,
    *,
    aliases: list[str] | None = None,
    window_start: datetime,
    window_end: datetime,
    budgets: Budgets,
) -> NewsFetch:
    """Search Google News for the topic and keep items inside the window."""
    result = NewsFetch()
    query = build_query(topic, aliases, start=window_start, end=window_end)
    if not query:
        result.notes.append("no query could be built from the topic")
        return result

    params = {
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    }
    url = f"{_FEED}?{urlencode(params)}"
    log = QueryLog(platform=Platform.GOOGLE_NEWS, query=url, kind="search")

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
        result.notes.append(f"Google News query failed ({exc})")
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

    if response.status_code != 200:
        result.status = (
            StageStatus.RATE_LIMITED
            if response.status_code == 429
            else StageStatus.FAILED
        )
        result.notes.append(f"Google News returned HTTP {response.status_code}")
        return result

    try:
        posts = parse_feed(response.text, topic=topic, aliases=aliases)
    except ValueError as exc:
        result.notes.append(f"Google News feed rejected: {exc}")
        result.status = StageStatus.FAILED
        return result

    kept = [
        p
        for p in posts
        if p.published_at is not None
        and window_start <= p.published_at < window_end
    ]
    result.posts = kept[: budgets.google_news_max_items]
    result.truncated = len(kept) > budgets.google_news_max_items
    if result.truncated:
        result.notes.append(
            f"Google News item cap reached ({budgets.google_news_max_items})"
        )
    result.status = StageStatus.OK if result.posts else StageStatus.EMPTY
    if not result.posts:
        result.notes.append(
            "Google News returned no article inside the window for this query"
        )
    return result


__all__ = ["NewsFetch", "build_query", "collect", "parse_feed"]
