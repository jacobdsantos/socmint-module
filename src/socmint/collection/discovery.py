"""Topic-driven source discovery via a free metasearch library.

Discovery returns *leads*: public channel handles and candidate post URLs. A
search-engine hit is not evidence of publication date or content, so leads are
kept separate from retrieved records until the original page is fetched.

DDGS is a free library fronting public search backends. Availability and rate
limits are outside our control; failures surface as ``StageStatus`` values
rather than being silently swallowed.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import unquote, urlparse

from ..models import Platform, QueryLog, StageStatus
from ..text import content_tokens, topic_terms

logger = logging.getLogger(__name__)

_TME_RE = re.compile(r"^/(?:s/)?([A-Za-z][A-Za-z0-9_]{3,31})/?$")


@dataclass(frozen=True)
class ChannelLead:
    username: str
    url: str
    title: str | None = None
    matched_term: str | None = None
    source_query: str | None = None


@dataclass
class DiscoveryResult:
    channels: list[ChannelLead] = field(default_factory=list)
    queries: list[QueryLog] = field(default_factory=list)
    status: StageStatus = StageStatus.EMPTY
    notes: list[str] = field(default_factory=list)


def telegram_queries(topic: str, aliases: list[str] | None = None) -> list[str]:
    """Search phrasings that reliably surface public channels.

    ``site:t.me/s/`` finds channels whose *title* contains the topic, which works
    for broad beats ("Philippines news") but returns noise for narrow topics
    ("Mayon"). The unrestricted ``<topic> telegram channel`` form finds topical
    channels that do not have the topic in their name, so both are issued.
    """
    terms = topic_terms(topic, aliases or [])
    queries: list[str] = []
    for term in terms[:3]:
        queries.append(f"site:t.me/s/ {term} news")
        queries.append(f"site:t.me/s/ {term}")
        queries.append(f"{term} telegram channel")
        queries.append(f"site:t.me/s/ {term} updates")
    return queries


def temporal_queries(
    topic: str,
    aliases: list[str] | None,
    start: datetime,
    end: datetime,
    max_queries: int = 6,
) -> list[str]:
    """Topic queries augmented with the window's month/year.

    Used where the platform has no native date operator. Keyword search ranks by
    relevance, not date, so month/year tokens surface contemporaneous material.
    This improves recall; it does not make search an exhaustive date index.
    """
    terms = topic_terms(topic, aliases or [])
    if not terms:
        return []
    base = terms[0]
    queries: list[str] = [base, *terms[1:]]

    months: list[str] = []
    cursor = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cursor < end and len(months) < 6:
        months.append(cursor.strftime("%B %Y"))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    for month in months:
        queries.append(f"{base} {month}")
    queries.append(f"{base} {start.year}")

    seen: set[str] = set()
    out: list[str] = []
    for query in queries:
        key = query.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(query)
    return out[:max_queries]


def youtube_queries(
    topic: str,
    aliases: list[str] | None,
    start: datetime,
    end: datetime,
    max_queries: int = 6,
) -> list[str]:
    """Date-bounded YouTube queries using the ``after:``/``before:`` operators.

    YouTube honours these operators in the search box and yt-dlp passes them
    through unchanged, which is materially better than ranking-then-filtering:
    an unbounded query for a topic whose main event was months earlier can return
    twenty results with none inside the window.

    ``after:D`` includes uploads from D+1, so we pass the day before the window
    start. ``before:D`` excludes D, which matches our half-open upper bound.
    """
    terms = topic_terms(topic, aliases or [])
    if not terms:
        return []
    after = (start - timedelta(days=1)).date().isoformat()
    before = end.date().isoformat()
    bound = f"after:{after} before:{before}"
    queries = [f"{term} {bound}" for term in terms]

    seen: set[str] = set()
    out: list[str] = []
    for query in queries:
        key = query.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(query)
    return out[:max_queries]


def parse_telegram_channel(url: str) -> str | None:
    """Extract a public channel username from a t.me URL, or None."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in {"t.me", "telegram.me", "www.t.me"}:
        return None
    match = _TME_RE.match(unquote(parsed.path))
    if not match:
        return None
    username = match.group(1)
    # Reserved/non-channel paths.
    if username.lower() in {"joinchat", "share", "iv", "s", "addstickers", "proxy"}:
        return None
    return username


def discover_telegram(
    topic: str,
    *,
    aliases: list[str] | None = None,
    max_queries: int = 6,
    max_results: int = 20,
    max_channels: int = 12,
    backend: str = "auto",
    sleep_between: float = 1.0,
) -> DiscoveryResult:
    """Find candidate public channels for *topic* using ordinary web search."""
    try:
        from ddgs import DDGS
    except ImportError:  # pragma: no cover - dependency declared in pyproject
        return DiscoveryResult(
            status=StageStatus.UNSUPPORTED,
            notes=["ddgs is not installed; discovery unavailable"],
        )

    terms = topic_terms(topic, aliases or [])
    # Individual significant words, used to judge whether a search hit is even
    # plausibly about the topic before spending a request on the channel.
    words = sorted({w for term in terms for w in content_tokens(term) if len(w) >= 4})
    result = DiscoveryResult()
    seen: set[str] = set()
    queries = telegram_queries(topic, aliases)[:max_queries]
    errors = 0

    for query in queries:
        log = QueryLog(platform=Platform.TELEGRAM, query=query, kind="discovery")
        started = time.monotonic()
        hits = None
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with DDGS() as client:
                    hits = client.text(query, max_results=max_results, backend=backend)
                last_exc = None
                break
            except Exception as exc:  # network/rate-limit/backend variance
                last_exc = exc
                if "no results" in str(exc).lower():
                    break
                if attempt < 2:
                    time.sleep(sleep_between * (attempt + 1))

        if last_exc is not None:
            exc = last_exc
            message = str(exc)
            if "no results" in message.lower():
                # An empty result set is a legitimate outcome, not a failure.
                log.status = StageStatus.EMPTY
                log.detail = "no results"
                log.finished_at = log.started_at
                result.queries.append(log)
                time.sleep(sleep_between)
                continue
            errors += 1
            log.status = (
                StageStatus.RATE_LIMITED if "rate" in message.lower() else StageStatus.FAILED
            )
            log.detail = f"{type(exc).__name__}: {exc}"
            log.finished_at = log.started_at
            result.queries.append(log)
            logger.warning("discovery query failed: %s (%s)", query, exc)
            time.sleep(sleep_between)
            continue

        log.results = len(hits or [])
        log.finished_at = log.started_at
        for hit in hits or []:
            href = hit.get("href") or hit.get("url") or ""
            username = parse_telegram_channel(href)
            if not username or username.lower() in seen:
                continue
            title = hit.get("title")
            haystack = f"{title or ''} {hit.get('body') or ''} {username}".lower()
            matched = next((t for t in terms if t.lower() in haystack), None)
            # A metasearch hit can be unrelated to the topic (site: filtering is
            # not guaranteed by every backend). Without this check, a query like
            # "Mayon volcano" yields Russian file-sharing channels.
            word_hit = next((w for w in words if w in haystack), None)
            if not matched and not word_hit:
                continue
            seen.add(username.lower())
            result.channels.append(
                ChannelLead(
                    username=username,
                    url=f"https://t.me/s/{username}",
                    title=title,
                    matched_term=matched or word_hit,
                    source_query=query,
                )
            )
            if len(result.channels) >= max_channels:
                break
        result.queries.append(log)
        if time.monotonic() - started < sleep_between:
            time.sleep(sleep_between)
        if len(result.channels) >= max_channels:
            result.notes.append(f"channel cap reached ({max_channels}); more may exist")
            break

    if errors and not result.channels:
        result.status = StageStatus.RATE_LIMITED if errors == len(queries) else StageStatus.FAILED
    elif result.channels:
        result.status = StageStatus.OK
    else:
        result.status = StageStatus.EMPTY
    return result
