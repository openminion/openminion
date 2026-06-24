from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.loop.strategies.research import (
    RESEARCH_MODE,
    ResearchMode,
)
from openminion.modules.brain.runner.cron_resume.handler import (
    resolve_cron_resume_selection,
    schedule_backoff_resume,
)
from openminion.modules.brain.runner.cron_resume.linker import DefaultCronJobLinker
from openminion.modules.brain.runner.cron_resume.policies import (
    ExponentialBackoffResumePolicy,
)
from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    Plan,
    WorkingState,
)
from openminion.modules.session.storage.repository import create_sqlite_cron_repository
from openminion.modules.task import TaskLifecycleState, TaskManager


@dataclass
class _FakeRunner:
    task_manager: TaskManager
    profile: Any = field(
        default_factory=lambda: SimpleNamespace(agent_id="router-agent")
    )


@dataclass
class _FakeServices:
    runner: _FakeRunner
    statuses: list[dict[str, Any]]
    plan_calls: list[str]

    def save_state(self, *, state: WorkingState) -> None:
        del state

    def emit_phase_status(self, *, state: WorkingState, **kwargs) -> None:
        del state
        self.statuses.append(dict(kwargs))

    def respond_with_meta(
        self,
        *,
        state: WorkingState,
        logger: Any,
        message: str,
        status: str,
        action_result: ActionResult | None = None,
        kind: str = "assistant",
    ):
        del logger
        state.status = status
        if action_result is not None:
            state.last_result = action_result
        return SimpleNamespace(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=action_result,
            kind=kind,
        )

    def direct_response(self, *, user_input, decision):
        del user_input, decision
        return ""

    def plan(self, *, state, user_input, logger, decision=None):
        del state, logger, decision
        text = str(user_input or "")
        self.plan_calls.append(text)
        if "gather_sources" in text:
            return Plan(objective="Candidate sources identified.", steps=[])
        if "read_sources" in text:
            return Plan(objective="Key details extracted from sources.", steps=[])
        if "synthesize" in text:
            return Plan(objective="Findings synthesized into a draft.", steps=[])
        return Plan(objective="Findings refined and checked.", steps=[])

    def approve_command(self, *, state, command, logger):
        del state, logger
        return command

    def act_command(self, *, state, command, logger):
        del state, command, logger
        raise AssertionError("research mode should not call ctx.act_command()")

    def assess_plan_feasibility(self, *, state, user_input, logger):
        del state, user_input, logger
        return None

    def evaluate_meta(self, **kwargs):
        del kwargs
        return None

    def apply_meta_directive(self, **kwargs):
        del kwargs

    def meta_override_response(self, **kwargs):
        del kwargs
        return None

    def meta_tool_restriction_reason(self, *, command, directive):
        del command, directive
        return None

    def command_has_side_effects(self, *, command):
        del command
        return False

    def resolve_verification_mode(self, *, current, candidate):
        return candidate if candidate is not None else current

    def verify(self, *, state, command, action_result, mode, logger):
        del state, command, action_result, mode, logger
        return True

    def improve(self, *, state, report, logger):
        del state, report, logger

    def compact(self, *, state, logger, content=""):
        del state, logger, content

    def evaluate_turn_closure(self, **kwargs):
        del kwargs
        return None

    def apply_closure_judgment(self, *, state, judgment):
        del state, judgment
        return "close"

    def extract_success_memories(self, **kwargs):
        del kwargs
        return []

    def create_task(
        self,
        *,
        session_id: str,
        mode_name: str,
        goal: str,
        agent_id: str | None,
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ):
        return self.runner.task_manager.create_task(
            session_id=session_id,
            mode_name=mode_name,
            goal=goal,
            agent_id=agent_id,
            metadata=metadata,
            task_id=task_id,
        )

    def get_task(self, *, task_id: str):
        return self.runner.task_manager.get_task(task_id)

    def list_open_tasks_for_session(
        self,
        *,
        session_id: str,
        mode_name: str | None = None,
        limit: int = 100,
    ):
        return self.runner.task_manager.list_open_tasks_for_session(
            session_id,
            mode_name=mode_name,
            limit=limit,
        )

    def save_checkpoint(
        self, *, task_id: str, checkpoint_id: str, state: dict[str, Any]
    ) -> None:
        self.runner.task_manager.save_checkpoint(task_id, checkpoint_id, state)

    def get_latest_checkpoint(self, *, task_id: str):
        return self.runner.task_manager.get_latest_checkpoint(task_id)

    def list_checkpoints(self, *, task_id: str):
        return self.runner.task_manager.list_checkpoints(task_id)

    def update_task_progress(self, *, task_id: str, progress: dict[str, Any]) -> None:
        self.runner.task_manager.update_progress(task_id, progress)

    def transition_task(
        self,
        *,
        task_id: str,
        to_state: TaskLifecycleState | str,
        failure_reason: str | None = None,
    ):
        return self.runner.task_manager.transition_task(
            task_id=task_id,
            to_state=to_state,
            failure_reason=failure_reason,
        )


def _manager(tmp_path: Path) -> TaskManager:
    repo = create_sqlite_cron_repository(db_path=tmp_path / "sessions.db")
    return TaskManager.from_cron_repository(repo)


def _state(*, session_id: str = "s-research", ticks: int = 4) -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="router-agent",
        budgets_remaining=BudgetCounters(
            ticks=ticks,
            tool_calls=4,
            a2a_calls=2,
            tokens=4000,
            time_ms=120000,
        ),
    )


def _ctx(task_manager: TaskManager, *, ticks: int = 4, session_id: str = "s-research"):
    services = _FakeServices(
        runner=_FakeRunner(task_manager=task_manager),
        statuses=[],
        plan_calls=[],
    )
    decision = SimpleNamespace(
        mode="research",
        confidence=0.91,
        reason_code="task_backed_research",
        objective="Investigate the cron-backed resume design.",
        sub_intents=[],
        rationale="",
        question=None,
        answer=None,
        plan_hint="",
    )
    logger = SimpleNamespace(emit=lambda *args, **kwargs: None)
    return (
        ExecutionContext(
            state=_state(session_id=session_id, ticks=ticks),
            decision=decision,
            user_input="investigate cron-backed resume design",
            logger=logger,
            options=SimpleNamespace(decompose_cancel_requested=False),
            llm_adapter=None,
            command_executor=SimpleNamespace(),
            _services=services,
        ),
        services,
    )


def _due_delta_seconds(job: dict[str, Any]) -> int:
    due_at = str(job.get("next_due_at") or "")
    parsed = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(
        round(
            (
                parsed.astimezone(timezone.utc) - datetime.now(timezone.utc)
            ).total_seconds()
        )
    )


def test_research_pause_then_cron_resume_completes_and_cleans_up(
    tmp_path: Path,
) -> None:
    task_manager = _manager(tmp_path)
    mode = ResearchMode()
    initial_ctx, _services = _ctx(task_manager, ticks=1, session_id="s-cron-cycle")

    paused = mode.execute(initial_ctx)

    assert paused.status == "waiting_user"
    task_id = str(initial_ctx.state.task_backed_task_id or "")
    record = task_manager.get_task(task_id)
    assert record is not None
    assert record.state == TaskLifecycleState.PAUSED
    linked_job_id = str(record.metadata.get("linked_cron_job_id") or "")
    linked_job = task_manager.get_scheduled_job(linked_job_id)
    assert linked_job is not None
    assert linked_job["payload"]["linked_task_id"] == task_id
    assert linked_job["payload"]["session_id"] == "s-cron-cycle"

    resumed_ctx, _services = _ctx(task_manager, ticks=5, session_id="s-cron-cycle")
    latest = task_manager.get_latest_checkpoint(task_id)
    assert latest is not None
    resumed_ctx.state.task_backed_task_id = task_id
    resumed_ctx.state.task_backed_checkpoint_id = latest[0]
    resumed_ctx.state.resume_task_id_hint = task_id
    resumed_ctx.state.resume_cron_job_id_hint = linked_job_id
    selection = resolve_cron_resume_selection(
        task_manager=task_manager,
        task_id_hint=resumed_ctx.state.resume_task_id_hint,
        cron_job_id_hint=resumed_ctx.state.resume_cron_job_id_hint,
    )
    assert selection.task_id == task_id

    resumed_ctx.state.task_backed_resume_state = mode.resume(resumed_ctx, latest[0])
    completed = mode.execute(resumed_ctx)

    assert completed.status == "done"
    updated = task_manager.get_task(task_id)
    assert updated is not None
    assert updated.state == TaskLifecycleState.DONE
    assert task_manager.get_scheduled_job(linked_job_id) is None


def test_completed_task_cron_resume_double_fire_is_idempotent(tmp_path: Path) -> None:
    task_manager = _manager(tmp_path)
    record = task_manager.create_task(
        session_id="s-cron-double",
        mode_name=RESEARCH_MODE,
        goal="Handle duplicate cron delivery",
        agent_id="agent-a",
    )
    job_id = task_manager.create_cron_job(
        name="duplicate-resume",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={
            "kind": "agentTurn",
            "message": "resume",
            "session_id": "s-cron-double",
        },
        agent_id="agent-a",
        session_target="isolated",
    )
    DefaultCronJobLinker(task_manager=task_manager).link(record.task_id, job_id)

    task_manager.transition_task(task_id=record.task_id, to_state="done")
    assert task_manager.get_scheduled_job(job_id) is None

    first = resolve_cron_resume_selection(
        task_manager=task_manager,
        task_id_hint=record.task_id,
        cron_job_id_hint=job_id,
    )
    second = resolve_cron_resume_selection(
        task_manager=task_manager,
        task_id_hint=record.task_id,
        cron_job_id_hint=job_id,
    )

    assert first.task_id == record.task_id
    assert first.cron_job_id == job_id
    assert first.orphan_cleaned is True
    assert second.task_id == record.task_id
    assert second.cron_job_id == job_id
    assert second.orphan_cleaned is True
    assert task_manager.get_scheduled_job(job_id) is None
    refreshed = task_manager.get_task(record.task_id)
    assert refreshed is not None
    assert "linked_cron_job_id" not in refreshed.metadata


def test_orphaned_cron_resume_hint_self_cleans_and_noops(tmp_path: Path) -> None:
    task_manager = _manager(tmp_path)
    job_id = task_manager.create_cron_job(
        name="orphan-resume",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "resume", "session_id": "s-orphan"},
        agent_id="agent-a",
        session_target="isolated",
    )

    selection = resolve_cron_resume_selection(
        task_manager=task_manager,
        task_id_hint="missing-task",
        cron_job_id_hint=job_id,
    )

    assert selection.task_id is None
    assert selection.orphan_cleaned is True
    assert task_manager.get_scheduled_job(job_id) is None


def test_schedule_backoff_resume_records_attempt_metadata(tmp_path: Path) -> None:
    task_manager = _manager(tmp_path)
    record = task_manager.create_task(
        session_id="s-backoff",
        mode_name=RESEARCH_MODE,
        goal="Investigate source drift",
        agent_id="agent-a",
    )

    job_id = schedule_backoff_resume(
        task_manager=task_manager,
        task_id=record.task_id,
        session_id="s-backoff",
        agent_id="agent-a",
        goal="Investigate source drift",
        mode_name=RESEARCH_MODE,
        interval=timedelta(minutes=2),
        attempt_count=3,
        first_scheduled_at="2026-05-01T00:00:00+00:00",
        extra_metadata={"reason_code": "resume-backoff"},
    )

    updated = task_manager.get_task(record.task_id)
    assert updated is not None
    assert updated.metadata["linked_cron_job_id"] == job_id
    assert updated.metadata["cron_resume_attempt_count"] == 3
    assert updated.metadata["cron_resume_current_interval_s"] == 120
    assert updated.metadata["reason_code"] == "resume-backoff"


def test_schedule_backoff_resume_replaces_existing_job(tmp_path: Path) -> None:
    task_manager = _manager(tmp_path)
    record = task_manager.create_task(
        session_id="s-replace",
        mode_name=RESEARCH_MODE,
        goal="Refresh cron resume",
        agent_id="agent-a",
    )
    first_job_id = task_manager.create_cron_job(
        name="resume-old",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "resume", "session_id": "s-replace"},
        agent_id="agent-a",
        session_target="isolated",
    )
    record.metadata["linked_cron_job_id"] = first_job_id
    task_manager.update_task_metadata(task_id=record.task_id, metadata=record.metadata)

    second_job_id = schedule_backoff_resume(
        task_manager=task_manager,
        task_id=record.task_id,
        session_id="s-replace",
        agent_id="agent-a",
        goal="Refresh cron resume",
        mode_name=RESEARCH_MODE,
        interval=timedelta(minutes=1),
        attempt_count=1,
        first_scheduled_at="2026-05-01T00:00:00+00:00",
    )

    assert second_job_id != first_job_id
    assert task_manager.get_scheduled_job(first_job_id) is None
    assert task_manager.get_scheduled_job(second_job_id) is not None


def test_backoff_policy_schedule_round_trip_stays_near_requested_interval() -> None:
    policy = ExponentialBackoffResumePolicy()
    schedule = policy.initial_schedule(
        type("Record", (), {"state": TaskLifecycleState.PAUSED})(),
        type("Spec", (), {"has_resume": True})(),
    )

    store_schedule = schedule.to_store_schedule(now=datetime.now(timezone.utc))
    assert store_schedule["kind"] == "at"
    delta = _due_delta_seconds({"next_due_at": store_schedule["at"]})
    assert 25 <= delta <= 35
