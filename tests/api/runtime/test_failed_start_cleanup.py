"""A failed `APIRuntime` start closes what it built, at the owner that built it.

Each case fails one step of a SQLite runtime start with automatic garbage
collection off, so collection releases nothing. Right after the caller gets the
original exception, each object an owner built is closed once by that owner and
nothing else is closed; an object several owners use is closed by its owner. No
file under the runtime home is open (on Windows each can also be renamed and
deleted) and no runtime manager thread is alive. `APIRuntime.close()` runs only
once the runtime object exists.
"""

from __future__ import annotations

import sqlite3
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from openminion import APIRuntime
from openminion.api import runtime as runtime_module
from openminion.api.core import bootstrap, infrastructure
from openminion.modules.llm.providers.factory import RuntimeLLMHandle
from openminion.modules.memory.config import ConfigError
from openminion.modules.memory.runtime.assembly import RuntimeMemoryAssembly
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl
from openminion.modules.storage import engine as storage_engine
from openminion.modules.storage.engine import StorageEngine
from openminion.modules.storage.record_store import RecordStoreSQLite
from openminion.modules.storage.runtime import context as storage_context
from openminion.modules.storage.runtime.context import RuntimeStorageContext
from openminion.modules.storage.runtime.migrations import MigrationError
from openminion.modules.telemetry.service import TelemetryService
from openminion.modules.tool.authoring import ToolAuthoringService
from openminion.services.agent.service import AgentService
from openminion.services.gateway.service import GatewayService
from openminion.services.runtime.manager import AgentRuntimeManager
from openminion.tools.ops.service import OpsService
from tests._failed_start_scenarios import assert_released, automatic_gc_off
from tests._runtime_storage_backend_scenarios import (
    start_runtime,
    state_db,
    write_runtime_config,
)

_COUNTED_CLOSES = (
    (RuntimeStorageContext, "close", "runtime_storage"),
    (StorageEngine, "close", "storage_engine"),
    (RecordStoreSQLite, "close", "record_store"),
    (TelemetryService, "close_sync", "telemetry_service"),
    (PolicyCtl, "close", "action_policy"),
    (OpsService, "close", "ops_service"),
    (RetrieveCtl, "close", "retrieve_ctl"),
    (RuntimeMemoryAssembly, "close", "memory_assembly"),
    (ToolAuthoringService, "close", "authored_tools"),
    (RuntimeLLMHandle, "close", "llm_runtime"),
    (AgentService, "close", "agent"),
    (GatewayService, "close", "gateway"),
    (APIRuntime, "close", "runtime"),
    (AgentRuntimeManager, "shutdown", "runtime_manager"),
)

# What each step adds to the owners that have something to close.
_STORAGE = {"record_store", "telemetry_service"}
_ENGINE = _STORAGE | {"storage_engine"}
_RUNTIME_STORAGE = _ENGINE | {"runtime_storage"}
_OPS = _RUNTIME_STORAGE | {"llm_runtime", "action_policy"}
_RETRIEVE = _OPS | {"ops_service", "retrieve_ctl"}
_MEMORY = _RETRIEVE | {"memory_assembly"}
_SUPPORT = _MEMORY | {"authored_tools"}
_AGENT = _SUPPORT | {"agent"}
_INFRASTRUCTURE_AND_STACK = _AGENT | {"gateway"}
_RUNTIME = _INFRASTRUCTURE_AND_STACK | {"runtime"}
_STARTED = _RUNTIME | {"runtime_manager"}


class _Injected(RuntimeError):
    """The error a case injects at one step of the start."""


class _Closes:
    """Every counted close call, with the object it closed."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def assert_each_closed_once(self, owners: set[str]) -> None:
        assert {label for label, _ in self.calls} == owners
        per_object = Counter((label, id(target)) for label, target in self.calls)
        # The agent also closes the LLM handle it was given, as on a start
        # that succeeds and is closed; every other object is closed once.
        llm_closes = 2 if "agent" in owners else 1
        for (label, _), count in per_object.items():
            assert count == (llm_closes if label == "llm_runtime" else 1), label


@pytest.fixture(autouse=True)
def _no_automatic_gc() -> Iterator[None]:
    with automatic_gc_off():
        yield


@pytest.fixture
def closes(monkeypatch: pytest.MonkeyPatch) -> _Closes:
    recorded = _Closes()

    def counted(label: str, original: Callable[..., Any]) -> Callable[..., Any]:
        def close(self: object, *args: Any, **kwargs: Any) -> Any:
            # Keeping the object keeps its id unique for the per-object count.
            recorded.calls.append((label, self))
            return original(self, *args, **kwargs)

        return close

    for owner, name, label in _COUNTED_CLOSES:
        monkeypatch.setattr(owner, name, counted(label, getattr(owner, name)))
    return recorded


def _failing(error: BaseException) -> Callable[..., Any]:
    def fail(*_args: object, **_kwargs: object) -> Any:
        raise error

    return fail


def _sessions_table_exists(root: Path) -> None:
    """A runtime database with a `sessions` table: migration v1 fails on it."""
    state_db(root).parent.mkdir(parents=True)
    connection = sqlite3.connect(state_db(root))
    try:
        connection.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()


# Each case: how the step fails, given the monkeypatch, the runtime home and the
# injected error, and which owners then have something to close.
_Step = Callable[[pytest.MonkeyPatch, Path, Callable[..., Any]], None]
_CASES: dict[str, tuple[_Step, set[str]]] = {
    "storage-engine-emitter-start": (
        lambda mp, root, fail: mp.setattr(
            storage_engine, "_start_pool_health_emitter", fail
        ),
        _STORAGE,
    ),
    "runtime-storage-migration": (
        lambda mp, root, fail: mp.setattr(storage_context, "migrate_database", fail),
        _ENGINE,
    ),
    "infrastructure-extension-runtime": (
        lambda mp, root, fail: mp.setattr(
            infrastructure.LifecycleService, "build", fail
        ),
        _RUNTIME_STORAGE,
    ),
    "infrastructure-ops-service-after-action-policy": (
        lambda mp, root, fail: mp.setattr(
            infrastructure, "configured_ops_service", fail
        ),
        _OPS,
    ),
    "infrastructure-tool-authoring-after-memory-assembly": (
        lambda mp, root, fail: mp.setattr(
            infrastructure, "build_tool_authoring_service", fail
        ),
        _MEMORY,
    ),
    "infrastructure-after-support": (
        lambda mp, root, fail: mp.setattr(
            infrastructure, "build_channel_authenticity_policy", fail
        ),
        _SUPPORT,
    ),
    "stack-gateway-after-agent": (
        lambda mp, root, fail: mp.setattr(bootstrap, "build_gateway_service", fail),
        _AGENT,
    ),
    "construction-sqlite-connection-read": (
        lambda mp, root, fail: mp.setattr(
            RuntimeStorageContext, "connection", property(fail)
        ),
        _INFRASTRUCTURE_AND_STACK,
    ),
    "construction-post-init": (
        lambda mp, root, fail: mp.setattr(
            runtime_module, "initialize_runtime_components", fail
        ),
        _INFRASTRUCTURE_AND_STACK,
    ),
    "after-construction-runtime-manager": (
        lambda mp, root, fail: mp.setattr(bootstrap, "build_runtime_manager", fail),
        _RUNTIME,
    ),
    "after-construction-bind-runtime-handle": (
        lambda mp, root, fail: mp.setattr(
            APIRuntime, "_bind_runtime_handle", staticmethod(fail)
        ),
        _STARTED,
    ),
}


@pytest.mark.parametrize("case", sorted(_CASES))
def test_failed_start_closes_what_each_owner_built(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    closes: _Closes,
    case: str,
) -> None:
    step, owners = _CASES[case]
    root = tmp_path / "root"
    config_path = write_runtime_config(root)
    injected = _Injected(case)
    step(monkeypatch, root, _failing(injected))
    threads = set(threading.enumerate())

    with pytest.raises(_Injected) as excinfo:
        start_runtime(config_path)

    assert excinfo.value is injected
    closes.assert_each_closed_once(owners)
    assert_released(root, threads)


@pytest.mark.parametrize(
    ("case", "error", "owners"),
    [
        pytest.param("memory-provider", ConfigError, _RETRIEVE, id="memory-provider"),
        pytest.param(
            "sessions-table-exists", MigrationError, _ENGINE, id="sessions-table-exists"
        ),
    ],
)
def test_failed_start_from_its_configuration_closes_what_was_built(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    closes: _Closes,
    case: str,
    error: type[Exception],
    owners: set[str],
) -> None:
    root = tmp_path / "root"
    config_path = write_runtime_config(root)
    if case == "memory-provider":
        # The memory assembly reads it after the retrieve service exists.
        monkeypatch.setenv("OPENMINION_MEMORY_PROVIDER", "not-a-provider")
    else:
        _sessions_table_exists(root)
    threads = set(threading.enumerate())

    with pytest.raises(error):
        start_runtime(config_path)

    closes.assert_each_closed_once(owners)
    assert_released(root, threads)


@pytest.mark.parametrize(
    "case",
    ["stack-gateway-after-agent", "after-construction-bind-runtime-handle"],
)
def test_failed_start_keeps_its_error_when_a_close_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    closes: _Closes,
    case: str,
) -> None:
    step, owners = _CASES[case]
    root = tmp_path / "root"
    config_path = write_runtime_config(root)
    injected = _Injected(case)
    step(monkeypatch, root, _failing(injected))
    counted_close = RuntimeStorageContext.close

    def close_then_fail(self: RuntimeStorageContext) -> None:
        counted_close(self)
        raise OSError("runtime storage close failed")

    monkeypatch.setattr(RuntimeStorageContext, "close", close_then_fail)
    threads = set(threading.enumerate())

    with pytest.raises(_Injected) as excinfo:
        start_runtime(config_path)

    assert excinfo.value is injected
    # The closes after the runtime storage's still ran.
    closes.assert_each_closed_once(owners)
    assert_released(root, threads)


def test_successful_start_hands_everything_to_the_runtime(
    tmp_path: Path, closes: _Closes
) -> None:
    root = tmp_path / "root"
    config_path = write_runtime_config(root)
    threads = set(threading.enumerate())

    runtime = start_runtime(config_path)
    try:
        assert closes.calls == []
        [cache_key] = runtime._agent_services
        assert runtime._agent_services == {cache_key: runtime.agent}
        assert runtime._gateways == {cache_key: runtime.gateway}
        assert runtime._memory_assemblies == {
            cache_key: runtime.runtime_memory_assembly
        }
        assert runtime._agent_runtime_modes == {cache_key: runtime._runtime_mode}
        assert isinstance(runtime.runtime_manager, AgentRuntimeManager)
        assert runtime.agent._runtime_handle is runtime
    finally:
        runtime.close()

    closes.assert_each_closed_once(_STARTED)
    assert_released(root, threads)
