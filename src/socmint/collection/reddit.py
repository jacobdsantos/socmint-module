"""Reddit collector over the free OAuth API.

Reddit is the one source here that needs a key. The key is free, read-only and
issued instantly to any account that registers a "script" app, but it is still a
credential, so it is read from the environment and never written to code, logs
or exports.

Why OAuth and not the public JSON: ``www.reddit.com/r/<sub>/new.json`` answers
this network with a 403 bot-block page, while the token endpoint at
``www.reddit.com/api/v1/access_token`` answers with Reddit's own 401 JSON. The
protocol is reachable; only the unauthenticated surface is blocked.

Known limits, surfaced rather than hidden:
* Reddit search has no absolute date filter. The collector sorts by newest and
  pages back until it passes the window start, so a very old window with heavy
  recent activity can exhaust the page budget before reaching it.
* ``limit=100`` is the per-page maximum; deeper history needs more pages.
* Comment bodies are not collected -- posts only.
* Without credentials the platform reports ``unsupported``, not ``empty``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlencode

from ..models import (
    Engagement,
    EvidenceQuality,
    Platform,
    Post,
    Precision,
    QueryLog,
    StageStatus,
)
from ..security import safe_request
from ..settings import Budgets, RedditCredentials
from ..text import topic_match_terms

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_SEARCH_URL = "https://oauth.reddit.com/search"
_HOSTS = frozenset({"www.reddit.com", "oauth.reddit.com"})
_PAGE_LIMIT = 100
# Tokens last ~24h. Cache per client_id so repeated runs in one process do not
# re-authenticate; the expiry margin avoids using a token that dies mid-request.
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_TOKEN_MARGIN_SECONDS = 120.0


@dataclass
class RedditFetch:
    posts: list[Post] = field(default_factory=list)
    queries: list[QueryLog] = field(default_factory=list)
    status: StageStatus = StageStatus.EMPTY
    notes: list[str] = field(default_factory=list)
    truncated: bool = False


def get_token(credentials: RedditCredentials, budgets: Budgets) -> tuple[str | None, str | None]:
    """Return (token, error). Cached until shortly before it expires."""
    if not credentials.configured:
        return None, "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are not set"

    key = credentials.client_id or ""
    cached = _TOKEN_CACHE.get(key)
    if cached and cached[1] > time.time() + _TOKEN_MARGIN_SECONDS:
        return cached[0], None

    try:
        response = safe_request(
            "POST",
            _TOKEN_URL,
            timeout=budgets.request_timeout_seconds,
            max_bytes=budgets.max_response_bytes,
            user_agent=credentials.user_agent,
            allowed_hosts=_HOSTS,
            data={"grant_type": "client_credentials"},
            auth=(credentials.client_id or "", credentials.client_secret or ""),
        )
    except Exception as exc:  # noqa: BLE001 - a source failure must not abort the run
        return None, f"token request failed ({type(exc).__name__}: {exc})"

    if response.status_code != 200:
        return None, f"token endpoint returned HTTP {response.status_code}"

    try:
        payload = response.json()
    except ValueError:
        return None, "token endpoint returned a non-JSON body"
    if not isinstance(payload, dict):
        return None, "token endpoint returned an unexpected payload"
    token = payload.get("access_token")
    if not token:
        return None, f"token endpoint returned no access_token ({payload.get('error')})"

    expires_in = payload.get("expires_in")
    ttl = float(expires_in) if isinstance(expires_in, (int, float)) else 3600.0
    _TOKEN_CACHE[key] = (str(token), time.time() + ttl)
    return str(token), None


def _parse_created(value: object) -> tuple[datetime | None, Precision]:
    if not isinstance(value, (int, float)):
        return None, Precision.UNKNOWN
    return datetime.fromtimestamp(float(value), tz=UTC), Precision.SECOND


def parse_listing(
    payload: dict,
    *,
    topic: str | None = None,
    aliases: list[str] | None = None,
) -> list[Post]:
    """Map one listing page to posts. Pure function -- testable offline."""
    data = payload.get("data") if isinstance(payload, dict) else None
    children = data.get("children") if isinstance(data, dict) else None
    if not isinstance(children, list):
        return []

    posts: list[Post] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        item = child.get("data")
        if not isinstance(item, dict):
            continue
        post_id = str(item.get("id") or "")
        permalink = str(item.get("permalink") or "")
        if not post_id or not permalink:
            continue

        title = str(item.get("title") or "").strip()
        selftext = str(item.get("selftext") or "").strip()
        published, precision = _parse_created(item.get("created_utc"))
        author = str(item.get("author") or "") or None
        subreddit = str(item.get("subreddit") or "") or None
        score = item.get("score")
        comments = item.get("num_comments")

        post = Post(
            platform=Platform.REDDIT,
            post_id=post_id,
            url=f"https://www.reddit.com{permalink}",
            author_id=author,
            author_name=author,
            title=title or None,
            text=selftext,
            published_at=published,
            precision=precision,
            published_source="created_utc" if published else None,
            engagement=Engagement(
                likes=score if isinstance(score, int) else None,
                comments=comments if isinstance(comments, int) else None,
                missing_reason=(
                    None if isinstance(score, int) else "score not returned"
                ),
            ),
            # A link post's destination is what a human would click through to;
            # a self post has none, so the list stays empty rather than holding
            # the permalink we already store in ``url``.
            referenced_urls=(
                [str(item["url_overridden_by_dest"])]
                if item.get("url_overridden_by_dest")
                else []
            ),
            evidence_quality=(
                EvidenceQuality.ORIGINAL if published else EvidenceQuality.PARTIAL
            ),
            provenance=f"reddit search (r/{subreddit})" if subreddit else "reddit search",
        )
        if topic:
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
    credentials: RedditCredentials,
) -> RedditFetch:
    """Search all of Reddit for the topic, newest first, back to the window."""
    result = RedditFetch()
    if not topic.strip():
        result.notes.append("no topic to search for")
        return result

    token, error = get_token(credentials, budgets)
    if token is None:
        # Not configured is not the same as empty, and must not look like it.
        result.status = StageStatus.UNSUPPORTED
        result.notes.append(f"Reddit unavailable: {error}")
        return result

    after: str | None = None
    reached_older = False
    pages = max(1, budgets.max_pages_per_source)

    for _page in range(pages):
        params = {
            "q": topic,
            "sort": "new",
            "limit": str(_PAGE_LIMIT),
            "type": "link",
            "raw_json": "1",
        }
        if after:
            params["after"] = after
        url = f"{_SEARCH_URL}?{urlencode(params)}"
        log = QueryLog(platform=Platform.REDDIT, query=url, kind="search")

        try:
            response = safe_request(
                "GET",
                url,
                timeout=budgets.request_timeout_seconds,
                max_bytes=budgets.max_response_bytes,
                user_agent=credentials.user_agent,
                allowed_hosts=_HOSTS,
                headers={"Authorization": f"bearer {token}"},
            )
        except Exception as exc:  # noqa: BLE001 - a source failure must not abort the run
            log.status = StageStatus.FAILED
            log.detail = f"{type(exc).__name__}: {exc}"
            result.queries.append(log)
            result.notes.append(f"Reddit search failed ({exc})")
            result.status = StageStatus.FAILED
            return result

        if response.status_code == 200:
            log.status = StageStatus.OK
        elif response.status_code == 401:
            log.status = StageStatus.BLOCKED_AUTH
        elif response.status_code == 429:
            log.status = StageStatus.RATE_LIMITED
        else:
            log.status = StageStatus.FAILED
        log.detail = f"HTTP {response.status_code}"
        result.queries.append(log)

        if response.status_code != 200:
            result.status = log.status
            result.notes.append(f"Reddit search returned HTTP {response.status_code}")
            return result

        try:
            payload = response.json()
        except ValueError:
            result.notes.append("Reddit returned a non-JSON body")
            result.status = StageStatus.FAILED
            return result

        page_posts = parse_listing(payload, topic=topic, aliases=aliases)
        if not page_posts:
            break

        oldest = min((p.published_at for p in page_posts if p.published_at), default=None)
        for post in page_posts:
            if not post.topic_match:
                continue
            if post.published_at is None:
                continue
            if window_start <= post.published_at < window_end:
                result.posts.append(post)

        if oldest and oldest < window_start:
            reached_older = True
            break
        if len(result.posts) >= budgets.max_posts_per_run:
            result.truncated = True
            result.notes.append("post cap reached")
            break

        listing = payload.get("data") if isinstance(payload, dict) else None
        after = listing.get("after") if isinstance(listing, dict) else None
        if not after:
            break
        time.sleep(0.5)  # Reddit allows 100 requests/minute; stay well under

    result.status = StageStatus.OK if result.posts else StageStatus.EMPTY
    if reached_older:
        result.notes.append("reached posts older than window start")
    if not result.posts:
        result.notes.append("Reddit returned no in-window post for this topic")
    return result


__all__ = ["RedditFetch", "collect", "get_token", "parse_listing"]
