"""Run orchestration: collect -> normalise -> analyse -> persist.

The pipeline never silently downgrades a failure into an empty success. Every
platform carries its own status, and a run can legitimately end ``partial``.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict

from .analysis import assessment as assess_mod
from .analysis import claims as claims_mod
from .analysis import coordination as coord_mod
from .analysis import grading as grading_mod
from .analysis import indicators as indicator_mod
from .analysis import narratives as narr_mod
from .analysis import propagation as prop_mod
from .analysis import threat as threat_mod
from .collection import discovery, gdelt, mastodon, news_rss, reddit, telegram, youtube
from .models import (
    TLP,
    AnalysisBundle,
    CaseTemplate,
    Platform,
    PlatformReport,
    Post,
    Run,
    StageStatus,
    ThreatFraming,
    utcnow,
)
from .presets import resolve as resolve_preset
from .settings import AppConfig
from .store import Store
from .text import detect_language

logger = logging.getLogger(__name__)


def new_run_id() -> str:
    return f"{utcnow().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _dedupe(posts: list[Post]) -> list[Post]:
    """Collapse repeated observations of the same post, keeping the richest."""
    best: dict[str, Post] = {}
    for post in posts:
        current = best.get(post.identity)
        if current is None:
            best[post.identity] = post
            continue
        # Prefer a record that has a verified timestamp and more text.
        score = (post.has_timestamp, len(post.text or ""))
        current_score = (current.has_timestamp, len(current.text or ""))
        if score > current_score:
            best[post.identity] = post
    return list(best.values())


def _enrich_language(posts: list[Post]) -> None:
    for post in posts:
        if post.language:
            continue
        text = " ".join(filter(None, [post.title, post.text]))
        lang, confidence = detect_language(text)
        post.language = lang
        post.language_confidence = confidence


def collect(
    run: Run, config: AppConfig, *, store: Store | None = None
) -> tuple[Run, list[Post]]:
    """Collect from the requested platforms, honouring the acquisition deadline."""
    deadline = time.monotonic() + config.budgets.acquisition_deadline_seconds
    all_posts: list[Post] = []
    reports: list[PlatformReport] = []

    platforms = [Platform.YOUTUBE, Platform.TELEGRAM, Platform.MASTODON]
    if config.budgets.enable_reddit:
        platforms.append(Platform.REDDIT)
    if config.budgets.enable_google_news:
        platforms.append(Platform.GOOGLE_NEWS)
    if config.budgets.enable_gdelt:
        platforms.append(Platform.GDELT)

    for platform in platforms:
        report = PlatformReport(platform=platform)
        platform_posts: list[Post] = []

        if time.monotonic() >= deadline:
            report.discovery = report.retrieval = StageStatus.FAILED
            report.notes.append("acquisition deadline reached before this platform started")
            reports.append(report)
            continue

        if platform is Platform.YOUTUBE:
            terms = discovery.youtube_queries(
                run.topic,
                run.aliases,
                run.window.start,
                run.window.end,
                max_queries=config.budgets.max_queries_per_platform,
            )
            for term in terms:
                fetch = youtube.search(
                    term,
                    limit=config.budgets.max_discovery_results,
                    budgets=config.budgets,
                    topic=run.topic,
                    aliases=run.aliases,
                    window_start=run.window.start,

                    window_end=run.window.end,
                )
                run.queries.extend(fetch.queries)
                platform_posts.extend(fetch.posts)
                report.notes.extend(fetch.notes)
                report.sources_found += len(fetch.posts)
                if len(platform_posts) >= config.budgets.max_posts_per_run:
                    report.truncated = True
                    report.notes.append("post cap reached")
                    break
                if time.monotonic() >= deadline:
                    report.notes.append("acquisition deadline reached")
                    break
            report.discovery = StageStatus.OK if platform_posts else StageStatus.EMPTY
            report.retrieval = StageStatus.OK if platform_posts else StageStatus.EMPTY

        elif platform is Platform.TELEGRAM:
            found = discovery.discover_telegram(
                run.topic,
                aliases=run.aliases,
                max_queries=config.budgets.max_queries_per_platform,
                max_results=config.budgets.max_discovery_results,
                max_channels=config.budgets.max_sources_per_platform,
            )
            run.queries.extend(found.queries)
            report.notes.extend(found.notes)
            report.discovery = found.status
            report.sources_found = len(found.channels)

            if not found.channels:
                report.notes.append(
                    "no public channels discovered for this topic; Telegram coverage is empty"
                )
            for lead in found.channels:
                if time.monotonic() >= deadline:
                    report.notes.append("acquisition deadline reached mid-channel")
                    report.truncated = True
                    break
                fetch = telegram.collect_channel(
                    lead.username,
                    window_start=run.window.start,
                    window_end=run.window.end,
                    budgets=config.budgets,
                    topic=run.topic,
                    aliases=run.aliases,
                )
                run.queries.extend(fetch.queries)
                report.notes.extend(fetch.notes)
                platform_posts.extend(fetch.posts)
                if fetch.status in {StageStatus.RATE_LIMITED, StageStatus.BLOCKED_AUTH}:
                    report.notes.append(f"{lead.username}: {fetch.status}")
                if len(platform_posts) >= config.budgets.max_posts_per_run:
                    report.truncated = True
                    report.notes.append("post cap reached")
                    break
            report.retrieval = (
                StageStatus.OK if platform_posts else
                (StageStatus.PARTIAL if report.sources_found else StageStatus.EMPTY)
            )

        elif platform is Platform.MASTODON:
            fetch = mastodon.collect(
                run.topic,
                aliases=run.aliases,
                window_start=run.window.start,
                window_end=run.window.end,
                budgets=config.budgets,
            )
            run.queries.extend(fetch.queries)
            report.notes.extend(fetch.notes)
            platform_posts.extend(fetch.posts)
            report.sources_found = len(
                {p.author_id for p in fetch.posts if p.author_id}
            )
            report.discovery = fetch.status
            report.retrieval = fetch.status
            report.truncated = fetch.truncated

        elif platform is Platform.REDDIT:
            fetch = reddit.collect(
                run.topic,
                aliases=run.aliases,
                window_start=run.window.start,
                window_end=run.window.end,
                budgets=config.budgets,
                credentials=config.reddit,
            )
            run.queries.extend(fetch.queries)
            report.notes.extend(fetch.notes)
            platform_posts.extend(fetch.posts)
            report.sources_found = len({p.author_id for p in fetch.posts if p.author_id})
            report.discovery = fetch.status
            report.retrieval = fetch.status
            report.truncated = fetch.truncated

        elif platform is Platform.GOOGLE_NEWS:
            fetch = news_rss.collect(
                run.topic,
                aliases=run.aliases,
                window_start=run.window.start,
                window_end=run.window.end,
                budgets=config.budgets,
            )
            run.queries.extend(fetch.queries)
            report.notes.extend(fetch.notes)
            platform_posts.extend(fetch.posts)
            report.sources_found = len({p.author_id for p in fetch.posts if p.author_id})
            report.discovery = fetch.status
            report.retrieval = fetch.status
            report.truncated = fetch.truncated

        elif platform is Platform.GDELT:
            fetch = gdelt.collect(
                run.topic,
                aliases=run.aliases,
                window_start=run.window.start,
                window_end=run.window.end,
                budgets=config.budgets,
            )
            run.queries.extend(fetch.queries)
            report.notes.extend(fetch.notes)
            platform_posts.extend(fetch.posts)
            report.sources_found = len({p.author_id for p in fetch.posts if p.author_id})
            report.discovery = fetch.status
            report.retrieval = fetch.status
            report.truncated = fetch.truncated

        _enrich_language(platform_posts)
        report.posts_retrieved = len(platform_posts)
        report.posts_in_range = sum(
            1 for p in platform_posts if run.window.contains(p.published_at)
        )
        all_posts.extend(platform_posts)
        reports.append(report)

    all_posts = _dedupe(all_posts)
    all_posts.sort(key=lambda p: p.published_at or p.collected_at)

    run.platforms = reports
    run.status = StageStatus.OK
    if any(r.retrieval in {StageStatus.FAILED, StageStatus.RATE_LIMITED} for r in reports):
        run.status = StageStatus.PARTIAL
    if not all_posts:
        run.status = StageStatus.EMPTY
    if store is not None:
        store.save_posts(run.run_id, all_posts)
    return run, all_posts


def analyse(
    posts: list[Post],
    *,
    topic: str,
    config: AppConfig,
    aliases: list[str] | None = None,
    case_template: CaseTemplate = CaseTemplate.CUSTOM,
    tlp: TLP | None = None,
    include_claims: bool = True,
    retrieve_evidence: bool = False,
) -> AnalysisBundle:
    bundle = AnalysisBundle()

    in_scope = [p for p in posts if p.topic_match]
    excluded = len(posts) - len(in_scope)
    if excluded:
        bundle.method_notes.append(
            f"{excluded} retrieved post(s) did not match the topic terms and were excluded "
            "from narrative/coordination analysis (still present in the evidence export)"
        )

    narratives = narr_mod.build_narratives(
        in_scope,
        topic=topic,
        settings=config.analysis,
        force_lexical=config.analysis.force_lexical,
    )
    bundle.narratives = narratives.narratives
    bundle.degraded = narratives.degraded
    bundle.method_notes.extend(narratives.notes)

    posts_by_id = {p.identity: p for p in in_scope}

    coord = coord_mod.detect(
        in_scope,
        settings=config.analysis,
        vectors=narratives.vectors,
        vector_ids=narratives.vector_ids,
    )
    bundle.coordination = coord.groups
    bundle.method_notes.extend(coord.notes)

    bundle.propagation = prop_mod.build_edges(bundle.narratives, posts_by_id)
    bundle.earliest = prop_mod.earliest_observed(bundle.narratives, posts_by_id)

    if include_claims:
        claim_candidates = claims_mod.extract_claims(in_scope)
        if retrieve_evidence:
            for claim in claim_candidates:
                claim.evidence = claims_mod.search_evidence(claim)
        bundle.claims = claim_candidates

    # --- Cyber-threat-intelligence layer -----------------------------------
    bundle.indicators = indicator_mod.extract(in_scope)

    # A cluster that spans most of the corpus is a catch-all (typical of the
    # lexical fallback). Attributing a framing to it would describe the whole
    # sample, not a narrative, so it is left unclassified.
    catch_all_limit = max(3, int(len(in_scope) * 0.6))
    for narrative in bundle.narratives:
        members = [posts_by_id[i] for i in narrative.member_ids if i in posts_by_id]
        if len(members) > catch_all_limit:
            narrative.framing = ThreatFraming.NONE
            narrative.framing_evidence = []
            narrative.notes.append(
                "catch-all cluster (spans most of the corpus); framing not classified"
            )
            continue
        framing, evidence = threat_mod.classify_framing(narrative, members)
        narrative.framing = framing
        narrative.framing_evidence = evidence

    bundle.impersonation = threat_mod.detect_impersonation(
        in_scope, bundle.indicators, topic=topic, aliases=aliases
    )

    # Grading runs last: it reads narrative membership to rate information.
    grading_mod.grade(in_scope, bundle.narratives)
    bundle.method_notes.append(
        "Admiralty grades are provisional proxies derived from observable account "
        "behaviour and cross-account narrative support; every grade requires "
        "analyst confirmation."
    )

    bundle.assessment = assess_mod.synthesise(
        bundle, posts, template=case_template, tlp=tlp, topic=topic
    )

    return bundle


def execute(
    *,
    topic: str,
    window,
    config: AppConfig,
    aliases: list[str] | None = None,
    languages: list[str] | None = None,
    case_template: CaseTemplate = CaseTemplate.CUSTOM,
    tlp: TLP | None = None,
    store: Store | None = None,
    include_claims: bool = True,
    retrieve_evidence: bool = False,
) -> tuple[Run, list[Post], AnalysisBundle]:
    run = Run(
        run_id=new_run_id(),
        topic=topic,
        aliases=aliases or [],
        languages=languages or [],
        case_template=case_template,
        tlp=tlp or resolve_preset(case_template).default_tlp,
        window=window,
        config={
            "budgets": config.budgets.__dict__,
            "analysis": config.analysis.__dict__,
        },
    )
    if store is not None:
        store.save_run(run)

    started = time.monotonic()
    run, posts = collect(run, config, store=store)
    logger.info("collected %d posts in %.1fs", len(posts), time.monotonic() - started)

    bundle = analyse(
        posts,
        topic=topic,
        config=config,
        aliases=aliases,
        case_template=case_template,
        tlp=tlp,
        include_claims=include_claims,
        retrieve_evidence=retrieve_evidence,
    )
    run.finished_at = utcnow()
    if store is not None:
        store.save_analysis(run.run_id, bundle)
        store.save_run(run)
    return run, posts, bundle


def coverage_summary(run: Run, posts: list[Post]) -> dict:
    """Requested vs observed coverage -- the honesty layer for the dashboard."""
    by_platform: dict[str, dict] = defaultdict(
        lambda: {"posts": 0, "dated": 0, "undated": 0, "first": None, "last": None}
    )
    for post in posts:
        entry = by_platform[str(post.platform)]
        entry["posts"] += 1
        if post.published_at:
            entry["dated"] += 1
            if entry["first"] is None or post.published_at < entry["first"]:
                entry["first"] = post.published_at
            if entry["last"] is None or post.published_at > entry["last"]:
                entry["last"] = post.published_at
        else:
            entry["undated"] += 1
    return {
        "requested_start": run.window.start.isoformat(),
        "requested_end": run.window.end.isoformat(),
        "platforms": {
            platform: {
                **values,
                "first": values["first"].isoformat() if values["first"] else None,
                "last": values["last"].isoformat() if values["last"] else None,
            }
            for platform, values in by_platform.items()
        },
        "note": (
            "Coverage describes what was collected within budget. It is not an estimate "
            "of platform-wide activity, and empty does not mean nothing was posted."
        ),
    }
