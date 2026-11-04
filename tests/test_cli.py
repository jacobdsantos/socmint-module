from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from socmint import cli, pipeline
from socmint.models import DateRange, Platform, Run, StageStatus
from socmint.settings import AppConfig


def test_bare_date_is_inclusive_local_day():
    tz = ZoneInfo("Asia/Manila")
    start = cli._parse_bound("2026-10-20", tz, is_end=False)
    end = cli._parse_bound("2026-10-20", tz, is_end=True)
    # Manila is UTC+8: local midnight is 16:00 UTC the previous day.
    assert start == datetime(2026, 10, 19, 16, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 20, 16, 0, tzinfo=UTC)
    assert (end - start).total_seconds() == 86400


def test_iso_timestamp_is_taken_literally():
    tz = ZoneInfo("UTC")
    value = cli._parse_bound("2026-10-20T05:00:00+00:00", tz, is_end=False)
    assert value == datetime(2026, 10, 20, 5, 0, tzinfo=UTC)


def test_naive_iso_uses_supplied_timezone():
    tz = ZoneInfo("Asia/Manila")
    value = cli._parse_bound("2026-10-20T08:00:00", tz, is_end=False)
    assert value == datetime(2026, 10, 20, 0, 0, tzinfo=UTC)


def test_parser_requires_topic_and_dates():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run"])


def test_parser_accepts_repeatable_alias():
    parser = cli.build_parser()
    args = parser.parse_args(
        ["run", "--topic", "x", "--start", "2026-10-20", "--end", "2026-10-21",
         "--alias", "a", "--alias", "b"]
    )
    assert args.aliases == ["a", "b"]


def test_main_rejects_unknown_timezone(capsys):
    code = cli.main(["run", "--topic", "x", "--start", "2026-10-20", "--end", "2026-10-21",
                     "--tz", "Not/AZone", "--data-dir", "/tmp/socmint-test-tz"])
    assert code == 2


def test_main_rejects_inverted_range(capsys):
    code = cli.main(["run", "--topic", "x", "--start", "2026-10-25", "--end", "2026-10-21",
                     "--data-dir", "/tmp/socmint-test-inv"])
    assert code == 2


def test_collect_marks_partial_when_a_platform_fails(monkeypatch):
    """A failed platform must surface as partial, not as a silent empty run."""

    def failing_youtube(term, **kwargs):
        from socmint.collection.youtube import YouTubeFetch

        return YouTubeFetch(status=StageStatus.FAILED, notes=["blocked"])

    def empty_telegram(topic, **kwargs):
        from socmint.collection.discovery import DiscoveryResult

        return DiscoveryResult(status=StageStatus.EMPTY, notes=["nothing found"])

    monkeypatch.setattr(pipeline.youtube, "search", failing_youtube)
    monkeypatch.setattr(pipeline.discovery, "discover_telegram", empty_telegram)

    run = Run(
        run_id="r1",
        topic="x",
        window=DateRange(
            start=datetime(2026, 10, 20, tzinfo=UTC),
            end=datetime(2026, 11, 3, tzinfo=UTC),
        ),
    )
    run, posts = pipeline.collect(run, AppConfig())
    assert posts == []
    assert run.status in {StageStatus.PARTIAL, StageStatus.EMPTY}
    statuses = {str(r.platform): r for r in run.platforms}
    assert Platform.YOUTUBE.value in statuses
    assert Platform.TELEGRAM.value in statuses


def test_collect_respects_post_cap(monkeypatch):
    from socmint.collection.youtube import YouTubeFetch
    from tests.conftest import make_post

    def many_youtube(term, **kwargs):
        posts = [
            make_post(post_id=f"y{i}", author_id="chan", text="typhoon Philippines coverage")
            for i in range(50)
        ]
        return YouTubeFetch(posts=posts, status=StageStatus.OK)

    def empty_telegram(topic, **kwargs):
        from socmint.collection.discovery import DiscoveryResult

        return DiscoveryResult(status=StageStatus.EMPTY)

    monkeypatch.setattr(pipeline.youtube, "search", many_youtube)
    monkeypatch.setattr(pipeline.discovery, "discover_telegram", empty_telegram)

    from dataclasses import replace

    config = AppConfig()
    config = replace(config, budgets=replace(config.budgets, max_posts_per_run=10))
    run = Run(
        run_id="r2",
        topic="x",
        window=DateRange(
            start=datetime(2026, 10, 20, tzinfo=UTC),
            end=datetime(2026, 11, 3, tzinfo=UTC),
        ),
    )
    run, posts = pipeline.collect(run, config)
    youtube_report = next(r for r in run.platforms if r.platform == Platform.YOUTUBE)
    assert youtube_report.truncated is True
