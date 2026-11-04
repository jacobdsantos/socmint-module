"""Public Telegram channel collector.

Reads the ordinary public web preview (``t.me/s/<channel>``), which exposes
posts without an account. This is channel-seeded retrieval: the channel must be
discovered first. Keyword search inside a channel is supported by the same
surface via ``?q=``.

Known limits, surfaced to the caller rather than hidden:
* Only channels that have the public web preview enabled are readable.
* The preview paginates by message id; deep history may be unreachable.
* View counts are as-displayed snapshots, not historical engagement.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup, Tag

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

_VIEWS_RE = re.compile(r"([\d.,]+)\s*([KMB])?", re.IGNORECASE)
_MULT = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


@dataclass
class ChannelFetch:
    posts: list[Post] = field(default_factory=list)
    queries: list[QueryLog] = field(default_factory=list)
    status: StageStatus = StageStatus.EMPTY
    notes: list[str] = field(default_factory=list)
    truncated: bool = False
    title: str | None = None


def _parse_int(text: str | None) -> int | None:
    if not text:
        return None
    match = _VIEWS_RE.search(text.replace(",", ""))
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    if match.group(2):
        value *= _MULT.get(match.group(2).upper(), 1)
    return int(value)


def _parse_time(node: Tag | None) -> tuple[datetime | None, Precision]:
    if node is None:
        return None, Precision.UNKNOWN
    raw = node.get("datetime")
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


def _media(post_node: Tag) -> list[MediaRef]:
    media: list[MediaRef] = []
    for photo in post_node.select("a.tgme_widget_message_photo_wrap"):
        style = photo.get("style") or ""
        match = re.search(r"url\(['\"]?(.*?)['\"]?\)", style)
        if match:
            media.append(MediaRef(kind=MediaKind.PHOTO, url=match.group(1)))
    for video in post_node.select("a.tgme_widget_message_video_player, video"):
        href = video.get("href") or video.get("src")
        if href:
            media.append(MediaRef(kind=MediaKind.VIDEO, url=href))
    return media


def parse_channel_page(html: str, channel: str) -> tuple[list[Post], str | None]:
    """Parse one public channel page. Pure function -- unit-testable offline."""
    soup = BeautifulSoup(html, "lxml")
    title_node = soup.select_one("div.tgme_channel_info_header_title")
    channel_title = title_node.get_text(" ", strip=True) if title_node else None

    posts: list[Post] = []
    for node in soup.select("div.tgme_widget_message"):
        data_post = node.get("data-post")
        if not data_post or "/" not in data_post:
            continue
        _, msg_id = data_post.split("/", 1)
        text_node = node.select_one("div.tgme_widget_message_text")
        text = text_node.get_text(" ", strip=True) if text_node else ""

        time_node = node.select_one("time.time")
        published, precision = _parse_time(time_node)

        views_node = node.select_one("span.tgme_widget_message_views")
        views = _parse_int(views_node.get_text(strip=True) if views_node else None)

        # Explicit forwarded-from marker is a source-stated relationship.
        fwd = node.select_one("a.tgme_widget_message_forwarded_from_name")
        is_repost = fwd is not None

        url = f"https://t.me/{data_post}"
        quality = (
            EvidenceQuality.ORIGINAL
            if text and published
            else EvidenceQuality.PARTIAL
        )
        posts.append(
            Post(
                platform=Platform.TELEGRAM,
                post_id=msg_id,
                url=url,
                author_id=channel,
                author_name=channel_title or channel,
                text=text,
                published_at=published,
                precision=precision,
                published_source="time.time@datetime" if published else None,
                engagement=Engagement(views=views, missing_reason=None if views is not None else "not displayed"),
                media=_media(node),
                is_repost=is_repost,
                referenced_urls=re.findall(r"https?://\S+", text),
                evidence_quality=quality,
                provenance=f"t.me/s/{channel}",
            )
        )
    return posts, channel_title


def collect_channel(
    channel: str,
    *,
    window_start: datetime,
    window_end: datetime,
    budgets: Budgets,
    topic: str | None = None,
    aliases: list[str] | None = None,
    query: str | None = None,
    max_pages: int | None = None,
    client: httpx.Client | None = None,
) -> ChannelFetch:
    """Page backwards through a public channel, keeping posts inside the window.

    When ``terms`` is supplied, each post is marked for literal topic relevance
    and a channel whose first page contains no match is abandoned. Without this,
    a discovered channel that merely *mentions* a topic in passing can burn the
    whole post budget on unrelated automated feeds.
    """
    result = ChannelFetch()
    pages = max_pages if max_pages is not None else budgets.max_pages_per_source
    before: int | None = None
    oldest_seen: datetime | None = None
    stop = False
    topic_matches_seen = 0

    for page in range(pages):
        params = []
        if query:
            params.append(f"q={quote(query)}")
        if before:
            params.append(f"before={before}")
        url = f"https://t.me/s/{channel}"
        if params:
            url += "?" + "&".join(params)

        log = QueryLog(platform=Platform.TELEGRAM, query=url, kind="page")
        try:
            response = safe_get(
                url,
                timeout=budgets.request_timeout_seconds,
                max_bytes=budgets.max_response_bytes,
                user_agent=budgets.user_agent,
            )
        except Exception as exc:  # noqa: BLE001 - one bad channel must not abort the run
            # Covers httpx transport errors, unsafe URLs, and unexpected parser
            # input. The channel is marked failed and the run continues.
            log.status = StageStatus.FAILED
            log.detail = f"{type(exc).__name__}: {exc}"
            result.queries.append(log)
            result.notes.append(f"{channel}: page {page + 1} failed ({exc})")
            break

        if response.status_code == 200:
            log.status = StageStatus.OK
        elif response.status_code == 429:
            log.status = StageStatus.RATE_LIMITED
        else:
            log.status = StageStatus.FAILED
        log.detail = f"HTTP {response.status_code}"
        result.queries.append(log)

        if response.status_code != 200:
            if response.status_code == 429:
                result.status = StageStatus.RATE_LIMITED
            break

        posts, title = parse_channel_page(response.text, channel)
        if title and not result.title:
            result.title = title
        if not posts:
            result.notes.append(f"{channel}: no public posts on page {page + 1}")
            break

        # Literal topic relevance. A discovered channel can be topically adjacent
        # while most of its traffic is unrelated (e.g. a global alert feed).
        if topic:
            for post in posts:
                haystack = " ".join(filter(None, [post.title, post.text]))
                matched = topic_match_terms(haystack, topic, aliases or [])
                post.topic_match = bool(matched)
                post.topic_terms_matched = matched

            # Abort on *topic* relevance, not on collected volume: a channel can
            # be about the topic while its matching posts fall outside the
            # requested window, which is a coverage gap, not an irrelevant source.
            topic_matches = sum(1 for p in posts if p.topic_match)
            topic_matches_seen += topic_matches
            if page >= 1 and topic_matches_seen == 0:
                result.notes.append(
                    f"{channel}: no post matched the topic terms in the first "
                    f"{page + 1} pages; channel skipped"
                )
                result.status = StageStatus.EMPTY
                break

        page_oldest = min((p.published_at for p in posts if p.published_at), default=None)
        new_count = 0
        for post in posts:
            if topic and not post.topic_match:
                continue
            if post.published_at is None:
                # Undated posts cannot be placed in the window; keep as leads.
                if post.text:
                    result.posts.append(post)
                    new_count += 1
                continue
            if window_start <= post.published_at < window_end:
                result.posts.append(post)
                new_count += 1

        oldest_seen = page_oldest or oldest_seen
        min_id = min((int(p.post_id) for p in posts if p.post_id.isdigit()), default=None)
        if min_id is None:
            break
        before = min_id

        # Stop once the page is entirely older than the window.
        if page_oldest and page_oldest < window_start:
            stop = True
        if new_count == 0 and page_oldest is None:
            break
        if stop:
            break
        time.sleep(0.4)  # be gentle with the public surface

    if result.posts:
        result.status = StageStatus.PARTIAL if stop else StageStatus.OK
        if stop:
            result.notes.append(f"{channel}: reached posts older than window start")
    elif result.queries and any(q.status == StageStatus.RATE_LIMITED for q in result.queries):
        result.status = StageStatus.RATE_LIMITED
    else:
        result.status = StageStatus.EMPTY
    result.truncated = len(result.posts) >= budgets.max_posts_per_run
    return result
