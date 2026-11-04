"""Public Mastodon tag collector.

Reads the ordinary public hashtag timeline (``/api/v1/timelines/tag/<tag>``),
which most instances serve without an account. This is **tag-seeded** retrieval:
a topic only yields posts if somebody used a hashtag derived from it.

Known limits, surfaced rather than hidden:
* Anonymous search (``/api/v2/search``) needs a token, so there is no keyword
  index to fall back on. A topic with no hashtag yields nothing.
* An instance may disable public preview, in which case the endpoint returns
  401 and that instance is skipped.
* Mastodon is federated: collecting from four instances is a sample of four
  instances, not of "Mastodon".
* Tag timelines are reverse-chronological and paginate by ``max_id``; the
  budget, not the platform, bounds how far back we read.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..models import (
    Engagement,
    EvidenceQuality,
    MediaKind,
    MediaRef,
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

_PAGE_LIMIT = 40  # instance maximum for this endpoint
_HASHTAG_RE = re.compile(r"^[a-z0-9_]{2,60}$")


@dataclass
class TagFetch:
    posts: list[Post] = field(default_factory=list)
    queries: list[QueryLog] = field(default_factory=list)
    status: StageStatus = StageStatus.EMPTY
    notes: list[str] = field(default_factory=list)
    truncated: bool = False


def candidate_tags(topic: str, aliases: list[str] | None = None, limit: int = 4) -> list[str]:
    """Derive hashtags from the topic without inventing synonyms.

    Multi-word topics are tried as a joined tag (``#philippinestyphoon``) and as
    individual words (``#philippines``). Both forms are literal readings of what
    the user typed, which keeps the derivation explainable.
    """
    raw = [topic, *(aliases or [])]
    candidates: list[str] = []
    for item in raw:
        cleaned = item.strip().lstrip("#").strip()
        if not cleaned:
            continue
        words = [w for w in re.split(r"[^\w]+", cleaned.lower()) if w]
        if len(words) > 1:
            candidates.append("".join(words))
        candidates.extend(words)

    seen: set[str] = set()
    out: list[str] = []
    for tag in candidates:
        if tag in seen or not _HASHTAG_RE.match(tag):
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= limit:
            break
    return out


def _text_of(html: str) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)


def _parse_time(raw: str | None) -> tuple[datetime | None, Precision]:
    if not raw:
        return None, Precision.UNKNOWN
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None, Precision.UNKNOWN
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    precision = Precision.SECOND if (dt.second or dt.microsecond) else Precision.MINUTE
    return dt.astimezone(UTC), precision


def _media(status: dict) -> list[MediaRef]:
    media: list[MediaRef] = []
    for item in status.get("media_attachments") or []:
        kind = {
            "image": MediaKind.PHOTO,
            "video": MediaKind.VIDEO,
            "gifv": MediaKind.VIDEO,
        }.get(str(item.get("type", "")).lower())
        url = item.get("url") or item.get("preview_url")
        if kind and url:
            media.append(MediaRef(kind=kind, url=url))
    return media


def parse_statuses(statuses: list[dict], instance: str) -> list[Post]:
    """Map one API page of statuses to posts. Pure function -- testable offline."""
    posts: list[Post] = []
    for status in statuses:
        if not isinstance(status, dict):
            continue
        status_id = str(status.get("id") or "")
        if not status_id:
            continue
        # ``id`` is instance-local, so the same federated status has a different
        # id on every instance that carries it. ``uri`` is the canonical
        # cross-instance identity; use it so four instances do not yield four
        # copies of one post.
        canonical = str(status.get("uri") or "").strip()
        identity = canonical or status_id
        # A boost is a source-stated repost; the original content is nested.
        is_repost = status.get("reblog") is not None
        content = status.get("reblog") if is_repost else status
        if not isinstance(content, dict):
            continue

        text = _text_of(str(content.get("content") or ""))
        published, precision = _parse_time(status.get("created_at"))
        account = content.get("account") or {}
        acct = str(account.get("acct") or account.get("username") or "") or None
        display = str(account.get("display_name") or "").strip() or acct

        favourites = status.get("favourites_count")
        reblogs = status.get("reblogs_count")
        replies = status.get("replies_count")

        posts.append(
            Post(
                platform=Platform.MASTODON,
                post_id=identity,
                url=str(status.get("url") or status.get("uri") or ""),
                author_id=acct,
                author_name=display,
                text=text,
                language=content.get("language") or status.get("language"),
                published_at=published,
                precision=precision,
                published_source="created_at" if published else None,
                engagement=Engagement(
                    likes=favourites if isinstance(favourites, int) else None,
                    shares=reblogs if isinstance(reblogs, int) else None,
                    replies=replies if isinstance(replies, int) else None,
                    missing_reason=(
                        None
                        if isinstance(favourites, int)
                        else "counts not returned by this instance"
                    ),
                ),
                media=_media(content),
                is_repost=is_repost,
                reply_to=(
                    str(content.get("in_reply_to_id"))
                    if content.get("in_reply_to_id")
                    else None
                ),
                referenced_urls=re.findall(r"https?://\S+", text),
                evidence_quality=(
                    EvidenceQuality.ORIGINAL if text and published else EvidenceQuality.PARTIAL
                ),
                provenance=f"{instance}/api/v1/timelines/tag",
            )
        )
    return posts


def collect_tag(
    tag: str,
    *,
    instances: tuple[str, ...] | list[str],
    window_start: datetime,
    window_end: datetime,
    budgets: Budgets,
    topic: str | None = None,
    aliases: list[str] | None = None,
    max_pages: int | None = None,
) -> TagFetch:
    """Read one hashtag across the configured instances, newest first."""
    result = TagFetch()
    pages = max_pages if max_pages is not None else budgets.max_pages_per_source
    reached_older = False

    for instance in instances:
        before: str | None = None
        for page in range(pages):
            params = [f"limit={_PAGE_LIMIT}"]
            if before:
                params.append(f"max_id={quote(before)}")
            url = f"https://{instance}/api/v1/timelines/tag/{quote(tag)}?{'&'.join(params)}"

            log = QueryLog(platform=Platform.MASTODON, query=url, kind="page")
            try:
                response = safe_get(
                    url,
                    timeout=budgets.request_timeout_seconds,
                    max_bytes=budgets.max_response_bytes,
                    user_agent=budgets.user_agent,
                    allowed_hosts=frozenset(instances),
                )
            except Exception as exc:  # noqa: BLE001 - one bad instance must not abort the run
                log.status = StageStatus.FAILED
                log.detail = f"{type(exc).__name__}: {exc}"
                result.queries.append(log)
                result.notes.append(f"{instance}/{tag}: page {page + 1} failed ({exc})")
                break

            if response.status_code == 200:
                log.status = StageStatus.OK
            elif response.status_code in {401, 403}:
                # Public preview disabled on this instance.
                log.status = StageStatus.BLOCKED_AUTH
            elif response.status_code == 429:
                log.status = StageStatus.RATE_LIMITED
            else:
                log.status = StageStatus.FAILED
            log.detail = f"HTTP {response.status_code}"
            result.queries.append(log)

            if response.status_code != 200:
                if response.status_code == 429:
                    result.status = StageStatus.RATE_LIMITED
                result.notes.append(
                    f"{instance}/{tag}: HTTP {response.status_code}, instance skipped"
                )
                break

            try:
                payload = response.json()
            except ValueError:
                result.notes.append(f"{instance}/{tag}: response was not JSON")
                break
            if not isinstance(payload, list):
                result.notes.append(f"{instance}/{tag}: unexpected payload shape")
                break
            if not payload:
                break

            page_posts = parse_statuses(payload, instance)
            if topic:
                for post in page_posts:
                    matched = topic_match_terms(
                        " ".join(filter(None, [post.title, post.text])), topic, aliases or []
                    )
                    post.topic_match = bool(matched)
                    post.topic_terms_matched = matched

            oldest = min((p.published_at for p in page_posts if p.published_at), default=None)
            for post in page_posts:
                if topic and not post.topic_match:
                    continue
                if post.published_at is None:
                    continue
                if window_start <= post.published_at < window_end:
                    result.posts.append(post)

            if oldest and oldest < window_start:
                reached_older = True
                break

            before = str(payload[-1].get("id") or "")
            if not before:
                break
            time.sleep(0.3)  # be gentle with public instances

    if result.posts:
        result.status = StageStatus.OK
        if reached_older:
            result.notes.append(f"#{tag}: reached posts older than window start")
    elif any(q.status == StageStatus.RATE_LIMITED for q in result.queries):
        result.status = StageStatus.RATE_LIMITED
    result.truncated = len(result.posts) >= budgets.max_posts_per_run
    return result


def collect(
    topic: str,
    *,
    aliases: list[str] | None = None,
    window_start: datetime,
    window_end: datetime,
    budgets: Budgets,
    max_tags: int | None = None,
) -> TagFetch:
    """Collect every candidate tag for a topic, de-duplicated by status id."""
    limit = max_tags if max_tags is not None else budgets.mastodon_max_tags
    tags = candidate_tags(topic, aliases, limit=limit)
    combined = TagFetch()
    if not tags:
        combined.notes.append("no usable hashtag could be derived from the topic")
        return combined

    combined.notes.append(
        "hashtag-seeded: derived tags " + ", ".join(f"#{t}" for t in tags)
    )
    seen: set[str] = set()
    for tag in tags:
        fetch = collect_tag(
            tag,
            instances=budgets.mastodon_instances,
            window_start=window_start,
            window_end=window_end,
            budgets=budgets,
            topic=topic,
            aliases=aliases,
        )
        combined.queries.extend(fetch.queries)
        combined.notes.extend(fetch.notes)
        for post in fetch.posts:
            if post.identity in seen:
                continue
            seen.add(post.identity)
            combined.posts.append(post)
        if fetch.status is StageStatus.RATE_LIMITED:
            combined.status = StageStatus.RATE_LIMITED
        if len(combined.posts) >= budgets.max_posts_per_run:
            combined.truncated = True
            combined.notes.append("post cap reached")
            break

    if combined.posts and combined.status is not StageStatus.RATE_LIMITED:
        combined.status = StageStatus.OK
    combined.truncated = combined.truncated or len(combined.posts) >= budgets.max_posts_per_run
    return combined


__all__ = ["TagFetch", "candidate_tags", "collect", "collect_tag", "parse_statuses"]
