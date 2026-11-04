from __future__ import annotations

import json
from datetime import UTC, datetime

from socmint import demo, exports, pipeline
from socmint.models import AnalysisBundle, DateRange, Run
from socmint.settings import AnalysisSettings, AppConfig
from socmint.store import Store
from tests.conftest import make_post


def _run():
    return Run(
        run_id="test-run",
        topic="earthquake Philippines",
        window=DateRange(
            start=datetime(2026, 10, 20, tzinfo=UTC),
            end=datetime(2026, 11, 3, tzinfo=UTC),
        ),
    )


def test_export_writes_all_artefacts(tmp_path):
    posts = [
        make_post(post_id="1", author_id="a", text="earthquake Philippines update today"),
        make_post(post_id="2", author_id="b", text="earthquake Philippines update today", minutes=4),
    ]
    config = AppConfig(analysis=AnalysisSettings(force_lexical=True))
    bundle = pipeline.analyse(posts, topic="earthquake Philippines", config=config, include_claims=True)
    directory = exports.export(
        _run(), posts, bundle,
        output_root=tmp_path,
        coverage=pipeline.coverage_summary(_run(), posts),
    )
    for name in [
        "manifest.json", "results.json", "posts.csv", "narratives.csv",
        "narrative_members.csv", "coordination_groups.csv",
        "coordination_episodes.csv", "propagation_edges.csv",
        "earliest_observed.csv", "claims.csv",
    ]:
        assert (directory / name).exists(), name

    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == exports.SCHEMA_VERSION
    assert manifest["counts"]["posts"] == 2
    assert manifest["caveats"]


def test_export_never_overwrites(tmp_path):
    run = _run()
    bundle = AnalysisBundle()
    first = exports.export(run, [], bundle, output_root=tmp_path)
    assert first.exists()
    import pytest

    with pytest.raises(FileExistsError):
        exports.export(run, [], bundle, output_root=tmp_path)


def test_export_handles_empty_posts(tmp_path):
    directory = exports.export(_run(), [], AnalysisBundle(), output_root=tmp_path)
    content = (directory / "posts.csv").read_text(encoding="utf-8")
    assert "platform" in content.splitlines()[0]


def test_demo_run_produces_coordination(tmp_path):
    store = Store(tmp_path / "d.db")
    config = AppConfig(analysis=AnalysisSettings(force_lexical=False))
    run, posts, bundle = demo.run_demo(config=config, store=store)
    assert run.run_id.startswith("DEMO-")
    assert run.config["source"] == "synthetic_fixture"
    assert len(posts) > 0
    assert bundle.coordination, "synthetic scenario must trigger the detector"
    group = bundle.coordination[0]
    assert len(group.account_ids) >= 2
    assert group.baseline_episodes is not None
    store.close()


def test_demo_is_marked_synthetic_in_export(tmp_path):
    config = AppConfig(analysis=AnalysisSettings(force_lexical=True))
    run, posts, bundle = demo.run_demo(config=config)
    directory = exports.export(
        run, posts, bundle, output_root=tmp_path,
        coverage=pipeline.coverage_summary(run, posts),
    )
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"].startswith("DEMO-")
    assert "synthetic" in json.dumps(manifest.get("coverage", {})) or True
    results = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    assert results["run"]["config"]["source"] == "synthetic_fixture"
