"""SQLite database for tracking prompts, research runs, and learner iterations."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent / "learner.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_text TEXT NOT NULL,
    parent_id INTEGER REFERENCES prompts(id),
    generation INTEGER NOT NULL DEFAULT 0,
    change_description TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id INTEGER NOT NULL REFERENCES prompts(id),
    query_key TEXT NOT NULL,
    query_json TEXT NOT NULL,
    research_output TEXT NOT NULL,
    score INTEGER NOT NULL CHECK(score >= 0 AND score <= 100),
    score_coverage INTEGER NOT NULL CHECK(score_coverage >= 0 AND score_coverage <= 25),
    score_breadth INTEGER NOT NULL CHECK(score_breadth >= 0 AND score_breadth <= 25),
    score_addressability INTEGER NOT NULL CHECK(score_addressability >= 0 AND score_addressability <= 25),
    score_efficiency INTEGER NOT NULL CHECK(score_efficiency >= 0 AND score_efficiency <= 25),
    score_reasoning TEXT,
    iterations_used INTEGER,
    calls_made INTEGER,
    errors INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS learner_iterations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    iteration_number INTEGER NOT NULL,
    prompt_id INTEGER NOT NULL REFERENCES prompts(id),
    avg_score REAL,
    min_score REAL,
    max_score REAL,
    analysis TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""


@dataclass
class PromptRecord:
    id: int
    prompt_text: str
    parent_id: int | None
    generation: int
    change_description: str | None
    created_at: str


@dataclass
class RunRecord:
    id: int
    prompt_id: int
    query_key: str
    score: int
    score_coverage: int
    score_breadth: int
    score_addressability: int
    score_efficiency: int
    score_reasoning: str | None
    iterations_used: int | None
    calls_made: int | None
    errors: int
    created_at: str


@dataclass
class LearnerIterationRecord:
    id: int
    iteration_number: int
    prompt_id: int
    avg_score: float | None
    min_score: float | None
    max_score: float | None
    analysis: str | None
    created_at: str


class LearnerDB:
    """Wrapper around SQLite for the research learner's state."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- Prompts ---------------------------------------------------------------

    def insert_prompt(
        self,
        prompt_text: str,
        parent_id: int | None = None,
        generation: int = 0,
        change_description: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO prompts (prompt_text, parent_id, generation, change_description)
               VALUES (?, ?, ?, ?)""",
            (prompt_text, parent_id, generation, change_description),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_prompt(self, prompt_id: int) -> PromptRecord | None:
        row = self.conn.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,)).fetchone()
        if not row:
            return None
        return PromptRecord(
            id=row["id"],
            prompt_text=row["prompt_text"],
            parent_id=row["parent_id"],
            generation=row["generation"],
            change_description=row["change_description"],
            created_at=row["created_at"],
        )

    def get_latest_prompt(self) -> PromptRecord | None:
        row = self.conn.execute("SELECT * FROM prompts ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        return PromptRecord(
            id=row["id"],
            prompt_text=row["prompt_text"],
            parent_id=row["parent_id"],
            generation=row["generation"],
            change_description=row["change_description"],
            created_at=row["created_at"],
        )

    def get_best_prompt(self) -> PromptRecord | None:
        """Return the prompt with the highest average score across its runs."""
        row = self.conn.execute(
            """SELECT p.*, AVG(r.score) as avg_score
               FROM prompts p
               JOIN runs r ON r.prompt_id = p.id
               GROUP BY p.id
               ORDER BY avg_score DESC
               LIMIT 1"""
        ).fetchone()
        if not row:
            return None
        return PromptRecord(
            id=row["id"],
            prompt_text=row["prompt_text"],
            parent_id=row["parent_id"],
            generation=row["generation"],
            change_description=row["change_description"],
            created_at=row["created_at"],
        )

    def prompt_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM prompts").fetchone()
        return row["cnt"] if row else 0

    def get_prompt_lineage(self, prompt_id: int) -> list[PromptRecord]:
        """Walk parent_id chain from the given prompt back to the seed."""
        lineage: list[PromptRecord] = []
        current = prompt_id
        while current is not None:
            p = self.get_prompt(current)
            if not p:
                break
            lineage.append(p)
            current = p.parent_id
        lineage.reverse()
        return lineage

    # -- Runs ------------------------------------------------------------------

    def insert_run(
        self,
        prompt_id: int,
        query_key: str,
        query_json: dict,
        research_output: dict,
        score: int,
        score_coverage: int,
        score_breadth: int,
        score_addressability: int,
        score_efficiency: int,
        score_reasoning: str | None = None,
        iterations_used: int | None = None,
        calls_made: int | None = None,
        errors: int = 0,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO runs (prompt_id, query_key, query_json, research_output,
                   score, score_coverage, score_breadth, score_addressability, score_efficiency,
                   score_reasoning, iterations_used, calls_made, errors)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                prompt_id,
                query_key,
                json.dumps(query_json, default=str),
                json.dumps(research_output, default=str),
                score,
                score_coverage,
                score_breadth,
                score_addressability,
                score_efficiency,
                score_reasoning,
                iterations_used,
                calls_made,
                errors,
            ),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_runs_for_prompt(self, prompt_id: int) -> list[RunRecord]:
        rows = self.conn.execute(
            "SELECT * FROM runs WHERE prompt_id = ? ORDER BY id", (prompt_id,)
        ).fetchall()
        return [
            RunRecord(
                id=r["id"],
                prompt_id=r["prompt_id"],
                query_key=r["query_key"],
                score=r["score"],
                score_coverage=r["score_coverage"],
                score_breadth=r["score_breadth"],
                score_addressability=r["score_addressability"],
                score_efficiency=r["score_efficiency"],
                score_reasoning=r["score_reasoning"],
                iterations_used=r["iterations_used"],
                calls_made=r["calls_made"],
                errors=r["errors"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def get_all_runs(self) -> list[RunRecord]:
        rows = self.conn.execute("SELECT * FROM runs ORDER BY id").fetchall()
        return [
            RunRecord(
                id=r["id"],
                prompt_id=r["prompt_id"],
                query_key=r["query_key"],
                score=r["score"],
                score_coverage=r["score_coverage"],
                score_breadth=r["score_breadth"],
                score_addressability=r["score_addressability"],
                score_efficiency=r["score_efficiency"],
                score_reasoning=r["score_reasoning"],
                iterations_used=r["iterations_used"],
                calls_made=r["calls_made"],
                errors=r["errors"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def get_avg_score_for_prompt(self, prompt_id: int) -> float | None:
        row = self.conn.execute(
            "SELECT AVG(score) as avg FROM runs WHERE prompt_id = ?", (prompt_id,)
        ).fetchone()
        return row["avg"] if row and row["avg"] is not None else None

    def get_best_score_ever(self) -> float:
        """Highest avg score across all prompts that have runs."""
        row = self.conn.execute(
            """SELECT MAX(avg_s) as best FROM (
                   SELECT AVG(score) as avg_s FROM runs GROUP BY prompt_id
               )"""
        ).fetchone()
        return row["best"] if row and row["best"] is not None else 0.0

    # -- Learner iterations ----------------------------------------------------

    def insert_learner_iteration(
        self,
        iteration_number: int,
        prompt_id: int,
        avg_score: float | None,
        min_score: float | None,
        max_score: float | None,
        analysis: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO learner_iterations
                   (iteration_number, prompt_id, avg_score, min_score, max_score, analysis)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (iteration_number, prompt_id, avg_score, min_score, max_score, analysis),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_completed_iterations(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM learner_iterations").fetchone()
        return row["cnt"] if row else 0

    def get_all_learner_iterations(self) -> list[LearnerIterationRecord]:
        rows = self.conn.execute(
            "SELECT * FROM learner_iterations ORDER BY iteration_number"
        ).fetchall()
        return [
            LearnerIterationRecord(
                id=r["id"],
                iteration_number=r["iteration_number"],
                prompt_id=r["prompt_id"],
                avg_score=r["avg_score"],
                min_score=r["min_score"],
                max_score=r["max_score"],
                analysis=r["analysis"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def get_recent_score_history(self, limit: int = 10) -> list[dict]:
        """Return recent learner iterations with prompt change descriptions for the learner LLM."""
        rows = self.conn.execute(
            """SELECT li.iteration_number, li.avg_score, li.min_score, li.max_score,
                      p.id as prompt_id, p.generation, p.change_description
               FROM learner_iterations li
               JOIN prompts p ON p.id = li.prompt_id
               ORDER BY li.iteration_number DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {
                "iteration": r["iteration_number"],
                "avg_score": r["avg_score"],
                "min_score": r["min_score"],
                "max_score": r["max_score"],
                "prompt_id": r["prompt_id"],
                "generation": r["generation"],
                "change_description": r["change_description"],
            }
            for r in reversed(rows)
        ]

    def get_sub_scores_for_iteration(self, prompt_id: int) -> dict:
        """Compute average sub-scores for a prompt's runs."""
        row = self.conn.execute(
            """SELECT AVG(score_coverage) as coverage, AVG(score_breadth) as breadth,
                      AVG(score_addressability) as addressability, AVG(score_efficiency) as efficiency
               FROM runs WHERE prompt_id = ?""",
            (prompt_id,),
        ).fetchone()
        if not row or row["coverage"] is None:
            return {"coverage": 0, "breadth": 0, "addressability": 0, "efficiency": 0}
        return {
            "coverage": round(row["coverage"], 1),
            "breadth": round(row["breadth"], 1),
            "addressability": round(row["addressability"], 1),
            "efficiency": round(row["efficiency"], 1),
        }

    # -- Reporting helpers -----------------------------------------------------

    def get_all_prompts(self) -> list[PromptRecord]:
        rows = self.conn.execute("SELECT * FROM prompts ORDER BY id").fetchall()
        return [
            PromptRecord(
                id=r["id"],
                prompt_text=r["prompt_text"],
                parent_id=r["parent_id"],
                generation=r["generation"],
                change_description=r["change_description"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
