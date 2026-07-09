"""Session-scoped goal run-until-done controller for GRUD v1."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openminion.modules.brain.constants import MissionStatus
from openminion.modules.prompting.continuation import build_goal_run_continuation_prompt
from openminion.modules.brain.storage.goals import GoalStore
from .clock import goal_now_ms
from openminion.modules.task.autonomy import (
    AutonomyProofPacket,
    AutonomyRun,
    AutonomyRunError,
    AutonomyRunPhase,
    AutonomyRunStatus,
    AutonomyRunStore,
    CommandEvidence,
    ContinuationPolicy,
    EvidenceStatus,
    build_terminal_proof_packet,
)

GoalRunOutcome = Literal[
    "satisfied",
    "continue",
    "blocked",
    "needs_user",
    "awaiting_async",
    "halted",
]

_TERMINAL_OUTCOMES: set[str] = {
    "satisfied",
    "blocked",
    "needs_user",
    "awaiting_async",
    "halted",
}


class _StrictGoalRunModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GoalRunCaps(_StrictGoalRunModel):
    """Conservative v1 continuation caps owned by GRUD-01."""

    max_auto_turns: int = Field(default=3, ge=1)
    max_wall_clock_seconds: int = Field(default=15 * 60, ge=1)
    repeated_no_progress_limit: int = Field(default=2, ge=1)
    token_cost_cap: str = "runtime_cap_or_unknown"
    user_interrupt_enabled: bool = True


class GoalRunCapState(_StrictGoalRunModel):
    max_auto_turns: int = Field(ge=1)
    turns_used: int = Field(ge=0)
    max_wall_clock_seconds: int = Field(ge=1)
    elapsed_seconds: int = Field(ge=0)
    token_cost_cap: str
    user_interrupt_enabled: bool = True
    repeated_no_progress_limit: int = Field(ge=1)
    repeated_no_progress_count: int = Field(ge=0)


class GoalRunState(_StrictGoalRunModel):
    """Persisted current-session goal loop state."""

    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    started_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)
    turn_count: int = Field(default=0, ge=0)
    last_evaluator_reason: str = ""
    status: MissionStatus = MissionStatus.ACTIVE
    caps: GoalRunCaps = Field(default_factory=GoalRunCaps)
    latest_evidence_refs: tuple[str, ...] = ()
    latest_next_instruction: str = ""
    repeated_no_progress_count: int = Field(default=0, ge=0)
    active: bool = True
    proof_packet_ref: str | None = None


class GoalRunEvaluation(_StrictGoalRunModel):
    """Typed evaluator result; runtime validates structure, not prose meaning."""

    goal_id: str = Field(min_length=1)
    outcome: GoalRunOutcome
    mission_status: MissionStatus
    reason: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    next_instruction: str = ""

    @field_validator("reason", "next_instruction", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _validate_status_matches_outcome(self) -> "GoalRunEvaluation":
        expected = _OUTCOME_TO_STATUS[self.outcome]
        if self.mission_status != expected:
            raise ValueError(
                "mission_status must match the canonical GRUD outcome mapping"
            )
        return self


class GoalContinuationDecision(_StrictGoalRunModel):
    """Controller decision after one evaluator result."""

    should_continue: bool
    mission_status: MissionStatus
    stop_reason: str = ""
    continuation_prompt: str = ""
    cap_state: GoalRunCapState


class GoalRunProofAdapter(_StrictGoalRunModel):
    """Lossless GRUD facts wrapped around the AWRK proof packet shape."""

    autonomy_proof: AutonomyProofPacket
    session_id: str
    final_goal_status: MissionStatus
    turn_count: int = Field(ge=0)
    latest_evaluator_reason: str
    cap_state: GoalRunCapState
    evidence_refs: tuple[str, ...] = ()
    proof_ref: str | None = None

    def to_autonomy_packet(self) -> AutonomyProofPacket:
        return self.autonomy_proof


class SQLiteGoalRunStore:
    """Small session-run store colocated with the brain runtime SQLite DB."""

    def __init__(self, sqlite_path: str | Path) -> None:
        self.sqlite_path = Path(sqlite_path)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def save(self, state: GoalRunState) -> GoalRunState:
        self._ensure_schema()
        payload = state.model_dump_json()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO goal_run_states (
                    run_id, session_id, goal_id, status, active, updated_at_ms,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    goal_id = excluded.goal_id,
                    status = excluded.status,
                    active = excluded.active,
                    updated_at_ms = excluded.updated_at_ms,
                    payload_json = excluded.payload_json
                """,
                (
                    state.run_id,
                    state.session_id,
                    state.goal_id,
                    state.status.value,
                    1 if state.active else 0,
                    state.updated_at_ms,
                    payload,
                ),
            )
        return state

    def get(self, run_id: str) -> GoalRunState | None:
        row = self._one(
            "SELECT payload_json FROM goal_run_states WHERE run_id = ?",
            (str(run_id or "").strip(),),
        )
        if row is None:
            return None
        return GoalRunState.model_validate_json(str(row[0]))

    def active_for_session(self, session_id: str) -> GoalRunState | None:
        row = self._one(
            """
            SELECT payload_json
              FROM goal_run_states
             WHERE session_id = ? AND active = 1
             ORDER BY updated_at_ms DESC
             LIMIT 1
            """,
            (str(session_id or "").strip(),),
        )
        if row is None:
            return None
        return GoalRunState.model_validate_json(str(row[0]))

    def latest_for_session(self, session_id: str) -> GoalRunState | None:
        row = self._one(
            """
            SELECT payload_json
              FROM goal_run_states
             WHERE session_id = ?
             ORDER BY updated_at_ms DESC
             LIMIT 1
            """,
            (str(session_id or "").strip(),),
        )
        if row is None:
            return None
        return GoalRunState.model_validate_json(str(row[0]))

    def deactivate_session(self, session_id: str, *, except_run_id: str = "") -> None:
        normalized_session = str(session_id or "").strip()
        if not normalized_session:
            return
        active = self.active_for_session(normalized_session)
        if active is None or active.run_id == except_run_id:
            return
        self.save(
            active.model_copy(
                update={"active": False, "updated_at_ms": goal_now_ms()},
            )
        )

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS goal_run_states (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    goal_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    updated_at_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_goal_run_states_session_active
                  ON goal_run_states(session_id, active, updated_at_ms DESC)
                """
            )

    def _one(self, sql: str, params: tuple[object, ...]) -> sqlite3.Row | None:
        self._ensure_schema()
        with self._connect() as conn:
            return cast(sqlite3.Row | None, conn.execute(sql, params).fetchone())

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn


class GoalRunController:
    """Interactive, session-scoped run-until-done owner for GRUD v1."""

    def __init__(
        self,
        *,
        goal_store: GoalStore,
        run_store: SQLiteGoalRunStore,
        proof_store: AutonomyRunStore | None = None,
    ) -> None:
        self.goal_store = goal_store
        self.run_store = run_store
        self.proof_store = proof_store

    def start_goal_run(
        self,
        *,
        session_id: str,
        goal_id: str,
        caps: GoalRunCaps | None = None,
    ) -> GoalRunState:
        normalized_session = _required(session_id, "session_id")
        normalized_goal = _required(goal_id, "goal_id")
        goal = self.goal_store.get(normalized_goal)
        if goal is None:
            raise KeyError(f"Unknown goal_id: {normalized_goal!r}")
        if not self.goal_store.is_bound_to_session(
            normalized_goal,
            normalized_session,
        ):
            raise ValueError(f"Goal is not active for this session: {normalized_goal}")
        timestamp = goal_now_ms()
        state = GoalRunState(
            run_id=f"grud_{uuid4().hex[:12]}",
            session_id=normalized_session,
            goal_id=normalized_goal,
            started_at_ms=timestamp,
            updated_at_ms=timestamp,
            caps=caps or GoalRunCaps(),
        )
        self.run_store.deactivate_session(normalized_session)
        return self.run_store.save(state)

    def active_state(self, *, session_id: str) -> GoalRunState | None:
        return self.run_store.active_for_session(session_id)

    def stop_session_run(
        self,
        *,
        session_id: str,
        reason: str = "operator_stop",
    ) -> GoalRunState | None:
        state = self.run_store.active_for_session(session_id)
        if state is None:
            return None
        stopped = state.model_copy(
            update={
                "active": False,
                "status": MissionStatus.CANCELLED,
                "last_evaluator_reason": str(reason or "operator_stop").strip(),
                "updated_at_ms": goal_now_ms(),
            }
        )
        return self.run_store.save(stopped)

    def pause_session_run(
        self,
        *,
        session_id: str,
        reason: str = "operator_pause",
    ) -> GoalRunState | None:
        state = self.run_store.active_for_session(session_id)
        if state is None:
            return None
        paused = state.model_copy(
            update={
                "active": False,
                "status": MissionStatus.PAUSED,
                "last_evaluator_reason": str(reason or "operator_pause").strip(),
                "updated_at_ms": goal_now_ms(),
            }
        )
        return self.run_store.save(paused)

    def resume_session_run(
        self,
        *,
        session_id: str,
        reason: str = "operator_resume",
    ) -> GoalRunState | None:
        state = self.run_store.latest_for_session(session_id)
        if state is None or state.active:
            return state
        if state.status not in {MissionStatus.PAUSED, MissionStatus.AWAITING_ASYNC}:
            return None
        resumed = state.model_copy(
            update={
                "active": True,
                "status": MissionStatus.ACTIVE,
                "last_evaluator_reason": str(reason or "operator_resume").strip(),
                "updated_at_ms": goal_now_ms(),
            }
        )
        self.run_store.deactivate_session(session_id, except_run_id=resumed.run_id)
        return self.run_store.save(resumed)

    def record_evaluation(
        self,
        state: GoalRunState,
        evaluation: GoalRunEvaluation,
    ) -> tuple[GoalRunState, GoalContinuationDecision]:
        if evaluation.goal_id != state.goal_id:
            raise ValueError("evaluation goal_id does not match active goal")
        next_turn_count = state.turn_count + 1
        repeated_count = _next_repeated_reason_count(state, evaluation)
        updated = state.model_copy(
            update={
                "turn_count": next_turn_count,
                "last_evaluator_reason": evaluation.reason,
                "latest_evidence_refs": evaluation.evidence_refs,
                "latest_next_instruction": evaluation.next_instruction,
                "repeated_no_progress_count": repeated_count,
                "updated_at_ms": goal_now_ms(),
            }
        )
        decision = self.should_continue(updated, evaluation)
        persisted = updated.model_copy(
            update={
                "status": decision.mission_status,
                "active": decision.should_continue,
            }
        )
        if not decision.should_continue:
            persisted = self._apply_terminal_goal_status(
                persisted,
                reason=decision.stop_reason or evaluation.reason,
            )
        persisted = self.run_store.save(persisted)
        if not decision.should_continue:
            proof = self.finish_goal_run(
                persisted,
                cap_state=decision.cap_state,
                stop_reason=decision.stop_reason or evaluation.reason,
            )
            persisted = self.run_store.save(
                persisted.model_copy(update={"proof_packet_ref": proof.proof_ref})
            )
        return persisted, decision

    def should_continue(
        self,
        state: GoalRunState,
        evaluation: GoalRunEvaluation,
    ) -> GoalContinuationDecision:
        cap_state = self.cap_state(state)
        if evaluation.outcome in _TERMINAL_OUTCOMES:
            return GoalContinuationDecision(
                should_continue=False,
                mission_status=evaluation.mission_status,
                stop_reason=evaluation.reason,
                cap_state=cap_state,
            )
        cap_stop = self._cap_stop_reason(state, cap_state)
        if cap_stop:
            return GoalContinuationDecision(
                should_continue=False,
                mission_status=MissionStatus.PAUSED,
                stop_reason=cap_stop,
                cap_state=cap_state,
            )
        prompt = build_continuation_prompt(state, evaluation)
        return GoalContinuationDecision(
            should_continue=True,
            mission_status=MissionStatus.ACTIVE,
            continuation_prompt=prompt,
            cap_state=cap_state,
        )

    def cap_state(self, state: GoalRunState) -> GoalRunCapState:
        elapsed = max(0, (goal_now_ms() - state.started_at_ms) // 1000)
        return GoalRunCapState(
            max_auto_turns=state.caps.max_auto_turns,
            turns_used=state.turn_count,
            max_wall_clock_seconds=state.caps.max_wall_clock_seconds,
            elapsed_seconds=elapsed,
            token_cost_cap=state.caps.token_cost_cap,
            user_interrupt_enabled=state.caps.user_interrupt_enabled,
            repeated_no_progress_limit=state.caps.repeated_no_progress_limit,
            repeated_no_progress_count=state.repeated_no_progress_count,
        )

    def run_replay(
        self,
        *,
        session_id: str,
        goal_id: str,
        evaluations: tuple[GoalRunEvaluation, ...],
        caps: GoalRunCaps | None = None,
    ) -> GoalRunState:
        state = self.start_goal_run(session_id=session_id, goal_id=goal_id, caps=caps)
        for evaluation in evaluations:
            state, decision = self.record_evaluation(state, evaluation)
            if not decision.should_continue:
                return state
        if state.active:
            paused = state.model_copy(
                update={
                    "active": False,
                    "status": MissionStatus.PAUSED,
                    "last_evaluator_reason": "replay_evaluations_exhausted",
                    "updated_at_ms": goal_now_ms(),
                }
            )
            return self.run_store.save(paused)
        return state

    def finish_goal_run(
        self,
        state: GoalRunState,
        *,
        cap_state: GoalRunCapState | None = None,
        stop_reason: str = "",
    ) -> GoalRunProofAdapter:
        packet = self._build_autonomy_proof(
            state,
            cap_state=cap_state or self.cap_state(state),
            stop_reason=stop_reason,
        )
        proof_ref = _proof_ref(packet)
        if self.proof_store is not None:
            run = _autonomy_run_for_state(state, packet.status)
            if self.proof_store.get(run.run_id) is None:
                self.proof_store.create(run)
            proof_ref = str(self.proof_store.write_proof_packet(packet))
        return GoalRunProofAdapter(
            autonomy_proof=packet,
            session_id=state.session_id,
            final_goal_status=state.status,
            turn_count=state.turn_count,
            latest_evaluator_reason=state.last_evaluator_reason,
            cap_state=cap_state or self.cap_state(state),
            evidence_refs=state.latest_evidence_refs,
            proof_ref=proof_ref,
        )

    def _cap_stop_reason(
        self,
        state: GoalRunState,
        cap_state: GoalRunCapState,
    ) -> str:
        if cap_state.turns_used >= state.caps.max_auto_turns:
            return "cap:max_auto_turns"
        if cap_state.elapsed_seconds >= state.caps.max_wall_clock_seconds:
            return "cap:max_wall_clock_seconds"
        if state.repeated_no_progress_count >= state.caps.repeated_no_progress_limit:
            return "cap:repeated_no_progress"
        return ""

    def _apply_terminal_goal_status(
        self,
        state: GoalRunState,
        *,
        reason: str,
    ) -> GoalRunState:
        if state.status == MissionStatus.CANCELLED:
            return state
        self.goal_store.transition_status(state.goal_id, state.status, reason=reason)
        return state

    def _build_autonomy_proof(
        self,
        state: GoalRunState,
        *,
        cap_state: GoalRunCapState,
        stop_reason: str,
    ) -> AutonomyProofPacket:
        run_status = _autonomy_status_for_goal_status(state.status)
        run = _autonomy_run_for_state(state, run_status)
        command = CommandEvidence(
            command=("openminion", "goal", "run"),
            cwd_ref="session:" + state.session_id,
            started_at_ms=state.started_at_ms,
            ended_at_ms=state.updated_at_ms,
            exit_code=0,
            status=EvidenceStatus.SUCCEEDED
            if run_status == AutonomyRunStatus.COMPLETED
            else EvidenceStatus.BLOCKED,
            summary=(
                f"goal run stopped: status={state.status.value}; "
                f"turns={state.turn_count}; reason={stop_reason}"
            ),
        )
        return build_terminal_proof_packet(
            run,
            validation_summary=(
                f"GRUD terminal proof: session_id={state.session_id}; "
                f"goal_status={state.status.value}; turns={state.turn_count}; "
                f"cap_state={cap_state.model_dump_json()}"
            ),
            final_operator_summary=state.last_evaluator_reason or stop_reason,
            commands_run=(command,),
            artifact_refs=(f"session:{state.session_id}", *state.latest_evidence_refs),
        )


def build_continuation_prompt(
    state: GoalRunState,
    evaluation: GoalRunEvaluation,
) -> str:
    """Build the short structural prompt for the next automatic turn."""

    return build_goal_run_continuation_prompt(
        goal_id=state.goal_id,
        evaluator_outcome=evaluation.outcome,
        reason=evaluation.reason,
        evidence_refs=evaluation.evidence_refs,
        next_instruction=evaluation.next_instruction,
    )


def parse_replay_evaluations(
    goal_id: str,
    raw: str,
) -> tuple[GoalRunEvaluation, ...]:
    """Parse deterministic outcome:reason entries for local/e2e GRUD tests."""

    entries = [part.strip() for part in str(raw or "").split(",") if part.strip()]
    evaluations: list[GoalRunEvaluation] = []
    for entry in entries:
        outcome, sep, reason = entry.partition(":")
        normalized = outcome.strip()
        if not sep or normalized not in _OUTCOME_TO_STATUS:
            raise ValueError(
                "replay evaluations must use outcome:reason entries; "
                "outcome must be one of " + ",".join(sorted(_OUTCOME_TO_STATUS))
            )
        evaluations.append(
            GoalRunEvaluation(
                goal_id=goal_id,
                outcome=cast(GoalRunOutcome, normalized),
                mission_status=_OUTCOME_TO_STATUS[normalized],
                reason=reason.strip() or normalized,
                next_instruction="continue bounded goal work"
                if normalized == "continue"
                else "",
            )
        )
    return tuple(evaluations)


def render_goal_run_status(state: GoalRunState | None) -> str:
    if state is None:
        return "No active goal run for this session."
    cap_state = _cap_state_for_render(state)
    lines = [
        f"run={state.run_id}",
        f"goal={state.goal_id}",
        f"status={state.status.value}",
        f"turns={state.turn_count}/{state.caps.max_auto_turns}",
        f"elapsed={cap_state.elapsed_seconds}s/{state.caps.max_wall_clock_seconds}s",
        f"latest_reason={state.last_evaluator_reason or '-'}",
        f"cap_state={cap_state.model_dump_json()}",
    ]
    if state.latest_evidence_refs:
        lines.append("evidence_refs=" + ",".join(state.latest_evidence_refs))
    if state.proof_packet_ref:
        lines.append("proof=" + state.proof_packet_ref)
    return "\n".join(lines)


def _cap_state_for_render(state: GoalRunState) -> GoalRunCapState:
    return GoalRunCapState(
        max_auto_turns=state.caps.max_auto_turns,
        turns_used=state.turn_count,
        max_wall_clock_seconds=state.caps.max_wall_clock_seconds,
        elapsed_seconds=max(0, (goal_now_ms() - state.started_at_ms) // 1000),
        token_cost_cap=state.caps.token_cost_cap,
        user_interrupt_enabled=state.caps.user_interrupt_enabled,
        repeated_no_progress_limit=state.caps.repeated_no_progress_limit,
        repeated_no_progress_count=state.repeated_no_progress_count,
    )


def format_goal_focus_segment(state: GoalRunState | None) -> str:
    if state is None or not state.active:
        return ""
    reason = state.last_evaluator_reason or "started"
    if len(reason) > 48:
        reason = reason[:45].rstrip() + "..."
    return f"goal: {state.status.value} turn {state.turn_count} · {reason}"


_OUTCOME_TO_STATUS: dict[str, MissionStatus] = {
    "satisfied": MissionStatus.COMPLETED,
    "continue": MissionStatus.ACTIVE,
    "blocked": MissionStatus.PAUSED,
    "needs_user": MissionStatus.PAUSED,
    "awaiting_async": MissionStatus.AWAITING_ASYNC,
    "halted": MissionStatus.HALTED,
}


def _next_repeated_reason_count(
    state: GoalRunState,
    evaluation: GoalRunEvaluation,
) -> int:
    if evaluation.outcome != "continue":
        return 0
    if evaluation.reason and evaluation.reason == state.last_evaluator_reason:
        return state.repeated_no_progress_count + 1
    return 1


def _autonomy_status_for_goal_status(status: MissionStatus) -> AutonomyRunStatus:
    if status == MissionStatus.COMPLETED:
        return AutonomyRunStatus.COMPLETED
    if status == MissionStatus.CANCELLED:
        return AutonomyRunStatus.CANCELLED
    if status in {MissionStatus.PAUSED, MissionStatus.AWAITING_ASYNC}:
        return AutonomyRunStatus.BLOCKED
    if status == MissionStatus.HALTED:
        return AutonomyRunStatus.FAILED
    return AutonomyRunStatus.BLOCKED


def _autonomy_run_for_state(
    state: GoalRunState,
    status: AutonomyRunStatus,
) -> AutonomyRun:
    return AutonomyRun(
        run_id=state.run_id,
        goal_id=state.goal_id,
        goal_text=state.goal_id,
        session_id=state.session_id,
        status=status,
        phase=AutonomyRunPhase.CLOSED,
        continuation_policy=ContinuationPolicy(
            max_iterations=state.caps.max_auto_turns,
            max_wall_clock_ms=state.caps.max_wall_clock_seconds * 1000,
        ),
        permission_profile_id="local-safe",
        last_error=AutonomyRunError(
            code="GOAL_RUN_STOPPED",
            message=state.last_evaluator_reason,
        )
        if status not in {AutonomyRunStatus.COMPLETED, AutonomyRunStatus.CANCELLED}
        else None,
        operator_summary=state.last_evaluator_reason or None,
        created_at_ms=state.started_at_ms,
        updated_at_ms=state.updated_at_ms,
        completed_at_ms=state.updated_at_ms,
    )


def _proof_ref(packet: AutonomyProofPacket) -> str | None:
    if not packet.run_id:
        return None
    return f"awrk-proof:{packet.run_id}"


def _required(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


__all__ = [
    "GoalContinuationDecision",
    "GoalRunCapState",
    "GoalRunCaps",
    "GoalRunController",
    "GoalRunEvaluation",
    "GoalRunOutcome",
    "GoalRunProofAdapter",
    "GoalRunState",
    "SQLiteGoalRunStore",
    "build_continuation_prompt",
    "format_goal_focus_segment",
    "parse_replay_evaluations",
    "render_goal_run_status",
]
