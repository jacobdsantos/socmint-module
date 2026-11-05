"""SQLite persistence.

Source facts are stored once per run and never rewritten by later analysis.
Analyst decisions live in their own table so re-running analysis does not erase
human review.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import (
    AnalysisBundle,
    Platform,
    Post,
    Run,
    utcnow,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    run_id TEXT NOT NULL,
    identity TEXT NOT NULL,
    platform TEXT NOT NULL,
    published_at TEXT,
    payload TEXT NOT NULL,
    PRIMARY KEY (run_id, identity)
);
CREATE TABLE IF NOT EXISTS analysis (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    note TEXT,
    reviewer TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, kind, target_id)
);
CREATE TABLE IF NOT EXISTS credit_spend (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    ts TEXT NOT NULL,
    run_id TEXT,
    units INTEGER NOT NULL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_run_platform ON posts(run_id, platform);
CREATE INDEX IF NOT EXISTS idx_posts_run_published ON posts(run_id, published_at);
CREATE INDEX IF NOT EXISTS idx_credit_provider ON credit_spend(provider, ts);
"""


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Streamlit caches this object with @st.cache_resource but runs each
        # script pass on a different worker thread, so a connection bound to its
        # creating thread raises ProgrammingError on the second render. Disable
        # the thread check and serialise access with a lock instead: the check is
        # replaced by real synchronisation, not simply switched off.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _read(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # -- runs -------------------------------------------------------------
    def save_run(self, run: Run) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs(run_id, topic, created_at, payload) VALUES (?,?,?,?)",
                (run.run_id, run.topic, run.created_at.isoformat(), run.model_dump_json()),
            )

    def load_run(self, run_id: str) -> Run | None:
        with self._lock:
            row = self._conn.execute("SELECT payload FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return Run.model_validate_json(row["payload"]) if row else None

    def list_runs(self, limit: int = 50) -> list[Run]:
        rows = self._read("SELECT payload FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [Run.model_validate_json(r["payload"]) for r in rows]

    # -- posts ------------------------------------------------------------
    def save_posts(self, run_id: str, posts: Iterable[Post]) -> None:
        with self._tx() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO posts(run_id, identity, platform, published_at, payload) "
                "VALUES (?,?,?,?,?)",
                [
                    (
                        run_id,
                        p.identity,
                        str(p.platform),
                        p.published_at.isoformat() if p.published_at else None,
                        p.model_dump_json(exclude={"raw"}),
                    )
                    for p in posts
                ],
            )

    def load_posts(self, run_id: str, platform: Platform | None = None) -> list[Post]:
        if platform:
            rows = self._read(
                "SELECT payload FROM posts WHERE run_id=? AND platform=? ORDER BY published_at",
                (run_id, str(platform)),
            )
        else:
            rows = self._read(
                "SELECT payload FROM posts WHERE run_id=? ORDER BY published_at", (run_id,)
            )
        return [Post.model_validate_json(r["payload"]) for r in rows]

    # -- analysis ---------------------------------------------------------
    def save_analysis(self, run_id: str, bundle: AnalysisBundle) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO analysis(run_id, created_at, payload) VALUES (?,?,?)",
                (run_id, utcnow().isoformat(), bundle.model_dump_json()),
            )

    def load_analysis(self, run_id: str) -> AnalysisBundle | None:
        rows = self._read("SELECT payload FROM analysis WHERE run_id=?", (run_id,))
        return AnalysisBundle.model_validate_json(rows[0]["payload"]) if rows else None

    # -- human review (survives re-analysis) ------------------------------
    def save_review(
        self, run_id: str, kind: str, target_id: str, decision: str,
        note: str | None = None, reviewer: str | None = None,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO reviews"
                "(run_id, kind, target_id, decision, note, reviewer, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (run_id, kind, target_id, decision, note, reviewer, utcnow().isoformat()),
            )

    def load_reviews(self, run_id: str) -> dict[str, dict[str, Any]]:
        rows = self._read(
            "SELECT kind, target_id, decision, note, reviewer, created_at "
            "FROM reviews WHERE run_id=?",
            (run_id,),
        )
        return {
            f"{r['kind']}:{r['target_id']}": {
                "decision": r["decision"],
                "note": r["note"],
                "reviewer": r["reviewer"],
                "created_at": r["created_at"],
            }
            for r in rows
        }

    def apply_reviews(self, run_id: str, bundle: AnalysisBundle) -> AnalysisBundle:
        """Fold stored human decisions into a freshly computed bundle."""
        reviews = self.load_reviews(run_id)
        for narrative in bundle.narratives:
            r = reviews.get(f"narrative:{narrative.narrative_id}")
            if r:
                narrative.reviewer_label = r["decision"]
                narrative.notes.append(r["note"] or "")
        for group in bundle.coordination:
            r = reviews.get(f"coordination:{group.group_id}")
            if r:
                group.reviewer_decision = r["decision"]
        for claim in bundle.claims:
            r = reviews.get(f"claim:{claim.claim_id}")
            if r:
                claim.verdict = r["decision"]  # type: ignore[assignment]
                claim.rationale = r["note"]
                claim.reviewer = r["reviewer"]
                claim.reviewed_at = datetime.fromisoformat(r["created_at"])
        return bundle

    # -- metered spend ----------------------------------------------------
    def record_spend(
        self,
        provider: str,
        units: int,
        *,
        run_id: str | None = None,
        note: str | None = None,
    ) -> None:
        """Append one spend entry. Negative units are refunds, not corrections.

        Append-only on purpose: a ledger you can edit in place is not a ledger,
        and the whole point of this table is that a test run cannot quietly
        forget it spent the demo budget.
        """
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO credit_spend(provider, ts, run_id, units, note) VALUES (?,?,?,?,?)",
                (provider, utcnow().isoformat(), run_id, units, note),
            )

    def spend_total(self, provider: str) -> int:
        rows = self._read(
            "SELECT COALESCE(SUM(units), 0) AS total FROM credit_spend WHERE provider=?",
            (provider,),
        )
        return int(rows[0]["total"]) if rows else 0

    def spend_history(self, provider: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._read(
            "SELECT ts, run_id, units, note FROM credit_spend "
            "WHERE provider=? ORDER BY id DESC LIMIT ?",
            (provider, limit),
        )
        return [dict(r) for r in rows]

    # -- convenience ------------------------------------------------------
    def export_rows(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT payload FROM posts WHERE run_id=? ORDER BY published_at", (run_id,)
        ).fetchall()
        return [json.loads(r["payload"]) for r in rows]
