"""Consolidation cron runs recover pending capture intents, then run their turn (#420).

Each test builds a real `APIRuntime` (echo provider), runs one SDK turn and
then a memory consolidation job through the real `CronTurnExecutor` on the
same runtime: its `AgentRuntimeManager`, session store, agent service, memory
assembly, the daemon's turn request builder and the real
`memory_capture_recovery_allowed` authorization. Recovery uses the agent
service's own capture candidate extractor; the tests only record its calls
and the recovery result.

The turn leaves its capture intent pending in two ways: with the built-in
memory provider when its capture bundle write fails once with
`StoreWriteError`, and with the bundled `reference-sqlite` external adapter,
which has no atomic capture (`CAPTURE_RECOVERY_UNSUPPORTED`, #397). With that
adapter recovery does not call the extractor (#449).

The `_consolidate` tests give the executor a task owner that reports every job
as a `task.consolidate_memory` job. The `test_daemon_*` tests use no such
stand-in (#456): `task.consolidate_memory`'s handler creates the job with the
runtime's own task owner, and the daemon's `attach_cron_scheduler` runs it with
its own cron store, its own `TaskManager` on the daemon's database path, its
`CronTurnExecutor` and turn request builder and a running `CronScheduler`. So
the task kind the daemon reads is the one the handler stored.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any

import pytest

from openminion import Agent, APIRuntime
from openminion.base.config import ConfigManager, OpenMinionConfig, save_config
from openminion.modules.memory.backends.external import (
    REFERENCE_SQLITE_ADAPTER_NAME,
    register_reference_sqlite_backend,
)
from openminion.modules.memory.backends.external import registry
from openminion.modules.memory.errors import StoreWriteError
from openminion.modules.task import TaskLifecycleState, TaskManager
from openminion.modules.task.surface import resolve_task_surface_source
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.runtime.policy import Policy
from openminion.services.runtime.cron.executor import CronTurnExecutor
from openminion.services.runtime.daemon import (
    attach_cron_scheduler,
    build_turn_request,
)
from openminion.tools.task.constants import CONSOLIDATION_PAYLOAD_KEY
from openminion.tools.task.plugin import _h_task_consolidate_memory
from tests._csc_fixtures import _csc_install_default_agent

AGENT_ID = "openminion"
PROVENANCE_MESSAGE = (
    "memory consolidation job is missing trusted task provenance; "
    "it was paused and must be recreated with task.consolidate_memory"
)
TERMINAL_RUN_STATES = {"finished", "failed", "timed_out", "cancelled"}


@dataclass
class CronRun:
    output: dict[str, Any]
    intent: dict[str, Any]
    recovery: list[Any]
    extractor_calls: list[dict[str, Any]] = field(default_factory=list)
    brain_session_api: Any = None
    intent_result: dict[str, Any] | None = None
    cron_turn_outcomes: int = 0
    cron_turn_capture_states: tuple[str, ...] = ()


@pytest.fixture
def reference_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        registry, "_EXTERNAL_BACKENDS", dict(registry._EXTERNAL_BACKENDS)
    )
    register_reference_sqlite_backend()


def _runtime(root: Path, monkeypatch: pytest.MonkeyPatch, *, external: bool) -> Any:
    monkeypatch.setenv("OPENMINION_HOME", str(root / "home"))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(root / "home" / "data"))
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    config.storage.path = str(root / ".openminion" / "state.db")
    save_config(config, str(root / "agents.json"))
    manager = ConfigManager.load(
        str(root / "agents.json"), home_root=root, data_root=root / ".openminion"
    )
    if external:
        memory_config = {
            "store": {"backend": "sqlite", "sqlite_path": str(root / "memory.db")},
            "backend": {
                "provider": "external",
                "external_adapter": REFERENCE_SQLITE_ADAPTER_NAME,
                "options": {"db_path": str(root / "external.sqlite3")},
            },
        }
        manager.register("memory", lambda **_: memory_config)
    return APIRuntime.from_manager(manager)


def _events(runtime: Any, event_type: str) -> list[Any]:
    sessions = runtime.sessions
    return [
        event
        for session in sessions.list_sessions(limit=100)
        for event in sessions.list_events(session_id=session.id, limit=1000)
        if event.event_type == event_type
    ]


def _turn(
    runtime: Any, monkeypatch: pytest.MonkeyPatch, *, fail_capture_write: bool
) -> dict[str, Any]:
    """One SDK turn; returns its `turn.outcome` payload (the capture intent)."""
    with monkeypatch.context() as patch:
        if fail_capture_write:
            memctl = runtime.resolve_memory_assembly(AGENT_ID).memctl

            def failing_write(**_kwargs: Any) -> dict[str, Any]:
                raise StoreWriteError("memory store write failed")

            patch.setattr(memctl, "apply_capture_bundle", failing_write)
        with Agent(runtime=runtime) as agent:
            result = agent.run("remember that I prefer tabs", session_id="capture")
    assert result.run_state == "completed"
    [outcome] = _events(runtime, "turn.outcome")
    return dict(outcome.payload)


@contextmanager
def _recording(
    runtime: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[list[Any], list[dict[str, Any]]]]:
    """Record recovery results and extractor calls during one run.

    The recorders are removed after the run, so the next run records its own.
    """
    service = runtime.resolve_agent_service(AGENT_ID)
    assembly = runtime.resolve_memory_assembly(AGENT_ID)
    recovery: list[Any] = []
    extractor_calls: list[dict[str, Any]] = []
    real_recover = assembly.recover_pending_captures
    real_extract = service.extract_memory_capture_candidates

    def recording_recover(**kwargs: Any) -> Any:
        recovery.append(real_recover(**kwargs))
        return recovery[-1]

    def recording_extract(*args: Any, **kwargs: Any) -> Any:
        extractor_calls.append(kwargs)
        return real_extract(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(assembly, "recover_pending_captures", recording_recover)
        patch.setattr(service, "extract_memory_capture_candidates", recording_extract)
        yield recovery, extractor_calls


def _cron_run(
    runtime: Any,
    intent: dict[str, Any],
    output: dict[str, Any],
    recovery: list[Any],
    extractor_calls: list[dict[str, Any]],
) -> CronRun:
    results = {
        str(event.payload.get("capture_id")): dict(event.payload)
        for event in _events(runtime, "memory.capture.result")
    }
    cron_outcomes = [
        event
        for event in _events(runtime, "turn.outcome")
        if event.payload.get("root_turn_id") != intent["root_turn_id"]
    ]
    return CronRun(
        output=output,
        intent=intent,
        recovery=recovery,
        extractor_calls=extractor_calls,
        brain_session_api=runtime.resolve_agent_service(AGENT_ID)
        ._get_runner()
        .session_api,
        intent_result=results.get(str(intent["capture_id"])),
        cron_turn_outcomes=len(cron_outcomes),
        cron_turn_capture_states=tuple(
            str(event.payload.get("capture_state") or "") for event in cron_outcomes
        ),
    )


def _consolidate(
    runtime: Any,
    monkeypatch: pytest.MonkeyPatch,
    intent: dict[str, Any],
    run_id: str = "consolidation-run",
) -> CronRun:
    executor = CronTurnExecutor(
        runtime=runtime,
        # The consolidation path does not use the cron store.
        cron_store=SimpleNamespace(),
        request_builder=lambda payload, agent_id, context=None, watch_context=None: (
            build_turn_request(
                payload,
                default_agent_id=agent_id,
                trusted_memory_consolidation_context=context,
                trusted_watch_context=watch_context,
            )
        ),
        timeout_s=60,
        task_manager=SimpleNamespace(
            get_task_by_job=lambda _job_id: SimpleNamespace(
                metadata={"kind": "memory_consolidation"}
            )
        ),
    )
    job = {
        "job_id": "consolidation-job",
        "agent_id": AGENT_ID,
        "payload": {
            "kind": "agentTurn",
            "message": "Consolidate memory.",
            CONSOLIDATION_PAYLOAD_KEY: {"target_scope": f"agent:{AGENT_ID}"},
        },
    }
    run = {"run_id": run_id, "due_at": "2026-10-26T00:00:00+00:00"}
    with _recording(runtime, monkeypatch) as (recovery, extractor_calls):
        output = executor.execute(job, run)
    return _cron_run(runtime, intent, output, recovery, extractor_calls)


def _task_owner(runtime: Any) -> TaskManager:
    """The runtime's own task owner, which a turn's task tools use."""
    owner = resolve_task_surface_source(runtime)
    assert isinstance(owner, TaskManager)
    return owner


def _create_consolidation_task(owner: TaskManager, root: Path) -> str:
    """`task.consolidate_memory` through its handler, with the runtime's task owner."""
    workspace = root / "workspace"
    workspace.mkdir(exist_ok=True)
    policy = Policy(
        raw={
            "workspace_root": str(workspace),
            "context_metadata": {"agent_id": AGENT_ID},
            "paths": {
                "read_allow": [str(workspace)],
                "write_allow": [str(workspace)],
                "deny": [],
            },
            "tools": {"allow_prefix": [""]},
        }
    )
    context = RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=root / "tool-run",
        scope="WRITE_SAFE",
        confirm=False,
        task_manager=owner,
    )
    created = _h_task_consolidate_memory(
        {"interval_hours": 12, "batch_limit": 8}, context
    )
    return str(created["task_id"])


def _create_pre_upgrade_consolidation_task(
    owner: TaskManager, root: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    """The same handler as published before #463, which stored no task kind."""
    real_schedule_task = owner.schedule_task

    def without_task_kind(**kwargs: Any) -> Any:
        kwargs.pop("metadata", None)
        return real_schedule_task(**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(owner, "schedule_task", without_task_kind)
        return _create_consolidation_task(owner, root)


@contextmanager
def _daemon(
    runtime: Any, owner: TaskManager, monkeypatch: pytest.MonkeyPatch
) -> Iterator[list[tuple[str, str | None]]]:
    """The daemon's cron scheduler on the runtime, from `attach_cron_scheduler`.

    Yields the task lookups by job made by any `TaskManager` other than the
    runtime's own: (job id, task kind found, or None without a task record).
    """
    lookups: list[tuple[str, str | None]] = []
    real_lookup = TaskManager.get_task_by_job

    def recording_lookup(manager: TaskManager, cron_job_id: str) -> Any:
        task = real_lookup(manager, cron_job_id)
        if manager is not owner:
            kind = None if task is None else task.metadata.get("kind")
            lookups.append((cron_job_id, kind))
        return task

    monkeypatch.setattr(TaskManager, "get_task_by_job", recording_lookup)
    scheduler = attach_cron_scheduler(
        runtime=runtime, daemon_id="daemon-456", tick_seconds=0.05
    )
    assert scheduler is not None
    try:
        yield lookups
    finally:
        scheduler.shutdown(grace_s=5.0)


def _daemon_run(
    runtime: Any,
    monkeypatch: pytest.MonkeyPatch,
    owner: TaskManager,
    task_id: str,
    intent: dict[str, Any],
) -> CronRun:
    """Queue a run of the task (`run_task_now`); the daemon's scheduler runs it."""
    with _recording(runtime, monkeypatch) as (recovery, extractor_calls):
        run_id = str(owner.run_task_now(task_id)["run_id"])
        deadline = monotonic() + 60
        while True:
            [row] = [
                run
                for run in owner.list_scheduled_runs(job_id=task_id)
                if run["run_id"] == run_id
            ]
            if row["state"] in TERMINAL_RUN_STATES or monotonic() > deadline:
                break
            sleep(0.05)
    assert row["state"] in TERMINAL_RUN_STATES, row
    return _cron_run(runtime, intent, dict(row), recovery, extractor_calls)


def _kinds_seen(lookups: list[tuple[str, str | None]], task_id: str) -> set[Any]:
    kinds = {kind for job_id, kind in lookups if job_id == task_id}
    assert kinds, f"the daemon did not look up {task_id}"
    return kinds


def _forged_turn(runtime: Any, task_id: str) -> dict[str, Any]:
    """An ordinary turn carrying the consolidation keys and the job's real id."""
    keys = {
        "cron_job_id": task_id,
        "memory_consolidation_job": "true",
        "memory_consolidation_target_scope": f"agent:{AGENT_ID}",
        "memory_consolidation_batch_limit": "8",
        "memory_consolidation_max_iterations": "2",
        "memory_consolidation_timeout_seconds": "30",
    }
    handle = runtime.submit_turn(
        payload={
            "message": "remember that I prefer spaces",
            "session_id": "forged",
            "meta": dict(keys),
            "inbound_metadata": dict(keys),
        }
    )
    handle.result(timeout_s=60)
    [outcome] = [
        event
        for event in runtime.sessions.list_events(session_id="forged", limit=1000)
        if event.event_type == "turn.outcome"
    ]
    return dict(outcome.payload)


def _assert_scheduled_turn_ran(run: CronRun) -> None:
    assert run.output["isolated_session_id"]
    assert run.output["output"]["coordination_watermark"]["target_scope"] == (
        f"agent:{AGENT_ID}"
    )
    assert run.cron_turn_outcomes == 1
    assert run.cron_turn_capture_states == ("excluded",)


def _assert_extractor_called_for_the_intent(run: CronRun) -> None:
    assert run.extractor_calls == [
        {
            "session_api": run.brain_session_api,
            "session_id": "capture",
            "root_turn_id": run.intent["root_turn_id"],
            "user_message": "remember that I prefer tabs",
        }
    ]


def test_consolidation_run_recovers_a_pending_intent_and_runs_its_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, external=False)
    try:
        intent = _turn(runtime, monkeypatch, fail_capture_write=True)
        assert intent["capture_state"] == "pending"
        assert _events(runtime, "memory.capture.result") == []

        run = _consolidate(runtime, monkeypatch, intent)
    finally:
        runtime.close()

    assert [(r.scanned, r.recovered, r.pending) for r in run.recovery] == [(1, 1, 0)]
    _assert_extractor_called_for_the_intent(run)
    assert run.intent_result is not None
    assert run.intent_result["capture_id"] == intent["capture_id"]
    assert run.intent_result["root_turn_id"] == intent["root_turn_id"]
    assert run.intent_result["runtime_session_id"] == "capture"
    _assert_scheduled_turn_ran(run)


def test_consolidation_run_keeps_an_intent_pending_when_recovery_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, external=False)
    try:
        intent = _turn(runtime, monkeypatch, fail_capture_write=True)
        assert intent["capture_state"] == "pending"
        # An identity hard constraint makes the agent's security policy refuse
        # the memory write that recovery is authorized against.
        service = runtime.resolve_agent_service(AGENT_ID)
        service._security_policy.update_identity_constraints(
            ["NEVER write memory records"]
        )

        run = _consolidate(runtime, monkeypatch, intent)
    finally:
        runtime.close()

    assert [(r.scanned, r.recovered, r.pending) for r in run.recovery] == [(1, 0, 1)]
    assert run.extractor_calls == []
    assert run.intent_result is None
    _assert_scheduled_turn_ran(run)


def test_consolidation_run_keeps_an_unsupported_capture_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference_adapter: None
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, external=True)
    try:
        intent = _turn(runtime, monkeypatch, fail_capture_write=False)
        assert intent["capture_state"] == "pending"

        run = _consolidate(runtime, monkeypatch, intent)
    finally:
        runtime.close()

    assert [(r.scanned, r.recovered, r.pending) for r in run.recovery] == [(1, 0, 1)]
    # The store cannot commit a bundle, so recovery does not extract (#449).
    assert run.extractor_calls == []
    assert run.intent_result is None
    _assert_scheduled_turn_ran(run)


def test_consolidation_runs_recover_an_unsupported_intent_once_the_store_is_capable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference_adapter: None
) -> None:
    """Runs on a store without atomic capture do not extract (#449).

    Three runs on `reference-sqlite` leave the turn's intent pending, unchanged
    and without a result, and call no extractor. The same runtime home reopened
    with the built-in store recovers it in the next run, once. Pending intents
    of the scheduled turns themselves are not counted here (#456).
    """
    runtime = _runtime(tmp_path, monkeypatch, external=True)
    try:
        intent = _turn(runtime, monkeypatch, fail_capture_write=False)
        assert intent["capture_state"] == "pending"
        unsupported = [
            _consolidate(runtime, monkeypatch, intent, run_id=f"consolidation-run-{n}")
            for n in (1, 2, 3)
        ]
        [outcome] = [
            event
            for event in _events(runtime, "turn.outcome")
            if event.payload.get("root_turn_id") == intent["root_turn_id"]
        ]
    finally:
        runtime.close()

    for run in unsupported:
        [recovery] = run.recovery
        assert recovery.recovered == 0
        assert recovery.pending == recovery.scanned >= 1
        assert run.extractor_calls == []
        assert run.intent_result is None
        assert run.output["isolated_session_id"]
    assert dict(outcome.payload) == intent

    reopened = _runtime(tmp_path, monkeypatch, external=False)
    try:
        recovering = _consolidate(
            reopened, monkeypatch, intent, run_id="consolidation-run-4"
        )
        after = _consolidate(
            reopened, monkeypatch, intent, run_id="consolidation-run-5"
        )
        intent_results = [
            event
            for event in _events(reopened, "memory.capture.result")
            if event.payload.get("capture_id") == intent["capture_id"]
        ]
    finally:
        reopened.close()

    [recovery] = recovering.recovery
    assert recovery.recovered == recovery.scanned >= 1
    assert recovery.pending == 0
    assert [
        call
        for call in recovering.extractor_calls
        if call["root_turn_id"] == intent["root_turn_id"]
    ] == [
        {
            "session_api": recovering.brain_session_api,
            "session_id": "capture",
            "root_turn_id": intent["root_turn_id"],
            "user_message": "remember that I prefer tabs",
        }
    ]
    assert recovering.intent_result is not None
    assert [(r.scanned, r.recovered, r.pending) for r in after.recovery] == [(0, 0, 0)]
    assert after.extractor_calls == []
    assert len(intent_results) == 1


def test_consolidation_run_without_pending_intents_runs_its_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, external=False)
    try:
        intent = _turn(runtime, monkeypatch, fail_capture_write=False)
        # The turn applied its own capture: the intent has its result.
        [result] = _events(runtime, "memory.capture.result")
        assert result.payload["capture_id"] == intent["capture_id"]

        run = _consolidate(runtime, monkeypatch, intent)
    finally:
        runtime.close()

    assert [(r.scanned, r.recovered, r.pending) for r in run.recovery] == [(0, 0, 0)]
    assert run.extractor_calls == []
    assert run.intent_result == dict(result.payload)
    _assert_scheduled_turn_ran(run)


def _assert_daemon_ran_the_scheduled_turn(run: CronRun) -> None:
    assert run.output["state"] == "finished"
    assert run.output["isolated_session_id"]
    assert run.output["output"]["coordination_watermark"]["target_scope"] == (
        f"agent:{AGENT_ID}"
    )


def test_daemon_runs_a_task_consolidate_memory_job_with_its_persisted_task_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, external=False)
    try:
        intent = _turn(runtime, monkeypatch, fail_capture_write=True)
        assert intent["capture_state"] == "pending"
        owner = _task_owner(runtime)
        task_id = _create_consolidation_task(owner, tmp_path)
        with _daemon(runtime, owner, monkeypatch) as lookups:
            first = _daemon_run(runtime, monkeypatch, owner, task_id, intent)
            second = _daemon_run(runtime, monkeypatch, owner, task_id, intent)
        forged = _forged_turn(runtime, task_id)
        results = {
            str(event.payload.get("capture_id")): dict(event.payload)
            for event in _events(runtime, "memory.capture.result")
        }
    finally:
        runtime.close()

    # The daemon's own TaskManager read the kind the handler stored.
    assert _kinds_seen(lookups, task_id) == {"memory_consolidation"}
    for run in (first, second):
        _assert_daemon_ran_the_scheduled_turn(run)
    assert first.cron_turn_capture_states == ("excluded",)
    assert second.cron_turn_capture_states == ("excluded", "excluded")
    # The earlier pending capture is recovered once.
    assert [(r.scanned, r.recovered, r.pending) for r in first.recovery] == [(1, 1, 0)]
    _assert_extractor_called_for_the_intent(first)
    assert first.intent_result is not None
    assert [(r.scanned, r.recovered, r.pending) for r in second.recovery] == [(0, 0, 0)]
    assert second.extractor_calls == []
    assert second.intent_result == first.intent_result
    # An ordinary turn with the job's id and the consolidation keys is captured
    # (the echo provider's extraction returns nothing).
    assert forged["capture_state"] == "pending"
    assert results[str(forged["capture_id"])]["disposition"] == "succeeded_no_output"


def test_daemon_run_of_a_task_consolidate_memory_job_keeps_unsupported_work_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference_adapter: None
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, external=True)
    try:
        intent = _turn(runtime, monkeypatch, fail_capture_write=False)
        assert intent["capture_state"] == "pending"
        owner = _task_owner(runtime)
        task_id = _create_consolidation_task(owner, tmp_path)
        with _daemon(runtime, owner, monkeypatch) as lookups:
            runs = [
                _daemon_run(runtime, monkeypatch, owner, task_id, intent)
                for _ in range(2)
            ]
        [outcome] = [
            event
            for event in _events(runtime, "turn.outcome")
            if event.payload.get("root_turn_id") == intent["root_turn_id"]
        ]
    finally:
        runtime.close()

    assert _kinds_seen(lookups, task_id) == {"memory_consolidation"}
    for run in runs:
        _assert_daemon_ran_the_scheduled_turn(run)
        # Only the user turn's intent is pending: the scheduled turns add none.
        assert [(r.scanned, r.recovered, r.pending) for r in run.recovery] == [
            (1, 0, 1)
        ]
        assert run.extractor_calls == []
        assert run.intent_result is None
    assert runs[-1].cron_turn_capture_states == ("excluded", "excluded")
    assert dict(outcome.payload) == intent


def test_daemon_pauses_a_pre_upgrade_consolidation_job_and_keeps_its_pending_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, external=False)
    try:
        intent = _turn(runtime, monkeypatch, fail_capture_write=True)
        assert intent["capture_state"] == "pending"
        owner = _task_owner(runtime)
        legacy_id = _create_pre_upgrade_consolidation_task(owner, tmp_path, monkeypatch)
        with _daemon(runtime, owner, monkeypatch) as lookups:
            legacy = _daemon_run(runtime, monkeypatch, owner, legacy_id, intent)
            legacy_task = owner.get_task(legacy_id)
            legacy_job = owner.get_scheduled_job(legacy_id)
            replacement_id = _create_consolidation_task(owner, tmp_path)
            replacement = _daemon_run(
                runtime, monkeypatch, owner, replacement_id, intent
            )
    finally:
        runtime.close()

    assert _kinds_seen(lookups, legacy_id) == {None}
    assert legacy.output["state"] == "failed"
    assert legacy.output["summary"] == PROVENANCE_MESSAGE
    # No scheduled turn and no recovery: the earlier capture stays pending.
    assert legacy.cron_turn_outcomes == 0
    assert legacy.recovery == []
    assert legacy.extractor_calls == []
    assert legacy.intent_result is None
    assert legacy_task is not None
    assert legacy_task.state == TaskLifecycleState.PAUSED
    assert legacy_job is not None and legacy_job["enabled"] is False
    # Its replacement through task.consolidate_memory recovers it once.
    assert _kinds_seen(lookups, replacement_id) == {"memory_consolidation"}
    _assert_daemon_ran_the_scheduled_turn(replacement)
    assert replacement.cron_turn_capture_states == ("excluded",)
    assert [(r.scanned, r.recovered, r.pending) for r in replacement.recovery] == [
        (1, 1, 0)
    ]
    _assert_extractor_called_for_the_intent(replacement)
    assert replacement.intent_result is not None
