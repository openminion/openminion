from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import inspect
from pathlib import Path

import pytest

from openminion.modules.brain.interfaces import SkillAPI as BrainSkillAPI
from openminion.modules.skill.config import load_config
from openminion.modules.skill.interfaces import SkillContract
from openminion.modules.skill.learning.runtime import (
    WorkflowObservationError,
    list_runtime_workflow_observations,
    list_runtime_workflow_shapes,
    observe_runtime_workflow,
)
from openminion.modules.skill.storage import SQLiteSkillStore
from openminion.modules.skill.storage.workflow_observations import (
    WorkflowObservationStorageError,
)
from openminion.modules.skill.runtime.skill import Skill


def _store(tmp_path: Path) -> SQLiteSkillStore:
    return SQLiteSkillStore(tmp_path / "skill.db", wal=False)


def _observe(
    store: SQLiteSkillStore,
    *,
    agent_id: str = "agent-a",
    source_run_ref: str = "run-1",
    intent_category: str = "modify",
) -> object:
    return observe_runtime_workflow(
        store,
        agent_id=agent_id,
        source_run_ref=source_run_ref,
        intent_category=intent_category,
        capability_category="code",
        tool_names=["file.write", "exec.run", "file.write"],
    )


def test_runtime_workflow_observation_is_disabled_by_default() -> None:
    assert load_config({"skill": {}}).runtime_workflow_observation_enabled is False


def test_runtime_workflow_observation_flag_requires_boolean() -> None:
    enabled = load_config({"skill": {"runtime_workflow_observation_enabled": True}})
    assert enabled.runtime_workflow_observation_enabled is True
    disabled = load_config({"skill": {"runtime_workflow_observation_enabled": False}})
    assert disabled.runtime_workflow_observation_enabled is False
    for invalid in ("true", 1):
        with pytest.raises(ValueError, match="must be a boolean"):
            load_config({"skill": {"runtime_workflow_observation_enabled": invalid}})


@pytest.mark.parametrize("contract", [Skill, SkillContract, BrainSkillAPI])
def test_skill_observer_contract_is_keyword_only(contract: type[object]) -> None:
    parameters = inspect.signature(contract.observe_workflow).parameters

    assert list(parameters) == [
        "self",
        "agent_id",
        "source_run_ref",
        "intent_category",
        "capability_category",
        "tool_names",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for name, parameter in parameters.items()
        if name != "self"
    )


def test_skill_write_boundary_enforces_opt_in_and_closed_categories(
    tmp_path: Path,
) -> None:
    disabled = Skill(
        {
            "skill": {
                "sqlite_path": str(tmp_path / "disabled.db"),
                "blob_root": str(tmp_path / "disabled-blob"),
                "fallback_root": str(tmp_path / "disabled-fallback"),
                "wal": False,
            }
        }
    )
    try:
        with pytest.raises(WorkflowObservationError, match="disabled"):
            disabled.observe_workflow(
                agent_id="agent-a",
                source_run_ref="run-1",
                intent_category="modify",
                capability_category="code",
                tool_names=["file.write"],
            )
    finally:
        disabled.close()

    store = _store(tmp_path)
    try:
        with pytest.raises(WorkflowObservationError, match="intent category"):
            observe_runtime_workflow(
                store,
                agent_id="agent-a",
                source_run_ref="run-1",
                intent_category="rewrite-user-request",
                capability_category="code",
                tool_names=["file.write"],
            )
        with pytest.raises(WorkflowObservationError, match="capability category"):
            observe_runtime_workflow(
                store,
                agent_id="agent-a",
                source_run_ref="run-1",
                intent_category="modify",
                capability_category="arbitrary-prose",
                tool_names=["file.write"],
            )
    finally:
        store.close()


def test_runtime_observation_is_agent_scoped_and_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        first = _observe(store)
        duplicate = _observe(store)
        other_agent = _observe(store, agent_id="agent-b")

        assert first.status == "observed"
        assert duplicate.status == "duplicate"
        assert duplicate.observation_id == first.observation_id
        assert other_agent.status == "observed"
        assert len(list_runtime_workflow_observations(store, agent_id="agent-a")) == 1
        assert len(list_runtime_workflow_observations(store, agent_id="agent-b")) == 1
    finally:
        store.close()


def test_conflicting_source_run_is_reported_without_overwrite(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        first = _observe(store)
        conflict = _observe(store, intent_category="verify")

        assert conflict.status == "conflict"
        assert conflict.observation_id == first.observation_id
        retained = list_runtime_workflow_observations(store, agent_id="agent-a")
        assert retained[0]["intent_category"] == "intent:modify"
    finally:
        store.close()


def test_second_matching_success_marks_shape_authoring_ready(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        first = _observe(store, source_run_ref="run-1")
        second = _observe(store, source_run_ref="run-2")
        shapes = list_runtime_workflow_shapes(store, agent_id="agent-a")

        assert first.status == "observed"
        assert second.status == "authoring_ready"
        assert second.shape_id == shapes[0].shape_id
        assert second.matching_success_count == 2
        assert shapes[0].risk_level == "medium"
        assert shapes[0].success_count == 2
    finally:
        store.close()


def test_third_success_and_different_shape_keep_truthful_counts(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        _observe(store, source_run_ref="run-1")
        _observe(store, source_run_ref="run-2")
        third = _observe(store, source_run_ref="run-3")
        different = observe_runtime_workflow(
            store,
            agent_id="agent-a",
            source_run_ref="run-4",
            intent_category="verify",
            capability_category="code",
            tool_names=["exec.run"],
        )
        shapes = list_runtime_workflow_shapes(store, agent_id="agent-a")

        assert third.status == "authoring_ready"
        assert third.matching_success_count == 3
        assert different.status == "observed"
        assert sorted(shape.success_count for shape in shapes) == [1, 3]
    finally:
        store.close()


def test_observations_survive_store_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "skill.db"
    store = SQLiteSkillStore(db_path, wal=False)
    _observe(store)
    store.close()

    reopened = SQLiteSkillStore(db_path, wal=False)
    try:
        observations = list_runtime_workflow_observations(reopened, agent_id="agent-a")
        assert [item["source_run_refs"] for item in observations] == [["run-1"]]
    finally:
        reopened.close()


def test_concurrent_same_identity_creates_one_observation(tmp_path: Path) -> None:
    db_path = tmp_path / "skill.db"

    def observe_once() -> str:
        store = SQLiteSkillStore(db_path, wal=True)
        try:
            return str(_observe(store).status)
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(lambda _index: observe_once(), range(2)))

    store = SQLiteSkillStore(db_path, wal=False)
    try:
        assert statuses == ["duplicate", "observed"]
        assert len(list_runtime_workflow_observations(store, agent_id="agent-a")) == 1
    finally:
        store.close()


def test_corrupt_observation_row_fails_explicitly(tmp_path: Path) -> None:
    import sqlite3

    db_path = tmp_path / "skill.db"
    store = SQLiteSkillStore(db_path, wal=False)
    _observe(store)
    store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE skill_workflow_observations SET bundle_json = ?",
            ("{not-json",),
        )

    reopened = SQLiteSkillStore(db_path, wal=False)
    try:
        with pytest.raises(WorkflowObservationError, match="stored.*invalid"):
            list_runtime_workflow_shapes(reopened, agent_id="agent-a")
    finally:
        reopened.close()


def test_runtime_observation_rejects_missing_structural_evidence(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(WorkflowObservationError, match="substantive tool"):
            observe_runtime_workflow(
                store,
                agent_id="agent-a",
                source_run_ref="run-1",
                intent_category="modify",
                capability_category="code",
                tool_names=[],
            )
    finally:
        store.close()


def test_storage_failures_use_the_observation_error_boundary() -> None:
    class _FailingStore:
        def list_workflow_observations(self, *, agent_id: str) -> list[object]:
            del agent_id
            raise WorkflowObservationStorageError("backend unavailable")

    with pytest.raises(WorkflowObservationError, match="storage failed"):
        list_runtime_workflow_observations(
            _FailingStore(),  # type: ignore[arg-type]
            agent_id="agent-a",
        )
