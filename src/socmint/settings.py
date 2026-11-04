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
class AppConfig:
    budgets: Budgets = field(default_factory=Budgets)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    data_dir: Path = DEFAULT_DATA_DIR
    offline: bool = False

    def run_dir(self, run_id: str) -> Path:
        return self.data_dir / "runs" / run_id
