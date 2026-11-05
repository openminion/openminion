"""Atomic capture bundles are an optional capability of external backends.

A backend without `apply_capture_bundle` is reported with
`CaptureRecoveryUnsupportedError`, the error unsupported capture already uses;
a backend's own capture errors propagate unchanged. The SDK tests run echo
turns on the bundled `reference-sqlite` adapter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sophiagraph.storage import SophiaGraphSqliteStore

from openminion import Agent, APIRuntime
from openminion.base.config import ConfigManager, OpenMinionConfig, save_config
from openminion.modules.brain.adapters.memory.runtime import MemctlAdapter
from openminion.modules.memory.backends.builtin import BackendMemoryStoreAdapter
from openminion.modules.memory.backends.external import (
    ExternalBackendCapabilities,
    REFERENCE_SQLITE_ADAPTER_NAME,
    register_external_backend,
    register_reference_sqlite_backend,
)
from openminion.modules.memory.backends.external import registry
from openminion.modules.memory.errors import CaptureRecoveryUnsupportedError
from openminion.modules.memory.runtime.capture_bundle import (
    CaptureBundleInput,
    CaptureCandidateInput,
)
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage import AuditedMemoryStore
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from tests._csc_fixtures import _csc_install_default_agent

CAPTURING_ADAPTER = "capture-test-implemented"
FAILING_ADAPTER = "capture-test-failing"


class CaptureFailed(RuntimeError):
    """The error a backend's own `apply_capture_bundle` raises in these tests."""


class CapturingReferenceBackend(SophiaGraphSqliteStore):
    """The reference backend with the optional capture method.

    The bundle is applied by the built-in SQLite capture implementation, in a
    store of its own next to the backend's database.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.capture_store = SQLiteMemoryStore(path.with_suffix(".capture.db"))
        self.bundles: list[Any] = []

    def apply_capture_bundle(self, bundle: Any) -> Any:
        self.bundles.append(bundle)
        return self.capture_store.apply_capture_bundle(bundle)


class FailingReferenceBackend(SophiaGraphSqliteStore):
    """The reference backend with a capture method that fails."""

    def apply_capture_bundle(self, bundle: Any) -> Any:
        raise CaptureFailed("backend capture failed")


def _bundle() -> CaptureBundleInput:
    return CaptureBundleInput(
        capture_id="capture:unit",
        root_turn_id="turn-1",
        session_id="session-1",
        agent_id="agent-1",
        candidates=(
            CaptureCandidateInput(
                kind="fact",
                normalized_key="fact:sky",
                title="Sky",
                content="the sky is blue",
            ),
        ),
    )


@pytest.fixture
def adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register the reference adapter and the two test adapters for one test."""
    monkeypatch.setattr(
        registry, "_EXTERNAL_BACKENDS", dict(registry._EXTERNAL_BACKENDS)
    )
    register_reference_sqlite_backend()
    for name, backend_type in (
        (CAPTURING_ADAPTER, CapturingReferenceBackend),
        (FAILING_ADAPTER, FailingReferenceBackend),
    ):

        def factory(*, config: Any, _type: type = backend_type, **_: Any) -> Any:
            return _type(Path(str(config.options["db_path"])))

        register_external_backend(
            name, factory=factory, capabilities=ExternalBackendCapabilities()
        )


def test_adapter_reports_a_backend_without_capture_as_unsupported(
    tmp_path: Path,
) -> None:
    backend = SophiaGraphSqliteStore(tmp_path / "reference.sqlite3")
    assert not callable(getattr(backend, "apply_capture_bundle", None))

    with pytest.raises(CaptureRecoveryUnsupportedError) as raised:
        BackendMemoryStoreAdapter(backend).apply_capture_bundle(_bundle())
    assert raised.value.code == "CAPTURE_RECOVERY_UNSUPPORTED"
    assert "SophiaGraphSqliteStore" in str(raised.value)

    # The memory service over the backend reports it the same way.
    with pytest.raises(CaptureRecoveryUnsupportedError):
        MemoryService(backend=backend).apply_capture_bundle(_bundle())
    # So does the memory API above the service, with the backend's message.
    memctl = MemctlAdapter(MemoryService(backend=backend), agent_id="agent-1")
    with pytest.raises(CaptureRecoveryUnsupportedError, match="SophiaGraphSqliteStore"):
        memctl.apply_capture_bundle(
            capture_id="capture:unit",
            root_turn_id="turn-1",
            session_id="session-1",
            agent_id="agent-1",
            candidates=[],
        )


@pytest.mark.parametrize(
    ("backend_type", "supported"),
    [
        (SophiaGraphSqliteStore, False),
        (CapturingReferenceBackend, True),
        (FailingReferenceBackend, True),
    ],
    ids=["without-capture", "implemented", "failing"],
)
def test_capture_capability_is_forwarded_through_the_store_owners(
    tmp_path: Path, backend_type: type[SophiaGraphSqliteStore], supported: bool
) -> None:
    """Each owner above the backend answers with the backend's own check (#449)."""
    backend = backend_type(tmp_path / "reference.sqlite3")
    adapter = BackendMemoryStoreAdapter(backend)
    owners = {
        "store adapter": adapter,
        "audited store adapter": AuditedMemoryStore(adapter),
        "memory service": MemoryService(backend=backend),
        "memory service over the audited adapter": MemoryService(
            store=AuditedMemoryStore(adapter)
        ),
        "memory API": MemctlAdapter(MemoryService(backend=backend), agent_id="a"),
    }

    answers = {name: owner.supports_capture_bundles() for name, owner in owners.items()}

    assert answers == dict.fromkeys(owners, supported)
    # Asking writes nothing.
    assert getattr(backend, "bundles", []) == []


def test_adapter_forwards_the_bundle_to_a_backend_that_implements_capture(
    tmp_path: Path,
) -> None:
    backend = CapturingReferenceBackend(tmp_path / "reference.sqlite3")
    bundle = _bundle()

    receipt = BackendMemoryStoreAdapter(backend).apply_capture_bundle(bundle)

    assert backend.bundles == [bundle]
    assert receipt.capture_id == "capture:unit"
    assert len(receipt.output_ids) == 1
    # The same bundle again returns the stored receipt, as the built-in path does.
    assert BackendMemoryStoreAdapter(backend).apply_capture_bundle(bundle) == receipt


def test_adapter_lets_an_implemented_capture_fail_with_its_own_error(
    tmp_path: Path,
) -> None:
    backend = FailingReferenceBackend(tmp_path / "reference.sqlite3")

    with pytest.raises(CaptureFailed, match="backend capture failed") as raised:
        BackendMemoryStoreAdapter(backend).apply_capture_bundle(_bundle())
    assert not isinstance(raised.value, CaptureRecoveryUnsupportedError)


def _run_turns(
    root: Path, monkeypatch: pytest.MonkeyPatch, adapter: str | None
) -> dict[str, Any]:
    """Two SDK turns in one session; then the capture events and a recovery pass."""
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
    if adapter is not None:
        memory_config = {
            "store": {"backend": "sqlite", "sqlite_path": str(root / "memory.db")},
            "backend": {
                "provider": "external",
                "external_adapter": adapter,
                "options": {"db_path": str(root / "external.sqlite3")},
            },
        }
        manager.register("memory", lambda **_: memory_config)
    runtime = APIRuntime.from_manager(manager)
    try:
        turns: list[Any] = []
        with Agent(runtime=runtime) as agent:
            for _ in range(2):
                try:
                    turns.append(agent.run("hello", session_id="capture"))
                except Exception as exc:  # noqa: BLE001
                    turns.append(exc)
        sessions = runtime.sessions
        events = [
            event
            for session in sessions.list_sessions(limit=100)
            for event in sessions.list_events(session_id=session.id, limit=1000)
        ]
        outcomes = [e for e in events if e.event_type == "turn.outcome"]
        agent_id = str(outcomes[0].payload["agent_id"]) if outcomes else ""
        # The recovery pass applies each pending intent through the runtime's
        # memory API again; the extractor only counts its calls.
        extractions: list[str] = []
        recovery = runtime.resolve_memory_assembly(agent_id).recover_pending_captures(
            sessions=sessions,
            agent_id=agent_id,
            extract_candidates=lambda *args: extractions.append(args[2]) or [],
            authorize=lambda *_: True,
        )
        return {
            "turns": turns,
            "intent states": [e.payload.get("capture_state") for e in outcomes],
            "results": [e for e in events if e.event_type == "memory.capture.result"],
            "recovery": (recovery.scanned, recovery.recovered, recovery.pending),
            "recovery extractions": len(extractions),
        }
    finally:
        runtime.close()


def test_sdk_turns_with_the_reference_backend_complete_with_capture_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapters: None
) -> None:
    run = _run_turns(tmp_path, monkeypatch, REFERENCE_SQLITE_ADAPTER_NAME)

    for turn in run["turns"]:
        assert not isinstance(turn, Exception), turn
        assert turn.run_state == "completed"
        assert turn.metadata["memory_capture_state"] == "pending"
        assert (
            '"error_code": "CAPTURE_RECOVERY_UNSUPPORTED"'
            in (turn.metadata["memory_capture_bundle_result"])
        )
        assert (
            '"disposition": "pending"' in turn.metadata["memory_capture_bundle_result"]
        )
    # Each turn's capture intent stays pending, with no result, and the
    # recovery pass finds both and leaves them pending without extracting
    # again: the backend cannot commit a bundle (#449).
    assert run["intent states"] == ["pending", "pending"]
    assert run["results"] == []
    assert run["recovery"] == (2, 0, 2)
    assert run["recovery extractions"] == 0


def test_sdk_turns_with_an_external_backend_that_implements_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapters: None
) -> None:
    run = _run_turns(tmp_path, monkeypatch, CAPTURING_ADAPTER)

    for turn in run["turns"]:
        assert not isinstance(turn, Exception), turn
        assert turn.run_state == "completed"
        assert "error_code" not in turn.metadata["memory_capture_bundle_result"]
    assert len(run["results"]) == 2
    assert run["recovery"] == (0, 0, 0)


def test_sdk_turn_lets_an_implemented_backend_capture_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapters: None
) -> None:
    run = _run_turns(tmp_path, monkeypatch, FAILING_ADAPTER)

    for turn in run["turns"]:
        assert isinstance(turn, CaptureFailed)
    assert run["intent states"] == ["pending", "pending"]
    assert run["results"] == []
    # The failure is the backend's, so recovery extracts, then keeps the
    # intents pending.
    assert run["recovery"] == (2, 0, 2)
    assert run["recovery extractions"] == 2


def test_sdk_turns_with_the_default_memory_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run_turns(tmp_path, monkeypatch, None)

    for turn in run["turns"]:
        assert not isinstance(turn, Exception), turn
        assert turn.run_state == "completed"
        assert "error_code" not in turn.metadata["memory_capture_bundle_result"]
    assert len(run["results"]) == 2
    assert run["recovery"] == (0, 0, 0)
