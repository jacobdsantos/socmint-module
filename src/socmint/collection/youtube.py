"""YouTube collector built on yt-dlp (anonymous, metadata only).

``ytsearchN:<query>`` gives topic discovery plus retrieval in one step. We never
download media: only metadata, thumbnails and links are kept.

Important honesty constraint: a keyword search returns what the platform's
search backend yields for that query. Filtering those results by upload date
does not turn the result into an exhaustive historical archive of the window.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

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
from ..settings import Budgets
from ..text import topic_match_terms

logger = logging.getLogger(__name__)


@dataclass
class YouTubeFetch:
    posts: list[Post] = field(default_factory=list)
    queries: list[QueryLog] = field(default_factory=list)
    status: StageStatus = StageStatus.EMPTY
    notes: list[str] = field(default_factory=list)
    truncated: bool = False


def _upload_datetime(entry: dict) -> tuple[datetime | None, Precision]:
    ts = entry.get("timestamp") or entry.get("release_timestamp")
    if isinstance(ts, (int, float)) and ts > 0:
        return datetime.fromtimestamp(ts, tz=UTC), Precision.SECOND
    raw = entry.get("upload_date")
    if isinstance(raw, str) and len(raw) == 8 and raw.isdigit():
        try:
            dt = datetime.strptime(raw, "%Y%m%d").replace(tzinfo=UTC)
            return dt, Precision.DAY
        except ValueError:
            pass
    return None, Precision.UNKNOWN


def entry_to_post(entry: dict) -> Post | None:
    """Convert a yt-dlp info dict to a Post. Pure function -- unit-testable."""
    video_id = entry.get("id")
    if not video_id:
        return None
    published, precision = _upload_datetime(entry)
    title = entry.get("title")
    description = entry.get("description") or ""
    channel_id = entry.get("channel_id") or entry.get("uploader_id")
    channel = entry.get("channel") or entry.get("uploader")

    media: list[MediaRef] = []
    thumb = entry.get("thumbnail")
    if thumb:
        media.append(MediaRef(kind=MediaKind.THUMBNAIL, url=thumb))
    if entry.get("webpage_url"):
        media.append(MediaRef(kind=MediaKind.VIDEO, url=entry["webpage_url"]))

    views = entry.get("view_count")
    likes = entry.get("like_count")
    comments = entry.get("comment_count")

    return Post(
        platform=Platform.YOUTUBE,
        post_id=str(video_id),
        url=entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
        author_id=channel_id,
        author_name=channel,
        text=description,
        title=title,
        language=entry.get("language"),
        published_at=published,
        precision=precision,
        published_source="timestamp/upload_date" if published else None,
        engagement=Engagement(
            views=views if isinstance(views, int) else None,
            likes=likes if isinstance(likes, int) else None,
            comments=comments if isinstance(comments, int) else None,
            missing_reason=None if isinstance(views, int) else "not returned",
        ),
        media=media,
        referenced_urls=[],
        evidence_quality=EvidenceQuality.ORIGINAL if published else EvidenceQuality.PARTIAL,
        provenance="yt-dlp ytsearch",
    )


def search(
    query: str,
    *,
    limit: int,
    budgets: Budgets,
    topic: str | None = None,
    aliases: list[str] | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> YouTubeFetch:
    """Run one keyword search and return in-window posts."""
    try:
        import yt_dlp
    except ImportError:  # pragma: no cover
        return YouTubeFetch(
            status=StageStatus.UNSUPPORTED,
            notes=["yt-dlp is not installed; YouTube collection unavailable"],
        )

    result = YouTubeFetch()
    log = QueryLog(platform=Platform.YOUTUBE, query=query, kind="search")
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": True,
        "socket_timeout": budgets.request_timeout_seconds,
        "playlistend": limit,
        "retries": budgets.max_retries,
        "extract_flat": False,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    except Exception as exc:  # yt-dlp raises a wide variety
        log.status = StageStatus.FAILED
        log.detail = f"{type(exc).__name__}: {exc}"
        result.queries.append(log)
        result.notes.append(f"search failed for {query!r}: {exc}")
        result.status = StageStatus.FAILED
        return result

    entries = (info or {}).get("entries") or []
    log.results = len(entries)
    log.finished_at = log.started_at
    result.queries.append(log)

    for entry in entries:
        post = entry_to_post(entry or {})
        if post is None:
            continue
        if (
            window_start
            and window_end
            and post.published_at
            and not (window_start <= post.published_at < window_end)
        ):
            continue
        # Relevance comes from the query itself: the platform returned these for
        # a topic query. Literal term hits are recorded for evidence, but a
        # result is not discarded merely because the description omits the term.
        haystack = " ".join(filter(None, [post.title, post.text]))
        post.topic_terms_matched = topic_match_terms(haystack, topic or "", aliases or [])
        post.topic_match = True
        result.posts.append(post)

    if result.posts:
        result.status = StageStatus.OK
    elif entries:
        result.status = StageStatus.EMPTY
        result.notes.append(
            f"{query!r}: {len(entries)} results returned but none had a verified "
            "upload date inside the requested window"
        )
    else:
        result.status = StageStatus.EMPTY
    return result


def retrieve_video(url_or_id: str, *, budgets: Budgets) -> Post | None:
    """Retrieve metadata for a known video (used to enrich discovered leads)."""
    try:
        import yt_dlp
    except ImportError:  # pragma: no cover
        return None
    target = url_or_id
    if "://" not in target:
        target = f"https://www.youtube.com/watch?v={target}"
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": budgets.request_timeout_seconds,
        "retries": budgets.max_retries,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(target, download=False)
    except Exception as exc:
        logger.warning("video retrieval failed for %s: %s", target, exc)
        return None
    return entry_to_post(info or {})
