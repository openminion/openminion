"""SQLite-backed repository for mission state persistence."""

import json
from datetime import datetime, timezone

from openminion.modules.brain.constants import MissionStatus
from openminion.modules.brain.schemas import LifecycleAuditRecord, MissionState
from openminion.modules.storage.record_store import RecordStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_ACTIVE_MISSION_STATUSES = (
    MissionStatus.ACTIVE.value,
    MissionStatus.PAUSED.value,
    MissionStatus.AWAITING_ASYNC.value,
)


class SqlMissionStateRepository:
    def __init__(self, store: RecordStore):
        self._store = store

    def create(self, state: MissionState) -> MissionState:
        payload = json.dumps(state.model_dump(mode="json"))
        now = _utc_now()
        self._store.execute_count(
            """
            INSERT INTO mission_states (
                mission_id, status, objective, task_id, mission_json,
                created_at, updated_at, latest_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state.mission_id,
                str(state.status),
                state.objective,
                state.task_id,
                payload,
                now,
                now,
                "",
            ),
        )
        return state

    def _persist_state(
        self,
        state: MissionState,
        *,
        latest_reason: str = "",
    ) -> MissionState:
        self._store.execute_count(
            """
            UPDATE mission_states
               SET status = ?, objective = ?, task_id = ?, mission_json = ?,
                   updated_at = ?, latest_reason = ?
             WHERE mission_id = ?
            """,
            (
                state.status.value,
                state.objective,
                state.task_id,
                json.dumps(state.model_dump(mode="json")),
                _utc_now(),
                str(latest_reason or "").strip(),
                state.mission_id,
            ),
        )
        return state

    def get(self, mission_id: str) -> MissionState | None:
        rows = self._store.query_dicts(
            "SELECT mission_json FROM mission_states WHERE mission_id = ?",
            (mission_id,),
        )
        if not rows:
            return None
        return MissionState.model_validate(json.loads(rows[0]["mission_json"]))

    def list_active(self) -> list[MissionState]:
        rows = self._store.query_dicts(
            """
            SELECT mission_json
              FROM mission_states
             WHERE status IN (?, ?, ?)
             ORDER BY updated_at DESC
            """,
            _ACTIVE_MISSION_STATUSES,
        )
        return [
            MissionState.model_validate(json.loads(row["mission_json"])) for row in rows
        ]

    def _record_audit(
        self,
        *,
        mission_id: str,
        prior_status: str | None,
        new_status: str | None,
        reason: str = "",
        actor: str = "system",
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
                "mission",
                mission_id,
                None,
                mission_id,
                _utc_now(),
                str(prior_status or "").strip() or None,
                str(new_status or "").strip() or None,
                str(reason or "").strip(),
                str(actor or "").strip() or "system",
                "{}",
            ),
        )

    def transition_status(
        self, mission_id: str, new_status, reason: str = ""
    ) -> MissionState:
        state = self.get(mission_id)
        if state is None:
            raise KeyError(f"Unknown mission_id: {mission_id!r}")
        normalized = MissionStatus(str(new_status))
        updated = state.model_copy(update={"status": normalized})
        persisted = self._persist_state(updated, latest_reason=reason)
        self._record_audit(
            mission_id=mission_id,
            prior_status=state.status.value,
            new_status=normalized.value,
            reason=reason,
        )
        return persisted

    def pause(self, mission_id: str, *, reason: str = "") -> MissionState:
        return self.transition_status(mission_id, MissionStatus.PAUSED, reason=reason)

    def resume(self, mission_id: str, *, reason: str = "") -> MissionState:
        return self.transition_status(mission_id, MissionStatus.ACTIVE, reason=reason)

    def abort(self, mission_id: str, *, reason: str = "") -> MissionState:
        return self.transition_status(
            mission_id,
            MissionStatus.CANCELLED,
            reason=reason or "operator_cancelled",
        )

    def list_mission_audit_trail(
        self,
        mission_id: str,
    ) -> list[LifecycleAuditRecord]:
        rows = self._store.query_dicts(
            """
            SELECT entity_kind, entity_id, timestamp, prior_status, new_status, reason,
                   actor, action_authorization_json
              FROM goal_audit_trail
             WHERE entity_kind = 'mission' AND entity_id = ?
             ORDER BY timestamp ASC, audit_id ASC
            """,
            (mission_id,),
        )
        return [
            LifecycleAuditRecord(
                entity_kind="mission",
                entity_id=str(row["entity_id"]),
                timestamp=str(row["timestamp"]),
                prior_status=row.get("prior_status"),
                new_status=row.get("new_status"),
                reason=str(row.get("reason", "") or ""),
                actor=str(row.get("actor", "") or ""),
                action_authorization={},
            )
            for row in rows
        ]
