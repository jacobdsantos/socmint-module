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
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import pipeline
from .exports import export
from .models import TLP, CaseTemplate, DateRange
from .presets import catalogue
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
