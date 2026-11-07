"""X/Twitter collector through the Xpoz API.

This is the only **metered** source in the module. Xpoz's free tier is a
one-time grant of 500 credits at 2 credits per Twitter query, and credits spent
while testing are not replaced. Everything here is therefore subordinate to
``spend.CreditLedger``: the collector asks permission, charges before it calls,
and refunds if the call fails.

Design consequences:

* **Disabled unless asked for.** ``CreditPolicy.allow_spend`` defaults to false,
  so a fresh clone cannot spend money by running the tests or the demo.
* **Unavailable is not empty.** No key, no package, or a refused spend all report
  ``unsupported`` / ``blocked_policy`` with the reason, never an empty result.
* **One billable call per run.** ``ResponseType.FAST`` returns a single page, so
  the cost of a run is known before it starts rather than discovered afterwards.
* **Capped per query, and rate-limited.** A query asks for at most
  ``CreditPolicy.max_results_per_query`` posts — a sample that answers "is this
  being talked about", not a corpus. A ``min_seconds_between_calls`` cooldown,
  measured from the ledger's last charge, stops a loop from hammering the API;
  because it reads the ledger it survives restarts rather than resetting.
* **No SSRF surface.** The SDK talks to a fixed endpoint; this module never
  hands it a user-supplied URL.

The SDK is an optional extra (``pip install socmint[xpoz]``). Import is lazy so
the module works without it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

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
from ..prose import plural
from ..settings import Budgets, XpozCredentials
from ..spend import CreditLedger
from ..text import topic_match_terms

logger = logging.getLogger(__name__)

_VIDEO_SUFFIXES = (".mp4", ".m3u8", ".mov")

#: Fields requested from the API. Verified against xpoz 0.10.1: omitting this
#: returns a default set without ``created_at``, so every post lands at day
#: precision and cannot support minute-level coordination. Requesting an
#: unsupported name (``impression_count`` did) fails the whole call with HTTP
#: 500, so this list is the tested intersection, not everything we would like.
_FIELDS = [
    "id",
    "text",
    "created_at",
    "author_username",
    "author_id",
    "lang",
    "like_count",
    "retweet_count",
    "reply_count",
    "urls",
    "is_retweet",
]


@dataclass
class XpozFetch:
    posts: list[Post] = field(default_factory=list)
    queries: list[QueryLog] = field(default_factory=list)
    status: StageStatus = StageStatus.EMPTY
    notes: list[str] = field(default_factory=list)
    truncated: bool = False
    credits_spent: int = 0


def _import_sdk() -> tuple[Any | None, str | None]:
    """Import the Xpoz SDK lazily. Returns (module, error)."""
    try:
        import xpoz  # noqa: PLC0415 - optional dependency, imported on demand
    except ImportError:
        return None, "xpoz is not installed (pip install 'socmint[xpoz]')"
    return xpoz, None


def _parse_created(post: Any) -> tuple[datetime | None, Precision]:
    raw = getattr(post, "created_at", None)
    if isinstance(raw, str) and raw.strip():
        try:
            dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC), Precision.SECOND
        except ValueError:
            pass
    date_only = getattr(post, "created_at_date", None)
    if isinstance(date_only, str) and date_only.strip():
        try:
            dt = datetime.strptime(date_only.strip()[:10], "%Y-%m-%d").replace(tzinfo=UTC)
            return dt, Precision.DAY
        except ValueError:
            return None, Precision.UNKNOWN
    return None, Precision.UNKNOWN


def _media(post: Any) -> list[MediaRef]:
    media: list[MediaRef] = []
    for url in getattr(post, "media_urls", None) or []:
        if not isinstance(url, str):
            continue
        kind = MediaKind.VIDEO if url.lower().endswith(_VIDEO_SUFFIXES) else MediaKind.PHOTO
        media.append(MediaRef(kind=kind, url=url))
    return media


def _permalink(post: Any) -> str:
    post_id = getattr(post, "id", None)
    handle = getattr(post, "author_username", None)
    if post_id and handle:
        return f"https://x.com/{handle}/status/{post_id}"
    if post_id:
        return f"https://x.com/i/web/status/{post_id}"
    return ""


def to_post(post: Any, *, topic: str | None = None, aliases: list[str] | None = None) -> Post | None:
    """Map one SDK ``TwitterPost`` to our contract. Every field is optional."""
    post_id = getattr(post, "id", None)
    text = getattr(post, "text", None) or ""
    if not post_id:
        return None

    published, precision = _parse_created(post)
    handle = getattr(post, "author_username", None)
    author_id = getattr(post, "author_id", None) or handle

    def _count(name: str) -> int | None:
        value = getattr(post, name, None)
        return value if isinstance(value, int) else None

    mapped = Post(
        platform=Platform.X,
        post_id=str(post_id),
        url=_permalink(post),
        author_id=str(author_id) if author_id else None,
        author_name=str(handle) if handle else None,
        text=text,
        language=getattr(post, "lang", None),
        published_at=published,
        precision=precision,
        published_source="created_at" if published else None,
        engagement=Engagement(
            # impression_count is not a valid field on this endpoint; the API
            # rejects the whole call if it is requested, so views stay absent.
            views=None,
            likes=_count("like_count"),
            comments=_count("reply_count"),
            shares=_count("retweet_count"),
            missing_reason=(
                None if _count("like_count") is not None else "counts not returned"
            ),
        ),
        media=_media(post),
        is_repost=bool(getattr(post, "is_retweet", False)),
        reply_to=(
            str(post.reply_to_tweet_id) if getattr(post, "reply_to_tweet_id", None) else None
        ),
        referenced_urls=[u for u in (getattr(post, "urls", None) or []) if isinstance(u, str)],
        evidence_quality=(
            EvidenceQuality.ORIGINAL if text and published else EvidenceQuality.PARTIAL
        ),
        provenance="xpoz twitter search",
    )
    if topic:
        matched = topic_match_terms(
            " ".join(filter(None, [mapped.title, mapped.text])), topic, aliases or []
        )
        mapped.topic_terms_matched = matched
        mapped.topic_match = bool(matched)
    return mapped


def collect(
    topic: str,
    *,
    aliases: list[str] | None = None,
    window_start: datetime,
    window_end: datetime,
    budgets: Budgets,
    credentials: XpozCredentials,
    ledger: CreditLedger,
    run_id: str | None = None,
    client_factory: Any | None = None,
) -> XpozFetch:
    """Search X for the topic, subject to the credit policy.

    ``client_factory`` exists so tests can inject a fake SDK without the real one
    installed or a real key present.
    """
    result = XpozFetch()
    if not topic.strip():
        result.notes.append("no topic to search for")
        return result

    if client_factory is None:
        sdk, error = _import_sdk()
        if sdk is None:
            result.status = StageStatus.UNSUPPORTED
            result.notes.append(f"X unavailable: {error}")
            return result

        if not credentials.configured:
            result.status = StageStatus.UNSUPPORTED
            result.notes.append("X unavailable: XPOZ_API_KEY is not set")
            return result

        def client_factory() -> Any:  # type: ignore[misc]
            # check_update=False keeps the run deterministic and avoids a second
            # outbound call whose result we do not use.
            return sdk.XpozClient(credentials.api_key, check_update=False)

    # The ledger owns the policy; passing a second copy invites them to
    # diverge, and the one that decides would be the one nobody set.
    cost = ledger.policy.cost_per_call
    decision = ledger.check(cost)
    if not decision.allowed:
        result.status = StageStatus.BLOCKED_POLICY
        result.notes.append(f"X skipped: {decision.reason}")
        return result

    # Rate limit before the charge, so a refused call costs nothing. The gap is
    # measured from the ledger's last charge, so it survives restarts and is not
    # reset by re-running the command.
    waiting = ledger.cooldown_remaining()
    if waiting > 0:
        result.status = StageStatus.BLOCKED_POLICY
        result.notes.append(
            f"X skipped: {waiting:.0f}s cooldown remaining "
            f"(min_seconds_between_calls={ledger.policy.min_seconds_between_calls:g})"
        )
        return result

    # A sample, not a corpus: the per-query cap is the smaller of the run's post
    # budget and the credit policy's own ceiling.
    limit = max(1, min(budgets.max_posts_per_run, ledger.policy.max_results_per_query))

    query = topic.strip()
    log = QueryLog(
        platform=Platform.X,
        query=f"xpoz twitter.search_posts({query!r}, {window_start.date()}..{window_end.date()})",
        kind="search",
    )

    ledger.charge(cost, run_id=run_id, note=f"x search: {query}")
    result.credits_spent = cost

    client = None
    try:
        client = client_factory()
        page = client.twitter.search_posts(
            query,
            start_date=window_start.date().isoformat(),
            end_date=window_end.date().isoformat(),
            limit=limit,
            fields=_FIELDS,
        )
        raw_posts = list(getattr(page, "data", None) or [])
    except Exception as exc:  # noqa: BLE001 - a paid call failing must not abort the run
        ledger.refund(cost, run_id=run_id, note=f"x search failed: {type(exc).__name__}")
        result.credits_spent = 0
        log.status = StageStatus.FAILED
        log.detail = f"{type(exc).__name__}: {exc}"
        result.queries.append(log)
        result.notes.append(f"X search failed and the charge was refunded ({exc})")
        result.status = StageStatus.FAILED
        return result
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - closing must never mask the result
                logger.debug("xpoz client close failed", exc_info=True)

    log.status = StageStatus.OK
    log.detail = plural(len(raw_posts), "result")
    result.queries.append(log)

    # The API can return the same post twice in one page; the module's
    # identity contract assumes one record per post id.
    seen: set[str] = set()
    for raw in raw_posts:
        mapped = to_post(raw, topic=topic, aliases=aliases)
        if mapped is None or not mapped.topic_match:
            continue
        if mapped.published_at is None:
            continue
        if not (window_start <= mapped.published_at < window_end):
            continue
        if mapped.identity in seen:
            continue
        seen.add(mapped.identity)
        result.posts.append(mapped)

    # ``limit`` is a request, not a guarantee: the API has returned more than
    # asked before, and an off-topic-heavy page would otherwise let a run exceed
    # its own stated ceiling.
    if len(result.posts) > limit:
        result.posts = result.posts[:limit]
        result.truncated = True
    else:
        result.truncated = len(result.posts) >= limit
    result.status = StageStatus.OK if result.posts else StageStatus.EMPTY
    if not result.posts:
        result.notes.append("X returned no in-window post for this query")
    result.notes.append(f"capped at {plural(limit, 'result')} per query")
    return result


__all__ = ["XpozFetch", "collect", "to_post"]
