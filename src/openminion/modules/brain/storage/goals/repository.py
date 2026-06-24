"""SQLite-backed repository for persisted goals."""

import json
from datetime import datetime, timezone

from openminion.modules.brain.schemas import (
    ExternalBlocker,
    Goal,
    GoalStatus,
    LifecycleAuditRecord,
    build_operator_cancelled_failure_condition,
    validate_goal_status_transition,
)
from openminion.modules.brain.schemas.goals import GoalDriftSignal
from openminion.modules.storage.record_store import RecordStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqlGoalRepository:
    """Record-store repository for `Goal` persistence."""

    def __init__(self, store: RecordStore):
        self._store = store

    def create(self, goal: Goal) -> Goal:
        payload = json.dumps(goal.model_dump(mode="json"))
        now = _utc_now()
        self._store.execute_count(
            """
            INSERT INTO goals (
                goal_id, status, description, parent_goal_id, apd_plan_id,
                goal_json, created_at, updated_at, latest_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                goal.goal_id,
                str(goal.status),
                goal.description,
                goal.parent_goal_id,
                goal.apd_plan_id,
                payload,
                now,
                now,
                "",
            ),
        )
        return goal

    def _persist_goal(self, goal: Goal, *, latest_reason: str = "") -> Goal:
        self._store.execute_count(
            """
            UPDATE goals
               SET status = ?, description = ?, parent_goal_id = ?, apd_plan_id = ?,
                   goal_json = ?, updated_at = ?, latest_reason = ?
             WHERE goal_id = ?
            """,
            (
                goal.status.value,
                goal.description,
                goal.parent_goal_id,
                goal.apd_plan_id,
                json.dumps(goal.model_dump(mode="json")),
                _utc_now(),
                str(latest_reason or "").strip(),
                goal.goal_id,
            ),
        )
        return goal

    def get(self, goal_id: str) -> Goal | None:
        rows = self._store.query_dicts(
            "SELECT goal_json FROM goals WHERE goal_id = ?",
            (goal_id,),
        )
        if not rows:
            return None
        return Goal.model_validate(json.loads(rows[0]["goal_json"]))

    def list_active(self) -> list[Goal]:
        rows = self._store.query_dicts(
            """
            SELECT goal_json
              FROM goals
             WHERE status IN (?, ?, ?)
             ORDER BY updated_at DESC
            """,
            (
                GoalStatus.ACTIVE.value,
                GoalStatus.PAUSED.value,
                GoalStatus.AWAITING_ASYNC.value,
            ),
        )
        return [Goal.model_validate(json.loads(row["goal_json"])) for row in rows]

    def list_by_parent(self, parent_goal_id: str) -> list[Goal]:
        rows = self._store.query_dicts(
            """
            SELECT goal_json
              FROM goals
             WHERE parent_goal_id = ?
             ORDER BY updated_at DESC
            """,
            (str(parent_goal_id or "").strip(),),
        )
        return [Goal.model_validate(json.loads(row["goal_json"])) for row in rows]

    def list_by_plan_id(self, plan_id: str) -> list[Goal]:
        rows = self._store.query_dicts(
            """
            SELECT goal_json
              FROM goals
             WHERE apd_plan_id = ?
             ORDER BY updated_at DESC
            """,
            (str(plan_id or "").strip(),),
        )
        return [Goal.model_validate(json.loads(row["goal_json"])) for row in rows]

    def _record_audit(
        self,
        *,
        goal_id: str,
        prior_status: str | None,
        new_status: str | None,
        reason: str = "",
        actor: str = "system",
        action_authorization: dict[str, object] | None = None,
    ) -> None:
        self._store.execute_count(
            """
            INSERT INTO goal_audit_trail(
                entity_kind,
                entity_id,
                goal_id,
                mission_id,
                timestamp,
                prior_status,
                new_status,
                reason,
                actor,
                action_authorization_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "goal",
                goal_id,
                goal_id,
                None,
                _utc_now(),
                str(prior_status or "").strip() or None,
                str(new_status or "").strip() or None,
                str(reason or "").strip(),
                str(actor or "").strip() or "system",
                json.dumps(dict(action_authorization or {})),
            ),
        )

    def transition_status(
        self,
        goal_id: str,
        new_status: GoalStatus | str,
        reason: str = "",
    ) -> Goal:
        goal = self.get(goal_id)
        if goal is None:
            raise KeyError(f"Unknown goal_id: {goal_id!r}")
        normalized_status = validate_goal_status_transition(goal.status, new_status)
        updated = goal.model_copy(update={"status": normalized_status})
        persisted = self._persist_goal(updated, latest_reason=reason)
        self._record_audit(
            goal_id=goal.goal_id,
            prior_status=goal.status.value,
            new_status=normalized_status.value,
            reason=reason,
        )
        return persisted

    def replace(self, goal: Goal, *, reason: str = "") -> Goal:
        current = self.get(goal.goal_id)
        if current is None:
            raise KeyError(f"Unknown goal_id: {goal.goal_id!r}")
        persisted = self._persist_goal(goal, latest_reason=reason)
        if current.status != goal.status:
            self._record_audit(
                goal_id=goal.goal_id,
                prior_status=current.status.value,
                new_status=goal.status.value,
                reason=reason,
            )
        return persisted

    def set_apd_plan_id(self, goal_id: str, plan_id: str) -> Goal:
        goal = self.get(goal_id)
        if goal is None:
            raise KeyError(f"Unknown goal_id: {goal_id!r}")
        updated = goal.model_copy(
            update={"apd_plan_id": str(plan_id or "").strip() or None}
        )
        return self._persist_goal(updated)

    def set_owner(self, goal_id: str, owner_agent_id: str | None) -> Goal:
        goal = self.get(goal_id)
        if goal is None:
            raise KeyError(f"Unknown goal_id: {goal_id!r}")
        updated = goal.model_copy(
            update={"owner_agent_id": str(owner_agent_id or "").strip() or None}
        )
        return self._persist_goal(updated)

    def pause(self, goal_id: str, *, reason: str = "") -> Goal:
        return self.transition_status(goal_id, GoalStatus.PAUSED, reason=reason)

    def resume(self, goal_id: str, *, reason: str = "") -> Goal:
        return self.transition_status(goal_id, GoalStatus.ACTIVE, reason=reason)

    def abort(self, goal_id: str, *, reason: str = "") -> Goal:
        goal = self.get(goal_id)
        if goal is None:
            raise KeyError(f"Unknown goal_id: {goal_id!r}")
        updated = goal.model_copy(
            update={
                "status": validate_goal_status_transition(
                    goal.status,
                    GoalStatus.CANCELLED,
                ),
                "failure_conditions": [
                    *goal.failure_conditions,
                    build_operator_cancelled_failure_condition(
                        goal_id=goal.goal_id,
                        reason=reason,
                    ),
                ],
            }
        )
        persisted = self._persist_goal(updated, latest_reason=reason)
        self._record_audit(
            goal_id=goal.goal_id,
            prior_status=goal.status.value,
            new_status=GoalStatus.CANCELLED.value,
            reason=reason or "operator_cancelled",
        )
        return persisted

    def add_external_blocker(self, goal_id: str, blocker: ExternalBlocker) -> Goal:
        goal = self.get(goal_id)
        if goal is None:
            raise KeyError(f"Unknown goal_id: {goal_id!r}")
        updated = goal.model_copy(
            update={"external_blockers": [*goal.external_blockers, blocker]}
        )
        return self._persist_goal(
            updated, latest_reason=f"blocker:{blocker.blocker_id}"
        )

    def clear_external_blocker(self, goal_id: str, blocker_id: str) -> Goal:
        goal = self.get(goal_id)
        if goal is None:
            raise KeyError(f"Unknown goal_id: {goal_id!r}")
        normalized = str(blocker_id or "").strip()
        updated = goal.model_copy(
            update={
                "external_blockers": [
                    blocker
                    for blocker in goal.external_blockers
                    if blocker.blocker_id != normalized
                ]
            }
        )
        return self._persist_goal(
            updated, latest_reason=f"blocker_cleared:{normalized}"
        )

    def record_drift_signal_audit(self, signal: GoalDriftSignal) -> None:
        """Record drift signal audit helper."""

        evidence_payload: dict[str, object] = {
            "signal_id": signal.signal_id,
            "kind": signal.kind,
            "evidence": dict(signal.evidence),
        }
        self._record_audit(
            goal_id=signal.goal_id,
            prior_status=None,
            new_status=None,
            reason=f"mrdd_drift:{signal.kind}:{signal.description}",
            actor="mrdd_drift_detector",
            action_authorization=evidence_payload,
        )

    def list_goal_audit_trail(self, goal_id: str) -> list[LifecycleAuditRecord]:
        rows = self._store.query_dicts(
            """
            SELECT entity_kind, entity_id, timestamp, prior_status, new_status, reason,
                   actor, action_authorization_json
              FROM goal_audit_trail
             WHERE entity_kind = 'goal' AND entity_id = ?
             ORDER BY timestamp ASC, audit_id ASC
            """,
            (goal_id,),
        )
        return [
            LifecycleAuditRecord(
                entity_kind="goal",
                entity_id=str(row["entity_id"]),
                timestamp=str(row["timestamp"]),
                prior_status=row.get("prior_status"),
                new_status=row.get("new_status"),
                reason=str(row.get("reason", "") or ""),
                actor=str(row.get("actor", "") or ""),
                action_authorization=json.loads(
                    str(row.get("action_authorization_json", "") or "{}")
                ),
            )
            for row in rows
        ]

    def transfer_owner(
        self,
        goal_id: str,
        *,
        from_agent: str,
        to_agent: str,
        reason: str,
    ) -> Goal:
        goal = self.set_owner(goal_id, to_agent)
        self._record_audit(
            goal_id=goal_id,
            prior_status=goal.status.value,
            new_status=goal.status.value,
            reason=reason,
            actor=str(to_agent or "").strip() or "system",
            action_authorization={
                "transfer_owner": True,
                "from_agent": str(from_agent or "").strip(),
                "to_agent": str(to_agent or "").strip(),
            },
        )
        return goal
