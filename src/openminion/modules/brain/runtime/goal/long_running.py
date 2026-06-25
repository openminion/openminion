"""Runtime helpers for long-running goals and missions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from openminion.base.time import utc_now_iso

from ...checkpoint import CheckpointManager
from ...constants import MissionStatus
from ...loop.tools.task_ops import stable_task_id_for_plan_id
from ...schemas.goals import (
    Goal,
    GoalStatus,
    evaluate_goal_cost_budget,
    goal_has_unresolved_external_blockers,
)
from ...storage.goals import GoalStore
from ...storage.missions import MissionStateStore
from .policy import authorize_goal_action
from .verification import GoalVerificationResult, verify_goal_completion


@dataclass(frozen=True)
class GoalSessionResumeSnapshot:
    goal_id: str
    status: str
    apd_plan_id: str | None
    task_id: str | None
    mission_id: str | None
    checkpoint: dict[str, Any] | None

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "goal_id": self.goal_id,
            "status": self.status,
            "apd_plan_id": self.apd_plan_id,
            "task_id": self.task_id,
            "mission_id": self.mission_id,
        }
        if self.checkpoint is not None:
            payload["checkpoint"] = dict(self.checkpoint)
        return payload


class LongRunningGoalRuntime:
    """Typed lifecycle helper composed under BrainRunner and cron."""

    def __init__(
        self,
        *,
        goal_store: GoalStore,
        mission_store: MissionStateStore,
        checkpoint_manager: CheckpointManager | None = None,
    ) -> None:
        self.goal_store = goal_store
        self.mission_store = mission_store
        self.checkpoint_manager = checkpoint_manager

    def bind_goal_to_session(self, *, goal_id: str, session_id: str) -> Goal:
        """Make ``goal_id`` the current durable goal for ``session_id``."""

        return self.goal_store.bind_to_session(goal_id, session_id, active=True)

    def list_active_goals_for_session(self, session_id: str) -> list[Goal]:
        """Return active goals bound to one session, never the global list."""

        return self.goal_store.list_active_for_session(session_id)

    def hydrate_session_start(
        self,
        *,
        session_id: str,
        session_api: Any | None = None,
    ) -> tuple[GoalSessionResumeSnapshot, ...]:
        missions_by_task = {
            str(mission.task_id or "").strip(): mission
            for mission in self.mission_store.list_active()
            if str(mission.task_id or "").strip()
        }
        snapshots: list[GoalSessionResumeSnapshot] = []
        for goal in self.goal_store.list_active_for_session(session_id):
            task_id = self._task_id_for_goal(goal)
            mission = missions_by_task.get(task_id or "")
            checkpoint = None
            if task_id and self.checkpoint_manager is not None:
                envelope = self.checkpoint_manager.load_envelope(task_id=task_id)
                if envelope is not None:
                    checkpoint = envelope.model_dump(mode="json")
            snapshots.append(
                GoalSessionResumeSnapshot(
                    goal_id=goal.goal_id,
                    status=goal.status.value,
                    apd_plan_id=goal.apd_plan_id,
                    task_id=task_id,
                    mission_id=mission.mission_id if mission is not None else None,
                    checkpoint=checkpoint,
                )
            )
        if session_api is not None and callable(
            getattr(session_api, "append_event", None)
        ):
            session_api.append_event(
                session_id=session_id,
                event_type="goal.resume_context.loaded",
                payload={
                    "goal_count": len(snapshots),
                    "snapshots": [snapshot.as_payload() for snapshot in snapshots],
                },
            )
        return tuple(snapshots)

    def advance_from_cron(
        self,
        *,
        goal_id: str | None,
        mission_id: str | None,
        session_api: Any | None = None,
        session_id: str = "",
    ) -> None:
        if goal_id:
            goal = self.goal_store.get(goal_id)
            if goal is not None and not goal_has_unresolved_external_blockers(goal):
                if goal.status in {GoalStatus.PAUSED, GoalStatus.AWAITING_ASYNC}:
                    self.goal_store.resume(goal.goal_id, reason="cron_advance")
        if mission_id:
            mission = self.mission_store.get(mission_id)
            if mission is not None and mission.status in {
                MissionStatus.PAUSED,
                MissionStatus.AWAITING_ASYNC,
            }:
                self.mission_store.resume(mission.mission_id, reason="cron_advance")
        if (
            session_api is not None
            and session_id
            and callable(getattr(session_api, "append_event", None))
        ):
            session_api.append_event(
                session_id=session_id,
                event_type="goal.cron.advanced",
                payload={
                    "goal_id": str(goal_id or "").strip(),
                    "mission_id": str(mission_id or "").strip(),
                },
            )

    def apply_task_plan_signal(
        self,
        *,
        plan_id: str,
        root_goal_id: str | None,
        terminal_status: str,
        reason: str,
    ) -> Goal | None:
        goal = self.resolve_goal_for_plan(plan_id=plan_id, root_goal_id=root_goal_id)
        if goal is None:
            return None
        if goal.apd_plan_id != plan_id:
            goal = self.goal_store.set_apd_plan_id(goal.goal_id, plan_id)
        if terminal_status == "completed":
            return self.goal_store.transition_status(
                goal.goal_id,
                GoalStatus.COMPLETED,
                reason=reason,
            )
        if terminal_status in {"blocked", "failed"}:
            return self.goal_store.transition_status(
                goal.goal_id,
                GoalStatus.HALTED,
                reason=reason,
            )
        return goal

    def resolve_goal_for_plan(
        self, *, plan_id: str, root_goal_id: str | None
    ) -> Goal | None:
        """Resolve an explicit root goal or the sole goal linked to ``plan_id``."""

        normalized_goal_id = str(root_goal_id or "").strip()
        goal = self.goal_store.get(normalized_goal_id) if normalized_goal_id else None
        if goal is not None:
            return goal
        linked_goals = self.goal_store.list_by_plan_id(plan_id)
        if len(linked_goals) == 1:
            return linked_goals[0]
        return None

    def apply_termination_signal(
        self,
        *,
        goal_id: str,
        reason: str,
        budget_exhausted_terminal: bool = True,
    ) -> Goal | None:
        """Project an operational termination reason onto ``Goal.status``."""

        goal = self.goal_store.get(goal_id)
        if goal is None:
            return None
        status = project_goal_status_for_termination(
            reason,
            budget_exhausted_terminal=budget_exhausted_terminal,
        )
        if status == goal.status:
            return goal
        return self.goal_store.transition_status(goal.goal_id, status, reason=reason)

    def roll_up_child_goals(self, *, parent_goal_id: str) -> Goal | None:
        """Project child-goal terminal state into the parent goal."""

        parent = self.goal_store.get(parent_goal_id)
        if parent is None:
            return None
        children = self.goal_store.list_by_parent(parent.goal_id)
        if not children:
            return parent
        if all(child.status == GoalStatus.COMPLETED for child in children):
            return self.goal_store.transition_status(
                parent.goal_id,
                GoalStatus.COMPLETED,
                reason="child_goal_rollup_completed",
            )
        if any(
            child.status in {GoalStatus.HALTED, GoalStatus.CANCELLED}
            for child in children
        ):
            return self.goal_store.transition_status(
                parent.goal_id,
                GoalStatus.HALTED,
                reason="child_goal_rollup_failed",
            )
        return parent

    def consume_cost(
        self,
        *,
        goal_id: str,
        consumed_tokens: int = 0,
        consumed_dollars: float = 0.0,
    ) -> Goal | None:
        goal = self.goal_store.get(goal_id)
        if goal is None:
            return None
        exhausted = evaluate_goal_cost_budget(
            goal,
            consumed_tokens=consumed_tokens,
            consumed_dollars=consumed_dollars,
        )
        if exhausted is None:
            return goal
        updated = goal.model_copy(
            update={
                "status": GoalStatus.HALTED,
                "failure_conditions": [*goal.failure_conditions, exhausted],
            }
        )
        return self.goal_store.replace(updated, reason="cost_budget")

    def verify_goal_for_cli(
        self,
        *,
        goal_id: str,
        run_id: str,
        state: Any,
        logger: Any,
    ) -> GoalVerificationResult:
        return verify_goal_completion(
            goal_id,
            goals=self.goal_store,
            run_id=run_id,
            state=state,
            logger=logger,
        )

    def authorize_goal_action(
        self,
        *,
        goal_id: str,
        profile_policy: str | None,
        action_type: str | None,
    ) -> dict[str, Any]:
        del goal_id
        authorization = authorize_goal_action(
            profile_policy=profile_policy,
            action_type=action_type,
        )
        return {
            "allowed": authorization.allowed,
            "requires_user_confirm": authorization.requires_user_confirm,
            "reason": authorization.reason,
            "risk_tier": authorization.risk_tier,
        }

    def _task_id_for_goal(self, goal: Goal) -> str | None:
        if not goal.apd_plan_id:
            return None
        try:
            return stable_task_id_for_plan_id(goal.apd_plan_id)
        except ValueError:
            return None


def render_goal_summary(goal: Goal) -> str:
    """Render a compact operator-facing goal line."""

    parts = [f"{goal.goal_id} [{goal.status.value}] {goal.description}"]
    if goal.apd_plan_id:
        parts.append(f"plan={goal.apd_plan_id}")
    if goal.owner_agent_id:
        parts.append(f"owner={goal.owner_agent_id}")
    if goal.external_blockers:
        parts.append(f"blockers={len(goal.external_blockers)}")
    return " | ".join(parts)


def render_goal_verification(goal_id: str, result: GoalVerificationResult) -> str:
    """Render the typed goal verification result for the CLI."""

    lines = [f"goal={goal_id}", f"status={result.status}"]
    if result.unmet_criteria:
        lines.append("unmet_criteria=" + ",".join(result.unmet_criteria))
    if result.missing_deliverables:
        lines.append("missing_deliverables=" + ",".join(result.missing_deliverables))
    if result.triggered_failures:
        lines.append(
            "triggered_failures="
            + ",".join(item.condition_id for item in result.triggered_failures)
        )
    lines.append(f"verified_at={utc_now_iso()}")
    return "\n".join(lines)


TerminationReason = Literal[
    "verified_closeout",
    "needs_user",
    "job_pending",
    "budget_exhausted",
    "tool_failure",
    "explicit_cancel",
]


def project_goal_status_for_termination(
    reason: str,
    *,
    budget_exhausted_terminal: bool = True,
) -> GoalStatus:
    """Map operational loop termination reasons onto the canonical status enum."""

    normalized = str(reason or "").strip()
    if normalized in {"verified_closeout", "completed", "success"}:
        return GoalStatus.COMPLETED
    if normalized in {"needs_user", "waiting_user", "paused"}:
        return GoalStatus.PAUSED
    if normalized in {"job_pending", "awaiting_external", "awaiting_async"}:
        return GoalStatus.AWAITING_ASYNC
    if normalized == "budget_exhausted" and not budget_exhausted_terminal:
        return GoalStatus.ACTIVE
    if normalized in {"budget_exhausted", "tool_failure", "failed", "halted"}:
        return GoalStatus.HALTED
    if normalized in {"explicit_cancel", "cancelled", "canceled"}:
        return GoalStatus.CANCELLED
    return GoalStatus.HALTED


__all__ = [
    "GoalSessionResumeSnapshot",
    "LongRunningGoalRuntime",
    "TerminationReason",
    "project_goal_status_for_termination",
    "render_goal_summary",
    "render_goal_verification",
]
