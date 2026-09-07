from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from enum import StrEnum
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from openminion.base.generated_paths import resolve_generated_state_path
from openminion.modules.task.constants import (
    DEFAULT_PROJECT_TURN_TIMEOUT_SECONDS,
    DEFAULT_PROJECT_VERIFICATION_TIMEOUT_SECONDS,
)

_LOCAL_SAFE_PERMISSION_OVERRIDES = {
    "file.copy": "bypass",
    "file.move": "bypass",
    "file.write": "bypass",
}


class _StrictAutonomyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def autonomy_permission_metadata(permission_profile_id: str) -> dict[str, str]:
    profile_id = str(permission_profile_id or "").strip().lower()
    if profile_id == "local-safe":
        return {
            "permission_overrides": json.dumps(
                _LOCAL_SAFE_PERMISSION_OVERRIDES,
                sort_keys=True,
            )
        }
    if profile_id in {"readonly", "bypass"}:
        return {"permission_mode": profile_id}
    return {}


class AutonomyRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AutonomyRunPhase(StrEnum):
    INTAKE = "intake"
    PLAN = "plan"
    EXECUTE = "execute"
    VALIDATE = "validate"
    RECOVER = "recover"
    PROOF = "proof"
    CLOSED = "closed"


class EvidenceStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class TestEvidenceStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class ContinuationPolicy(_StrictAutonomyModel):
    max_iterations: int = Field(default=1, ge=0)
    max_wall_clock_ms: int | None = Field(default=None, ge=1)
    max_tool_calls: int | None = Field(default=None, ge=0)
    resume_on_daemon_restart: bool = False
    require_operator_after_blocked: bool = True
    permission_profile_id: str = "local-safe"


VerificationDomain = Literal[
    "coding",
    "research",
    "operations",
    "cross_application",
]


class AutonomyExecutionSelectors(_StrictAutonomyModel):
    agent_id: str = "default"
    config_ref: str | None = None
    default_act_profile: str | None = None
    verification_domain: VerificationDomain = "cross_application"
    verifier_ref: str = "command"
    verification_commands: tuple[str, ...] = ()
    turn_timeout_seconds: int = Field(
        default=DEFAULT_PROJECT_TURN_TIMEOUT_SECONDS,
        ge=1,
    )
    verification_timeout_seconds: int = Field(
        default=DEFAULT_PROJECT_VERIFICATION_TIMEOUT_SECONDS,
        ge=1,
    )
    verification_waiver_reason: str | None = None
    required_evidence_kinds: tuple[str, ...] = ("verification",)
    budget_policy_id: str = "continuation"


class AutonomyRunError(_StrictAutonomyModel):
    code: str
    message: str
    detail: str | None = None


class AutonomyRun(_StrictAutonomyModel):
    run_id: str
    goal_id: str | None = None
    goal_text: str
    session_id: str
    task_id: str | None = None
    checkpoint_id: str | None = None
    workspace_ref: str | None = None
    status: AutonomyRunStatus = AutonomyRunStatus.QUEUED
    phase: AutonomyRunPhase = AutonomyRunPhase.INTAKE
    continuation_policy: ContinuationPolicy = Field(default_factory=ContinuationPolicy)
    execution_selectors: AutonomyExecutionSelectors = Field(
        default_factory=AutonomyExecutionSelectors
    )
    permission_profile_id: str = "local-safe"
    proof_packet_ref: str | None = None
    last_error: AutonomyRunError | None = None
    parent_run_id: str | None = None
    worker_role: str | None = None
    memory_candidate_refs: tuple[str, ...] = ()
    skill_candidate_refs: tuple[str, ...] = ()
    operator_summary: str | None = None
    next_action_hint: str | None = None
    created_at_ms: int
    updated_at_ms: int
    completed_at_ms: int | None = None


class CommandEvidence(_StrictAutonomyModel):
    command: tuple[str, ...] | str
    cwd_ref: str
    started_at_ms: int
    ended_at_ms: int
    exit_code: int | None = None
    status: EvidenceStatus
    stdout_artifact_ref: str | None = None
    stderr_artifact_ref: str | None = None
    summary: str


class TestEvidence(_StrictAutonomyModel):
    command: tuple[str, ...] | str
    cwd_ref: str
    started_at_ms: int
    ended_at_ms: int
    exit_code: int | None = None
    passed: int | None = None
    failed: int | None = None
    skipped: int | None = None
    status: TestEvidenceStatus
    output_artifact_ref: str | None = None
    summary: str


class VerificationWaiver(_StrictAutonomyModel):
    reason: str = Field(min_length=1)
    recorded_at_ms: int


DelegatedRole = Literal["worker", "explorer", "reviewer"]
DelegatedRoleStatus = Literal["success", "failure", "skipped", "canceled"]


class DelegatedRoleEvidence(_StrictAutonomyModel):
    role: DelegatedRole
    status: DelegatedRoleStatus
    summary: str = Field(min_length=1)


class ContextBudgetEvidence(_StrictAutonomyModel):
    max_tokens: int = Field(ge=1)
    estimated_tokens_before: int = Field(ge=0)
    estimated_tokens_after: int = Field(ge=0)
    trimmed_count: int = Field(ge=0)
    overflow: bool = False
    retained_required_facts: tuple[str, ...] = ()


class AutonomyProofPacket(_StrictAutonomyModel):
    run_id: str
    goal_id: str | None = None
    status: AutonomyRunStatus
    started_at_ms: int
    ended_at_ms: int
    workspace_ref: str | None = None
    checkpoint_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    commands_run: tuple[CommandEvidence, ...] = ()
    tests_run: tuple[TestEvidence, ...] = ()
    validation_summary: str
    final_operator_summary: str
    cycle_summaries: tuple[str, ...] = ()
    next_resume_action: str | None = None
    failure_or_blocker: AutonomyRunError | None = None
    verification_waiver: VerificationWaiver | None = None
    delegation_results: tuple[DelegatedRoleEvidence, ...] = ()
    delegation_aggregation: dict[str, object] | None = None
    context_budget: ContextBudgetEvidence | None = None


_TERMINAL_STATUSES = {
    AutonomyRunStatus.BLOCKED,
    AutonomyRunStatus.FAILED,
    AutonomyRunStatus.COMPLETED,
    AutonomyRunStatus.CANCELLED,
}

_ALLOWED_TRANSITIONS: dict[AutonomyRunStatus, set[AutonomyRunStatus]] = {
    AutonomyRunStatus.QUEUED: {
        AutonomyRunStatus.RUNNING,
        AutonomyRunStatus.CANCELLED,
        AutonomyRunStatus.BLOCKED,
    },
    AutonomyRunStatus.RUNNING: {
        AutonomyRunStatus.WAITING_FOR_APPROVAL,
        AutonomyRunStatus.WAITING_FOR_INPUT,
        AutonomyRunStatus.BLOCKED,
        AutonomyRunStatus.FAILED,
        AutonomyRunStatus.COMPLETED,
        AutonomyRunStatus.CANCELLED,
    },
    AutonomyRunStatus.WAITING_FOR_APPROVAL: {
        AutonomyRunStatus.RUNNING,
        AutonomyRunStatus.CANCELLED,
        AutonomyRunStatus.BLOCKED,
    },
    AutonomyRunStatus.WAITING_FOR_INPUT: {
        AutonomyRunStatus.RUNNING,
        AutonomyRunStatus.CANCELLED,
        AutonomyRunStatus.BLOCKED,
    },
    AutonomyRunStatus.BLOCKED: {AutonomyRunStatus.RUNNING, AutonomyRunStatus.CANCELLED},
    AutonomyRunStatus.FAILED: {AutonomyRunStatus.RUNNING, AutonomyRunStatus.CANCELLED},
    AutonomyRunStatus.COMPLETED: set(),
    AutonomyRunStatus.CANCELLED: set(),
}


def now_ms() -> int:
    return int(time.time() * 1000)


def new_run_id() -> str:
    return f"awrk_{uuid4().hex[:12]}"


def resolve_autonomy_state_root(home_root: str | Path | None = None) -> Path:
    return resolve_generated_state_path("autonomy", module="task", home_root=home_root)


def build_local_workspace_ref(workspace: str | Path) -> str:
    root = Path(workspace).expanduser().resolve(strict=False)
    commit = _git_output(root, "rev-parse", "HEAD") or "unknown"
    status = _git_output(root, "status", "--porcelain")
    dirty = "unknown" if status is None else ("dirty" if status else "clean")
    return f"local:{root}#commit={commit};dirty={dirty}"


def build_autonomy_run(
    *,
    goal_text: str,
    goal_id: str | None,
    session_id: str,
    workspace_ref: str | None,
    max_iterations: int,
    permission_profile_id: str = "local-safe",
    agent_id: str = "default",
    config_ref: str | None = None,
    default_act_profile: str | None = None,
    verification_domain: VerificationDomain = "cross_application",
    verifier_ref: str = "command",
    verification_commands: tuple[str, ...] = (),
    turn_timeout_seconds: int = DEFAULT_PROJECT_TURN_TIMEOUT_SECONDS,
    verification_timeout_seconds: int = DEFAULT_PROJECT_VERIFICATION_TIMEOUT_SECONDS,
    verification_waiver_reason: str | None = None,
    required_evidence_kinds: tuple[str, ...] = ("verification",),
) -> AutonomyRun:
    timestamp = now_ms()
    policy = ContinuationPolicy(
        max_iterations=max_iterations,
        permission_profile_id=permission_profile_id,
    )
    return AutonomyRun(
        run_id=new_run_id(),
        goal_id=goal_id,
        goal_text=goal_text,
        session_id=session_id,
        workspace_ref=workspace_ref,
        continuation_policy=policy,
        execution_selectors=AutonomyExecutionSelectors(
            agent_id=agent_id,
            config_ref=config_ref,
            default_act_profile=default_act_profile,
            verification_domain=verification_domain,
            verifier_ref=verifier_ref,
            verification_commands=verification_commands,
            turn_timeout_seconds=turn_timeout_seconds,
            verification_timeout_seconds=verification_timeout_seconds,
            verification_waiver_reason=verification_waiver_reason,
            required_evidence_kinds=required_evidence_kinds,
        ),
        permission_profile_id=permission_profile_id,
        created_at_ms=timestamp,
        updated_at_ms=timestamp,
    )


class AutonomyRunStore:
    """File-backed v1a autonomy run store under generated task state."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else resolve_autonomy_state_root()
        self.runs_root = self.root / "runs"
        self.proof_root = self.root / "proof"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.proof_root.mkdir(parents=True, exist_ok=True)

    def create(self, run: AutonomyRun) -> AutonomyRun:
        if self._run_path(run.run_id).exists():
            raise ValueError(f"autonomy run already exists: {run.run_id}")
        self.save(run)
        return run

    def save(self, run: AutonomyRun) -> None:
        self.runs_root.mkdir(parents=True, exist_ok=True)
        _write_text_atomic(self._run_path(run.run_id), _dump_model(run))

    def get(self, run_id: str) -> AutonomyRun | None:
        path = self._run_path(run_id)
        if not path.exists():
            return None
        return AutonomyRun.model_validate_json(path.read_text(encoding="utf-8"))

    def require(self, run_id: str) -> AutonomyRun:
        run = self.get(run_id)
        if run is None:
            raise KeyError(f"autonomy run not found: {run_id}")
        return run

    def list_runs(
        self,
        *,
        status: AutonomyRunStatus | None = None,
        limit: int = 50,
    ) -> list[AutonomyRun]:
        runs = [
            AutonomyRun.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.runs_root.glob("*.json")
        ]
        if status is not None:
            runs = [run for run in runs if run.status == status]
        runs.sort(key=lambda run: run.created_at_ms, reverse=True)
        return runs[: max(0, limit)]

    def transition(
        self,
        run_id: str,
        *,
        status: AutonomyRunStatus,
        phase: AutonomyRunPhase | None = None,
        operator_summary: str | None = None,
        next_action_hint: str | None = None,
        error: AutonomyRunError | None = None,
    ) -> AutonomyRun:
        run = self.require(run_id)
        _validate_transition(run.status, status)
        timestamp = now_ms()
        updated = run.model_copy(
            update={
                "status": status,
                "phase": phase or run.phase,
                "operator_summary": operator_summary,
                "next_action_hint": next_action_hint,
                "last_error": error,
                "updated_at_ms": timestamp,
                "completed_at_ms": timestamp
                if status in _TERMINAL_STATUSES
                else run.completed_at_ms,
            }
        )
        self.save(updated)
        return updated

    def write_proof_packet(self, packet: AutonomyProofPacket) -> Path:
        self.proof_root.mkdir(parents=True, exist_ok=True)
        path = self.proof_root / f"{packet.run_id}.json"
        _write_text_atomic(path, _dump_model(packet))
        run = self.require(packet.run_id)
        self.save(run.model_copy(update={"proof_packet_ref": str(path)}))
        return path

    def _run_path(self, run_id: str) -> Path:
        safe = run_id.replace("/", "_").replace("\\", "_")
        return self.runs_root / f"{safe}.json"


def build_terminal_proof_packet(
    run: AutonomyRun,
    *,
    validation_summary: str,
    final_operator_summary: str,
    cycle_summaries: tuple[str, ...] = (),
    commands_run: tuple[CommandEvidence, ...] = (),
    tests_run: tuple[TestEvidence, ...] = (),
    artifact_refs: tuple[str, ...] = (),
    verification_waiver: VerificationWaiver | None = None,
    delegation_results: tuple[DelegatedRoleEvidence, ...] = (),
    delegation_aggregation: dict[str, object] | None = None,
    context_budget: ContextBudgetEvidence | None = None,
) -> AutonomyProofPacket:
    ended_at = run.completed_at_ms or run.updated_at_ms
    return AutonomyProofPacket(
        run_id=run.run_id,
        goal_id=run.goal_id,
        status=run.status,
        started_at_ms=run.created_at_ms,
        ended_at_ms=ended_at,
        workspace_ref=run.workspace_ref,
        checkpoint_refs=(run.checkpoint_id,) if run.checkpoint_id else (),
        artifact_refs=artifact_refs,
        commands_run=commands_run,
        tests_run=tests_run,
        validation_summary=validation_summary,
        final_operator_summary=final_operator_summary,
        cycle_summaries=cycle_summaries,
        next_resume_action=run.next_action_hint,
        failure_or_blocker=run.last_error,
        verification_waiver=verification_waiver,
        delegation_results=delegation_results,
        delegation_aggregation=delegation_aggregation,
        context_budget=context_budget,
    )


def _validate_transition(
    current: AutonomyRunStatus,
    target: AutonomyRunStatus,
) -> None:
    if target == current:
        return
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"invalid autonomy transition: {current} -> {target}")


def _dump_model(model: BaseModel) -> str:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )


def _write_text_atomic(path: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _git_output(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ("git", "-C", str(root), *args),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


__all__ = (
    "AutonomyExecutionSelectors",
    "AutonomyProofPacket",
    "AutonomyRun",
    "AutonomyRunError",
    "AutonomyRunPhase",
    "AutonomyRunStatus",
    "AutonomyRunStore",
    "CommandEvidence",
    "ContinuationPolicy",
    "ContextBudgetEvidence",
    "DelegatedRoleEvidence",
    "EvidenceStatus",
    "TestEvidence",
    "TestEvidenceStatus",
    "VerificationWaiver",
    "autonomy_permission_metadata",
    "build_autonomy_run",
    "build_local_workspace_ref",
    "build_terminal_proof_packet",
    "resolve_autonomy_state_root",
)
