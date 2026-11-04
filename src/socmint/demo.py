"""Synthetic scenario runner.

Real collection on a two-week window may legitimately contain no repeated
cross-account coordination, which leaves the detector undemonstrated. This module
loads a clearly-labelled fictional dataset so the behaviour can be shown end to
end.

Everything it produces is tagged ``synthetic`` in the run config and manifest. It
must never be mixed into a live evidence set.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from importlib import resources

from .models import (
    TLP,
    CaseTemplate,
    DateRange,
    Engagement,
    EvidenceQuality,
    Platform,
    PlatformReport,
    Post,
    Precision,
    Run,
    StageStatus,
)
from .pipeline import analyse, new_run_id
from .settings import AppConfig
from .store import Store

FIXTURE_NAME = "coordinated_demo.json"


def load_fixture() -> dict:
    path = resources.files("socmint.fixtures").joinpath(FIXTURE_NAME)
    return json.loads(path.read_text(encoding="utf-8"))


def _build_posts(fixture: dict) -> tuple[list[Post], DateRange]:
    start = datetime.fromisoformat(fixture["window_start"])
    end = datetime.fromisoformat(fixture["window_end"])
    posts: list[Post] = []
    for item in fixture["posts"]:
        published = start + timedelta(minutes=item["minutes"])
        platform = Platform(item["platform"])
        if platform is Platform.YOUTUBE:
            url = f"https://www.youtube.com/watch?v={item['id']}"
            text = item.get("text", "")
            title = item.get("title")
        else:
            url = f"https://t.me/{item['author']}/{item['id']}"
            text = item.get("text", "")
            title = None
        posts.append(
            Post(
                platform=platform,
                post_id=str(item["id"]),
                url=url,
                author_id=item["author"],
                author_name=item.get("name", item["author"]),
                text=text,
                title=title,
                published_at=published,
                precision=Precision.SECOND,
                published_source="fixture",
                engagement=Engagement(views=100, likes=5, missing_reason=None),
                evidence_quality=EvidenceQuality.ORIGINAL,
                provenance="synthetic fixture",
                topic_match=True,
            )
        )
    return posts, DateRange(start=start, end=end)


def run_demo(
    *,
    config: AppConfig,
    store: Store | None = None,
) -> tuple[Run, list[Post], object]:
    fixture = load_fixture()
    posts, window = _build_posts(fixture)
    run = Run(
        run_id=f"DEMO-{new_run_id()}",
        topic=fixture["topic"],
        case_template=CaseTemplate.IMPERSONATION_DEEPFAKE,
        tlp=TLP.AMBER,
        window=window,
        config={
            "source": "synthetic_fixture",
            "notice": fixture["notice"],
        },
    )
    if store is not None:
        store.save_run(run)
        store.save_posts(run.run_id, posts)

    bundle = analyse(
        posts,
        topic=run.topic,
        config=config,
        case_template=CaseTemplate.IMPERSONATION_DEEPFAKE,
        tlp=TLP.AMBER,
        include_claims=True,
    )

    # Populate platform reports so the coverage tab is coherent. These counts
    # describe the fixture, not any platform.
    reports: list[PlatformReport] = []
    for platform in (Platform.YOUTUBE, Platform.TELEGRAM):
        platform_posts = [p for p in posts if p.platform == platform]
        reports.append(
            PlatformReport(
                platform=platform,
                discovery=StageStatus.OK,
                retrieval=StageStatus.OK,
                analysis=StageStatus.OK,
                sources_found=len({p.author_id for p in platform_posts}),
                posts_retrieved=len(platform_posts),
                posts_in_range=len(platform_posts),
                notes=["SYNTHETIC FIXTURE — not collected from the platform"],
            )
        )
    run.platforms = reports

    run.finished_at = datetime.now(window.start.tzinfo)
    run.status = StageStatus.OK
    if store is not None:
        store.save_analysis(run.run_id, bundle)
        store.save_run(run)
    return run, posts, bundle
