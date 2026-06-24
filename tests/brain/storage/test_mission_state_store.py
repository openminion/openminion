from __future__ import annotations

from openminion.modules.brain.constants import MissionStatus
from openminion.modules.brain.schemas import (
    BudgetCounters,
    MissionBudgetEnvelope,
    MissionState,
)
from openminion.modules.brain.storage.missions import SQLiteMissionStateStore


def _budget() -> MissionBudgetEnvelope:
    counters = BudgetCounters(
        ticks=10,
        tool_calls=10,
        a2a_calls=0,
        tokens=1000,
        time_ms=60_000,
    )
    return MissionBudgetEnvelope(
        total_remaining=counters,
        per_turn_max=counters,
        remaining_llm_calls_total=10,
        llm_calls_per_turn_max=2,
    )


def _state(mission_id: str, *, task_id: str | None = None) -> MissionState:
    return MissionState(
        mission_id=mission_id,
        objective=f"objective {mission_id}",
        task_id=task_id,
        budget=_budget(),
    )


def test_mission_state_store_round_trips_existing_owner_surface(tmp_path) -> None:
    store = SQLiteMissionStateStore(tmp_path / "missions.db")

    created = store.create(_state("mission-1", task_id="task-1"))
    fetched = store.get("mission-1")

    assert fetched is not None
    assert fetched.mission_id == created.mission_id
    assert fetched.status == MissionStatus.ACTIVE
    assert fetched.task_id == "task-1"


def test_mission_state_store_lists_active_and_transitions_status(tmp_path) -> None:
    store = SQLiteMissionStateStore(tmp_path / "missions.db")
    store.create(_state("mission-active"))
    store.create(_state("mission-complete"))

    store.transition_status(
        "mission-complete",
        MissionStatus.COMPLETED,
        reason="all criteria met",
    )

    active_ids = [state.mission_id for state in store.list_active()]
    completed = store.get("mission-complete")

    assert active_ids == ["mission-active"]
    assert completed is not None
    assert completed.status == MissionStatus.COMPLETED


def test_mission_state_store_rejects_unknown_mission(tmp_path) -> None:
    store = SQLiteMissionStateStore(tmp_path / "missions.db")

    try:
        store.transition_status("missing", MissionStatus.PAUSED, reason="pause")
    except KeyError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("expected missing mission transition to raise KeyError")


def test_mission_state_store_records_audit_rows(tmp_path) -> None:
    store = SQLiteMissionStateStore(tmp_path / "missions.db")
    store.create(_state("mission-audit"))
    store.pause("mission-audit", reason="manual_pause")
    store.resume("mission-audit", reason="manual_resume")

    audit = store.list_mission_audit_trail("mission-audit")
    assert [row.reason for row in audit] == ["manual_pause", "manual_resume"]
    assert audit[0].prior_status == MissionStatus.ACTIVE.value
