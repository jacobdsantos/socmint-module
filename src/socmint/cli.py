"""Command-line entry point.

Date handling: a bare ``YYYY-MM-DD`` is an inclusive local calendar date. The
resulting window is half-open in UTC: ``[start_local_midnight, end_local_midnight
+ 1 day)``. A full ISO timestamp is taken literally and must include an offset
or be interpreted in ``--tz``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import pipeline
from .exports import export
from .models import TLP, CaseTemplate, DateRange
from .presets import catalogue
from .prose import plural
from .settings import AppConfig
from .store import Store


def _parse_bound(value: str, tz: ZoneInfo, *, is_end: bool) -> datetime:
    try:
        parsed_date = date.fromisoformat(value)
    except ValueError:
        parsed_dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=tz)
        return parsed_dt.astimezone(UTC)
    local = datetime.combine(parsed_date, time.min, tzinfo=tz)
    if is_end:
        local += timedelta(days=1)  # inclusive end date -> exclusive upper bound
    return local.astimezone(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="socmint",
        description="Topic-driven SOCMINT collection and evidence-linked analysis.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="collect and analyse a topic")
    run.add_argument("--topic", required=True)
    run.add_argument("--start", required=True, help="YYYY-MM-DD or ISO timestamp")
    run.add_argument("--end", required=True, help="YYYY-MM-DD (inclusive) or ISO timestamp")
    run.add_argument("--alias", action="append", default=[], dest="aliases")
    run.add_argument("--lang", action="append", default=[], dest="languages")
    run.add_argument(
        "--case",
        default="custom",
        choices=[preset["template"] for preset in catalogue()],
        help="case template: frames the assessment, not the collection",
    )
    run.add_argument(
        "--tlp",
        default=None,
        choices=[marking.value for marking in TLP],
        help="TLP marking for the assessment (default from the case template)",
    )
    run.add_argument("--tz", default="UTC", help="IANA timezone for bare dates (default UTC)")
    run.add_argument("--data-dir", default="data")
    run.add_argument("--export", dest="export_dir", default=None)
    run.add_argument("--evidence", action="store_true", help="retrieve candidate claim evidence")
    run.add_argument("--no-claims", action="store_true")
    run.add_argument("--max-posts", type=int, default=None)
    run.add_argument("--offline-analysis", action="store_true",
                     help="skip the embedding model and use lexical grouping")
    run.add_argument("--no-news", action="store_true",
                     help="skip Google News; collect social sources only")
    run.add_argument("--reddit", action="store_true", dest="enable_reddit",
                     help="also collect Reddit (off by default; needs a free OAuth app)")
    run.add_argument("--x", action="store_true", dest="enable_x",
                     help="include X via Xpoz (metered; also needs --allow-spend)")
    run.add_argument("--allow-spend", action="store_true",
                     help="permit spending credits; off unless given")
    run.add_argument("--max-credits", type=int, default=None,
                     help="ceiling for this run (default from policy)")
    run.add_argument("--x-max-results", type=int, default=None,
                     help="max X posts kept per query (default 10)")
    run.add_argument("--x-cooldown", type=float, default=None,
                     help="min seconds between billable X calls (default 30; 0 disables)")
    run.add_argument("--gdelt", action="store_true",
                     help="also query GDELT (IP rate-limited; off by default)")

    demo = sub.add_parser(
        "demo",
        help="run the labelled SYNTHETIC coordination scenario (not live data)",
    )
    demo.add_argument("--data-dir", default="data")
    demo.add_argument("--export", dest="export_dir", default=None)
    demo.add_argument("--offline-analysis", action="store_true",
                      help="skip the embedding model and use lexical grouping")

    show = sub.add_parser("show", help="print a stored run summary")
    show.add_argument("run_id")
    show.add_argument("--data-dir", default="data")

    listing = sub.add_parser("list", help="list stored runs")
    listing.add_argument("--data-dir", default="data")

    site = sub.add_parser(
        "export-site",
        help="write a run as a self-contained static HTML case file",
    )
    site.add_argument("--run", required=True, dest="run_id")
    site.add_argument("--out", required=True, help="output directory")
    site.add_argument("--data-dir", default="data")
    site.add_argument(
        "--summary",
        action="store_true",
        help="add a model-written summary beside the brief (needs GEMINI_API_KEY)",
    )
    site.add_argument(
        "--summary-model",
        default=None,
        help="model to use for --summary (default: the built-in default)",
    )

    reanalyse = sub.add_parser(
        "reanalyse",
        help="re-run analysis over an existing run's stored posts (no collection)",
    )
    reanalyse.add_argument("--run", required=True, dest="run_id")
    reanalyse.add_argument("--data-dir", default="data")

    credits = sub.add_parser(
        "credits", help="show metered-API balance and spend history"
    )
    credits.add_argument("--data-dir", default="data")
    credits.add_argument("--limit", type=int, default=20)

    return parser


def _print_summary(run, posts, bundle) -> None:
    print(f"run: {run.run_id}")
    print(f"topic: {run.topic}   window: {run.window.start.date()} .. "
          f"{(run.window.end - timedelta(microseconds=1)).date()} UTC")
    print(f"status: {run.status}")
    for report in run.platforms:
        print(
            f"  {report.platform:<9} discovery={report.discovery:<8} "
            f"retrieval={report.retrieval:<8} posts={report.posts_retrieved:<4} "
            f"in_range={report.posts_in_range}"
        )
        for note in report.notes[:3]:
            print(f"      note: {note}")
    print(f"posts collected: {len(posts)}  (dated: {sum(1 for p in posts if p.published_at)})")
    print(f"narratives: {len(bundle.narratives)}  "
          f"coordination groups: {len(bundle.coordination)}  "
          f"propagation edges: {len(bundle.propagation)}  "
          f"claims: {len(bundle.claims)}")
    actionable = sum(1 for i in bundle.indicators if i.role.value == "indicator")
    references = sum(1 for i in bundle.indicators if i.role.value == "reference")
    print(f"indicators: {actionable}  references excluded: {references}  "
          f"impersonation findings: {len(bundle.impersonation)}")

    if bundle.assessment is not None:
        assessment = bundle.assessment
        print(f"case: {assessment.case_template.value}  {assessment.tlp.value}  "
              f"risk={assessment.risk_score}/100  confidence={assessment.confidence}")
        for judgement in assessment.key_judgements:
            print(f"  {judgement.judgement_id} [{judgement.confidence}] {judgement.statement}")

    framed = [n for n in bundle.narratives if n.framing.value != "none"]
    if framed:
        print("framings: " + ", ".join(f"{n.narrative_id}={n.framing.value}" for n in framed))

    if bundle.degraded:
        print("WARNING: analysis degraded -- see method notes")
    for note in bundle.method_notes[:5]:
        print(f"  method: {note}")


def main(argv: list[str] | None = None) -> int:
    from .envfile import load_env_file

    # Before any config is built, so .env and an exported shell variable are
    # equivalent from the caller's point of view.
    load_env_file()
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "list":
        with Store(Path(args.data_dir) / "socmint.db") as store:
            for run in store.list_runs():
                print(f"{run.run_id}  {run.status:<8} {run.topic}")
        return 0

    if args.command == "export-site":
        from .site import export_site

        db = Path(args.data_dir) / "socmint.db"
        if not db.exists():
            print(f"no database at {db}", file=sys.stderr)
            return 2
        with Store(db) as store:
            run = store.load_run(args.run_id)
            if run is None:
                print(f"unknown run: {args.run_id}", file=sys.stderr)
                return 2
            posts = store.load_posts(args.run_id)
            bundle = store.load_analysis(args.run_id)
            if bundle is None:
                print(f"run {args.run_id} has no analysis", file=sys.stderr)
                return 2
        summary = None
        if args.summary:
            from .summary import DEFAULT_MODEL, SummaryUnavailable, build_summary

            key = os.environ.get("GEMINI_API_KEY", "").strip()
            if not key:
                # A missing key is not a reason to fail an export. The page is
                # complete without the summary; say so and carry on.
                print(
                    "note: GEMINI_API_KEY is not set; exporting without a model summary",
                    file=sys.stderr,
                )
            else:
                try:
                    summary = build_summary(
                        run,
                        bundle,
                        post_count=len(posts),
                        api_key=key,
                        model=args.summary_model or DEFAULT_MODEL,
                    )
                    print(f"summary: {summary.provenance}")
                except SummaryUnavailable as exc:
                    print(f"note: no model summary ({exc})", file=sys.stderr)

        try:
            index = export_site(run, posts, bundle, args.out, summary=summary)
        except FileExistsError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"wrote {index}")
        print("deploy: wrangler pages deploy <out>   or   vercel deploy <out> --prod")
        return 0

    if args.command == "reanalyse":
        db = Path(args.data_dir) / "socmint.db"
        if not db.exists():
            print(f"no database at {db}", file=sys.stderr)
            return 2
        config = AppConfig(data_dir=Path(args.data_dir))
        with Store(db) as store:
            run = store.load_run(args.run_id)
            if run is None:
                print(f"unknown run: {args.run_id}", file=sys.stderr)
                return 2
            posts = store.load_posts(args.run_id)
            if not posts:
                print(f"run {args.run_id} has no stored posts", file=sys.stderr)
                return 2

            # Analyst decisions live in their own table, not in the bundle, so
            # they survive. Say so rather than leaving it to be discovered.
            reviews = store.load_reviews(args.run_id)
            if reviews:
                print(
                    f"note: {plural(len(reviews), 'analyst review')} kept; the bundle "
                    "carries no verdicts of its own",
                    file=sys.stderr,
                )

            # Analysis is derived from the stored posts, so it can be rebuilt
            # without touching a collector or spending a credit. Source facts
            # (the posts) are not modified; only the derived bundle is replaced.
            bundle = pipeline.analyse(
                posts,
                topic=run.topic,
                config=config,
                aliases=run.aliases,
                case_template=run.case_template,
                tlp=run.tlp,
            )
            store.save_analysis(run.run_id, bundle)

        print(f"reanalysed {run.run_id}")
        _print_summary(run, posts, bundle)
        return 0

    if args.command == "credits":
        from .spend import CreditLedger

        config = AppConfig(data_dir=Path(args.data_dir))
        db = Path(args.data_dir) / "socmint.db"
        if not db.exists():
            print(f"no ledger at {db}")
            return 0
        with Store(db) as store:
            ledger = CreditLedger(store, config.credits)
            summary = ledger.summary()
            print(f"provider            {summary['provider']}")
            print(f"allowance           {summary['allowance']}")
            print(f"spent               {summary['spent']}")
            print(f"remaining           {summary['remaining']}")
            print(f"demo reserve        {summary['demo_reserve']}")
            print(f"available to test   {summary['available_for_testing']}")
            print(f"per-run ceiling     {summary['max_per_run']}")
            print(f"max results/query   {summary['max_results_per_query']}")
            print(f"call cooldown       {summary['min_seconds_between_calls']:g}s "
                  f"(waiting {summary['cooldown_remaining']:g}s)")
            print(f"spending enabled    {summary['allow_spend']}")
            rows = ledger.history(limit=args.limit)
            if rows:
                print("\nrecent entries")
                for row in rows:
                    print(
                        f"  {row['ts'][:19]}  {row['units']:>4}  "
                        f"{row['run_id'] or '-':<24} {row['note'] or ''}"
                    )
        return 0

    if args.command == "demo":
        from .demo import run_demo

        config = AppConfig(data_dir=Path(args.data_dir))
        if args.offline_analysis:
            from dataclasses import replace

            config = replace(config, analysis=replace(config.analysis, force_lexical=True))
        Path(args.data_dir).mkdir(parents=True, exist_ok=True)
        with Store(Path(args.data_dir) / "socmint.db") as store:
            run, posts, bundle = run_demo(config=config, store=store)
            print("SYNTHETIC SCENARIO — fictional accounts and text, not live collection.")
            _print_summary(run, posts, bundle)
            if args.export_dir:
                directory = export(
                    run, posts, bundle,
                    output_root=Path(args.export_dir),
                    coverage=pipeline.coverage_summary(run, posts),
                )
                print(f"exported: {directory}")
        return 0

    if args.command == "show":
        with Store(Path(args.data_dir) / "socmint.db") as store:
            run = store.load_run(args.run_id)
            if not run:
                print(f"run not found: {args.run_id}", file=sys.stderr)
                return 1
            posts = store.load_posts(args.run_id)
            bundle = store.load_analysis(args.run_id)
            if bundle is None:
                print("no analysis stored for this run", file=sys.stderr)
                return 1
            _print_summary(run, posts, bundle)
        return 0

    try:
        tz = ZoneInfo(args.tz)
    except ZoneInfoNotFoundError:
        print(f"unknown timezone: {args.tz}", file=sys.stderr)
        return 2

    start = _parse_bound(args.start, tz, is_end=False)
    end = _parse_bound(args.end, tz, is_end=True)
    try:
        window = DateRange(start=start, end=end)
    except ValueError as exc:
        print(f"invalid date range: {exc}", file=sys.stderr)
        return 2

    config = AppConfig(data_dir=Path(args.data_dir))
    if args.max_posts:
        from dataclasses import replace

        config = replace(config, budgets=replace(config.budgets, max_posts_per_run=args.max_posts))
    if args.no_news or args.enable_reddit or args.gdelt or args.enable_x:
        from dataclasses import replace

        config = replace(
            config,
            budgets=replace(
                config.budgets,
                enable_google_news=not args.no_news,
                enable_reddit=args.enable_reddit,
                enable_x=args.enable_x,
                enable_gdelt=args.gdelt,
            ),
        )
    if (
        args.allow_spend
        or args.max_credits is not None
        or args.x_max_results is not None
        or args.x_cooldown is not None
    ):
        from dataclasses import replace

        credits = config.credits
        if args.allow_spend:
            credits = replace(credits, allow_spend=True)
        if args.max_credits is not None:
            credits = replace(credits, max_per_run=max(0, args.max_credits))
        if args.x_max_results is not None:
            credits = replace(credits, max_results_per_query=max(1, args.x_max_results))
        if args.x_cooldown is not None:
            credits = replace(credits, min_seconds_between_calls=max(0.0, args.x_cooldown))
        config = replace(config, credits=credits)
    if args.offline_analysis:
        from dataclasses import replace

        config = replace(config, analysis=replace(config.analysis, force_lexical=True))

    Path(args.data_dir).mkdir(parents=True, exist_ok=True)
    with Store(Path(args.data_dir) / "socmint.db") as store:
        run, posts, bundle = pipeline.execute(
            topic=args.topic,
            window=window,
            config=config,
            aliases=args.aliases,
            languages=args.languages,
            case_template=CaseTemplate(args.case),
            tlp=TLP(args.tlp) if args.tlp else None,
            store=store,
            include_claims=not args.no_claims,
            retrieve_evidence=args.evidence,
        )
        coverage = pipeline.coverage_summary(run, posts)
        _print_summary(run, posts, bundle)

        if args.export_dir:
            directory = export(
                run, posts, bundle,
                output_root=Path(args.export_dir),
                coverage=coverage,
            )
            print(f"exported: {directory}")
        else:
            print(json.dumps(coverage, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
