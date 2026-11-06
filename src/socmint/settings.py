"""Runtime configuration and collection budgets.

Budgets bound *processing effort*, never the user's requested date range. When a
cap is hit the run is marked partial and the truncation is surfaced.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DATA_DIR = Path(os.environ.get("SOCMINT_DATA_DIR", "data"))


@dataclass(frozen=True)
class Budgets:
    max_discovery_results: int = 20  # DDGS results per query
    max_sources_per_platform: int = 12  # channels / channels-equivalents followed
    max_posts_per_run: int = 500
    max_queries_per_platform: int = 6
    max_pages_per_source: int = 8  # pagination depth per channel
    acquisition_deadline_seconds: float = 900.0
    request_timeout_seconds: float = 25.0
    max_retries: int = 2
    retry_backoff_seconds: float = 2.0
    max_response_bytes: int = 5_000_000
    # Mastodon is federated, so "the platform" is a set of instances. These are
    # the large general-purpose ones whose tag timelines are publicly readable.
    # Instance hosts are also in the URL allowlist; adding one here without
    # adding it there will fail closed, which is the intended failure mode.
    mastodon_instances: tuple[str, ...] = (
        "mastodon.social",
        "mastodon.online",
        "fosstodon.org",
        "mstdn.social",
    )
    mastodon_max_tags: int = 4
    # GDELT DOC 2.0 returns at most 250 articles per query.
    gdelt_max_records: int = 250
    gdelt_max_queries: int = 3
    # GDELT enforces "one request every five seconds" by IP with no Retry-After.
    # On a shared corporate NAT that quota is already spent by other users, so
    # it is off by default and switched on where the network allows it.
    enable_gdelt: bool = False
    # Google News RSS is free, keyless and reliable from the same network.
    enable_google_news: bool = True
    # Reddit needs a free OAuth app, which is now gated behind a manual
    # Responsible Builder Policy review. The adapter works but is off by
    # default: X via Xpoz covers the same gap and is already working.
    enable_reddit: bool = False
    # X is metered. Off by default on top of CreditPolicy.allow_spend:
    # two explicit gates, because this is the only source that costs money.
    enable_x: bool = False
    google_news_max_items: int = 100
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )


@dataclass(frozen=True)
class AnalysisSettings:
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_revision: str | None = None  # pin in production
    # Threshold on *centred* cosine (see analysis.narratives._center). Raw e5
    # scores overlap between related and unrelated text; centring separates them.
    narrative_similarity: float = -0.15
    # A fixed threshold does not survive a change of corpus size: on a few dozen
    # posts -0.15 separates related from unrelated text, but at 500 posts it
    # merges everything into one cluster, which is not a narrative list. When a
    # single cluster holds more than this share, the threshold is tightened by
    # ``narrative_threshold_step`` and clustering is retried.
    narrative_max_cluster_share: float = 0.6
    narrative_threshold_step: float = 0.1
    narrative_threshold_steps: int = 6
    near_duplicate_jaccard: float = 0.82
    min_text_chars: int = 12
    coordination_min_episodes: int = 2
    coordination_time_window_seconds: float = 900.0  # 15 minutes
    # Centred-cosine threshold for "these two posts say the same thing".
    # Deliberately high: paraphrase-level overlap, not mere same-topic.
    coordination_semantic_similarity: float = 0.45
    coordination_max_pairs: int = 200_000
    baseline_shuffles: int = 5
    max_narratives: int = 60
    force_lexical: bool = False


@dataclass(frozen=True)
class CreditPolicy:
    """How much of a metered API this module is allowed to consume.

    The default is deliberately paranoid: spending is **off** unless someone
    turns it on, and even then a reserve is held back that no amount of testing
    can touch. A prototype that can silently drain its own demo budget is worse
    than one that refuses to run.
    """

    #: Total credits on the account (Xpoz free tier is a one-time 500).
    allowance: int = 500
    #: Held back for the demo. Testing stops here, not at zero.
    demo_reserve: int = 200
    #: Ceiling for a single run, so one runaway query cannot eat the rest.
    max_per_run: int = 40
    #: Credits one search call costs (Xpoz: 2 per Twitter query).
    cost_per_call: int = 2
    #: Off by default. Nothing spends until this is explicitly set.
    allow_spend: bool = False
    #: Results requested per query. The SDK will happily return hundreds; on a
    #: metered source the point is a sample that answers "is this being talked
    #: about", not a corpus. Ten is enough for that and keeps the run readable.
    max_results_per_query: int = 10
    #: Minimum gap between billable calls, measured from the last charge in the
    #: ledger so it holds across runs and restarts, not just within one process.
    #: Set to 0 to disable.
    min_seconds_between_calls: float = 30.0

    @property
    def testable(self) -> int:
        return max(0, self.allowance - self.demo_reserve)


@dataclass(frozen=True)
class XpozCredentials:
    """Xpoz API key, read from the environment.

    Xpoz is the only metered source. It is not required: without a key the
    platform reports ``unsupported`` and every other collector still runs.
    """

    api_key: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


def _xpoz_credentials_from_env() -> XpozCredentials:
    return XpozCredentials(api_key=os.environ.get("XPOZ_API_KEY") or None)


@dataclass(frozen=True)
class RedditCredentials:
    """Free Reddit OAuth app credentials, read from the environment.

    Reddit is the one source here that needs a key. It is free and read-only,
    but a key is still a secret, so it never appears in code, exports, or logs --
    only in the environment that runs the collector.
    """

    client_id: str | None = None
    client_secret: str | None = None
    user_agent: str = "socmint/0.1 (research prototype)"

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


def _reddit_credentials_from_env() -> RedditCredentials:
    return RedditCredentials(
        client_id=os.environ.get("REDDIT_CLIENT_ID") or None,
        client_secret=os.environ.get("REDDIT_CLIENT_SECRET") or None,
    )


@dataclass(frozen=True)
class AppConfig:
    budgets: Budgets = field(default_factory=Budgets)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    data_dir: Path = DEFAULT_DATA_DIR
    offline: bool = False
    reddit: RedditCredentials = field(default_factory=_reddit_credentials_from_env)
    xpoz: XpozCredentials = field(default_factory=_xpoz_credentials_from_env)
    credits: CreditPolicy = field(default_factory=CreditPolicy)

    def run_dir(self, run_id: str) -> Path:
        return self.data_dir / "runs" / run_id
