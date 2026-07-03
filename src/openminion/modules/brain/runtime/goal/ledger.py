"""Append-only step ledger for live goal runs."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openminion.modules.brain.constants import MissionStatus

from .clock import goal_now_ms
from .loop import GoalRunOutcome


class _StrictLedgerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GoalRunStep(_StrictLedgerModel):
    """One durable step in a goal run."""

    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    turn_index: int = Field(ge=0)
    started_at_ms: int = Field(ge=0)
    ended_at_ms: int = Field(ge=0)
    prompt_ref: str = ""
    action_summary: str = ""
    tool_evidence_refs: tuple[str, ...] = ()
    verification_summary: str = ""
    evaluator_outcome: GoalRunOutcome
    mission_status: MissionStatus
    evaluator_reason: str = ""
    next_instruction: str = ""
    error_refs: tuple[str, ...] = ()
    autonomy_run_id: str = ""
    proof_ref: str = ""

    @field_validator(
        "prompt_ref",
        "action_summary",
        "verification_summary",
        "evaluator_reason",
        "next_instruction",
        "autonomy_run_id",
        "proof_ref",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: object) -> str:
        return str(value or "").strip()


class GoalRunLedgerSummary(_StrictLedgerModel):
    run_id: str = ""
    step_count: int = 0
    latest_outcome: str = ""
    latest_reason: str = ""
    latest_next_instruction: str = ""
    evidence_refs: tuple[str, ...] = ()
    error_refs: tuple[str, ...] = ()


class SQLiteGoalRunStepLedger:
    """SQLite-backed append-only ledger colocated with goal-run state."""

    def __init__(self, sqlite_path: str | Path) -> None:
        self.sqlite_path = Path(sqlite_path)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def append(self, step: GoalRunStep) -> GoalRunStep:
        self._ensure_schema()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO goal_run_steps (
                    run_id, session_id, goal_id, turn_index, started_at_ms,
                    ended_at_ms, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step.run_id,
                    step.session_id,
                    step.goal_id,
                    step.turn_index,
                    step.started_at_ms,
                    step.ended_at_ms,
                    step.model_dump_json(),
                ),
            )
        return step

    def list_for_run(self, run_id: str) -> tuple[GoalRunStep, ...]:
        self._ensure_schema()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                  FROM goal_run_steps
                 WHERE run_id = ?
                 ORDER BY turn_index ASC, id ASC
                """,
                (str(run_id or "").strip(),),
            ).fetchall()
        return tuple(GoalRunStep.model_validate_json(str(row[0])) for row in rows)

    def summary_for_run(self, run_id: str) -> GoalRunLedgerSummary:
        steps = self.list_for_run(run_id)
        if not steps:
            return GoalRunLedgerSummary(run_id=str(run_id or "").strip())
        latest = steps[-1]
        evidence_refs = tuple(
            dict.fromkeys(ref for step in steps for ref in step.tool_evidence_refs)
        )
        error_refs = tuple(
            dict.fromkeys(ref for step in steps for ref in step.error_refs)
        )
        return GoalRunLedgerSummary(
            run_id=latest.run_id,
            step_count=len(steps),
            latest_outcome=latest.evaluator_outcome,
            latest_reason=latest.evaluator_reason,
            latest_next_instruction=latest.next_instruction,
            evidence_refs=evidence_refs,
            error_refs=error_refs,
        )

    def append_wake_event(
        self,
        *,
        run_id: str,
        session_id: str,
        goal_id: str,
        reason: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> GoalRunStep:
        next_index = len(self.list_for_run(run_id))
        now = goal_now_ms()
        return self.append(
            GoalRunStep(
                run_id=run_id,
                session_id=session_id,
                goal_id=goal_id,
                turn_index=next_index,
                started_at_ms=now,
                ended_at_ms=now,
                action_summary="async wake",
                tool_evidence_refs=evidence_refs,
                evaluator_outcome="continue",
                mission_status=MissionStatus.ACTIVE,
                evaluator_reason=reason,
                next_instruction="resume goal after async wake",
            )
        )

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS goal_run_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    goal_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    started_at_ms INTEGER NOT NULL,
                    ended_at_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_goal_run_steps_run_turn
                  ON goal_run_steps(run_id, turn_index, id)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn


__all__ = [
    "GoalRunLedgerSummary",
    "GoalRunStep",
    "SQLiteGoalRunStepLedger",
]
