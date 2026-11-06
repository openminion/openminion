import json
import sqlite3
from typing import Any

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from sophiagraph.storage import SophiaGraphSqliteStore

from openminion.modules.memory.runtime.capture_bundle import (
    CaptureBundleInput,
    CaptureBundleIntegrityError,
    CaptureCandidateInput,
)
from openminion.modules.memory.runtime.capture_recovery import (
    recover_pending_capture_bundles,
)
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite import SQLiteMemoryStore
from openminion.modules.memory.storage import (
    AuditedMemoryStore,
    CandidateListOptions,
    InMemoryMemoryStore,
)
from openminion.modules.brain.adapters.memory.runtime import MemctlAdapter
from openminion.modules.brain.runner.coordinator import BrainRunner
from openminion.modules.session.capture import (
    RuntimeTerminalCaptureWriter,
    TerminalCaptureReceiptError,
    build_capture_identity,
    terminal_capture_is_enabled,
    verify_terminal_capture_receipt,
)
from openminion.modules.storage.runtime.migrations import run_migrations
from openminion.modules.storage.runtime.session_store import SessionStore


def _bundle(*, capture_id: str, content: str = "Ada prefers concise answers"):
    return CaptureBundleInput(
        capture_id=capture_id,
        root_turn_id="root-1",
        session_id="session-1",
        agent_id="agent-1",
        candidates=(
            CaptureCandidateInput(
                kind="user_preference",
                normalized_key="user_preference:response_style",
                title="Response style",
                content=content,
            ),
        ),
    )


def test_capture_identity_is_root_bound_and_deterministic():
    first = build_capture_identity(
        runtime_session_id="session-1", root_turn_id="root-1"
    )
    replay = build_capture_identity(
        runtime_session_id="session-1", root_turn_id="root-1"
    )
    later = build_capture_identity(
        runtime_session_id="session-1", root_turn_id="root-2"
    )

    assert first == replay
    assert first.capture_id != later.capture_id
    assert first.event_id != later.event_id


def test_runtime_diagnostic_turns_skip_durable_terminal_capture():
    assert terminal_capture_is_enabled({"diagnostic_scope": "runtime"}) is False
    assert terminal_capture_is_enabled({}) is True


def test_sqlite_capture_bundle_is_atomic_idempotent_and_conflict_checked(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db", artifactctl=None)
    first = store.apply_capture_bundle(_bundle(capture_id="capture-1"))
    replay = store.apply_capture_bundle(_bundle(capture_id="capture-1"))

    assert first == replay
    assert first.disposition == "succeeded"
    assert len(first.output_ids) == 1
    assert store.candidate_get(first.output_ids[0]) is not None
    with pytest.raises(CaptureBundleIntegrityError):
        store.apply_capture_bundle(
            _bundle(capture_id="capture-1", content="Different report")
        )


def test_sqlite_capture_bundle_records_zero_output_receipt(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db", artifactctl=None)
    bundle = CaptureBundleInput(
        capture_id="capture-empty",
        root_turn_id="root-empty",
        session_id="session-1",
        agent_id="agent-1",
        candidates=(),
    )

    receipt = store.apply_capture_bundle(bundle)

    assert receipt.disposition == "succeeded_no_output"
    assert receipt.output_ids == ()


def test_gateway_verifies_persisted_terminal_receipt_before_delivery():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    sessions = SessionStore(connection)
    sessions.resolve_session(
        agent_id="agent-1",
        channel="console",
        target="local",
        session_id="session-1",
    )
    identity = build_capture_identity(
        runtime_session_id="session-1", root_turn_id="root-1"
    )
    receipt = RuntimeTerminalCaptureWriter(sessions).commit_terminal_capture_intent(
        identity=identity,
        event_payload={"status": "done", "step_output_count": 1},
    )
    metadata = {"terminal_capture_intent_receipt": json.dumps(receipt.as_payload())}

    verify_terminal_capture_receipt(
        sessions=sessions,
        response_metadata=metadata,
        session_id="session-1",
        run_id="root-1",
    )
    metadata["terminal_capture_intent_receipt"] = json.dumps(
        {**receipt.as_payload(), "capture_id": "wrong"}
    )
    with pytest.raises(TerminalCaptureReceiptError):
        verify_terminal_capture_receipt(
            sessions=sessions,
            response_metadata=metadata,
            session_id="session-1",
            run_id="root-1",
        )
    with pytest.raises(TerminalCaptureReceiptError):
        verify_terminal_capture_receipt(
            sessions=sessions,
            response_metadata={},
            session_id="session-1",
            run_id="root-1",
        )


def test_pending_capture_recovery_reuses_atomic_bundle_and_releases_hold(tmp_path):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    sessions = SessionStore(connection)
    sessions.resolve_session(
        agent_id="agent-1",
        channel="console",
        target="local",
        session_id="session-1",
    )
    identity = build_capture_identity(
        runtime_session_id="session-1", root_turn_id="root-1"
    )
    sessions.append_message(
        session_id="session-1",
        role="inbound",
        body="My name is Ada",
        metadata={"run_id": "root-1"},
    )
    RuntimeTerminalCaptureWriter(sessions).commit_terminal_capture_intent(
        identity=identity,
        event_payload={
            "status": "done",
            "agent_id": "agent-1",
        },
    )
    memory_store = SQLiteMemoryStore(tmp_path / "memory.db", artifactctl=None)
    memctl = MemctlAdapter(
        MemoryService(store=memory_store, owns_store=False),
        agent_id="agent-1",
    )

    result = recover_pending_capture_bundles(
        sessions=sessions,
        memctl=memctl,
        agent_id="agent-1",
        authorize=lambda *_args: True,
        extract_candidates=lambda _sessions, _session_id, _root_turn_id, user_message: [
            {
                "kind": "fact",
                "normalized_key": "fact:user_name",
                "title": "User name",
                "content": user_message,
                "tags": [],
                "confidence": 0.7,
            }
        ],
    )

    assert result.recovered == 1
    assert result.pending == 0
    assert (
        sessions.get_event_by_canonical_id(
            f"memory.capture.result:{identity.capture_id}"
        )
        is not None
    )
    assert memory_store.candidate_list(CandidateListOptions(limit=10))


def test_recovery_scans_past_sessions_without_pending_events(tmp_path):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    sessions = SessionStore(connection)
    sessions.resolve_session(
        agent_id="agent-1",
        channel="console",
        target="empty",
        session_id="session-empty",
    )
    sessions.resolve_session(
        agent_id="agent-1",
        channel="console",
        target="pending",
        session_id="session-pending",
    )
    identity = build_capture_identity(
        runtime_session_id="session-pending", root_turn_id="root-pending"
    )
    sessions.append_message(
        session_id="session-pending",
        role="inbound",
        body="Remember Helix",
        metadata={"run_id": "root-pending"},
    )
    RuntimeTerminalCaptureWriter(sessions).commit_terminal_capture_intent(
        identity=identity,
        event_payload={"status": "done", "agent_id": "agent-1"},
    )
    memory_store = SQLiteMemoryStore(tmp_path / "memory.db", artifactctl=None)

    result = recover_pending_capture_bundles(
        sessions=sessions,
        memctl=MemctlAdapter(
            MemoryService(store=memory_store, owns_store=False),
            agent_id="agent-1",
        ),
        agent_id="agent-1",
        limit=1,
        authorize=lambda *_args: True,
        extract_candidates=lambda *_args: [],
    )

    assert result.scanned == 1
    assert result.recovered == 1
    assert result.pending == 0


def test_recovery_revalidates_policy_before_reading_capture_source(tmp_path) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    sessions = SessionStore(connection)
    sessions.resolve_session(
        agent_id="agent-1",
        channel="console",
        target="local",
        session_id="session-1",
    )
    identity = build_capture_identity(
        runtime_session_id="session-1", root_turn_id="root-1"
    )
    RuntimeTerminalCaptureWriter(sessions).commit_terminal_capture_intent(
        identity=identity,
        event_payload={"status": "done", "agent_id": "agent-1"},
    )
    extractor = MagicMock(return_value=[])

    result = recover_pending_capture_bundles(
        sessions=sessions,
        memctl=MemctlAdapter(
            MemoryService(
                store=SQLiteMemoryStore(tmp_path / "memory.db", artifactctl=None),
                owns_store=False,
            ),
            agent_id="agent-1",
        ),
        agent_id="agent-1",
        authorize=lambda *_args: False,
        extract_candidates=extractor,
    )

    assert result.scanned == 1
    assert result.recovered == 0
    assert result.pending == 1
    extractor.assert_not_called()


def test_recovery_rejects_noncanonical_persisted_identity(tmp_path) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    sessions = SessionStore(connection)
    sessions.resolve_session(
        agent_id="agent-1",
        channel="console",
        target="local",
        session_id="session-1",
    )
    sessions.append_event(
        session_id="session-1",
        event_type="turn.outcome",
        canonical_event_id="turn.outcome:tampered",
        payload={
            "agent_id": "agent-1",
            "capture_id": "capture:tampered",
            "root_turn_id": "root-1",
            "capture_state": "pending",
        },
    )
    extractor = MagicMock(return_value=[])

    result = recover_pending_capture_bundles(
        sessions=sessions,
        memctl=MemctlAdapter(
            MemoryService(
                store=SQLiteMemoryStore(tmp_path / "memory.db", artifactctl=None),
                owns_store=False,
            ),
            agent_id="agent-1",
        ),
        agent_id="agent-1",
        authorize=lambda *_args: True,
        extract_candidates=extractor,
    )

    assert result.scanned == 1
    assert result.recovered == 0
    assert result.pending == 1
    extractor.assert_not_called()


def _pending_intents(*root_turn_ids: str) -> SessionStore:
    """One session with a pending capture intent, and its source, per root turn."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    sessions = SessionStore(connection)
    sessions.resolve_session(
        agent_id="agent-1",
        channel="console",
        target="local",
        session_id="session-1",
    )
    for root_turn_id in root_turn_ids:
        sessions.append_message(
            session_id="session-1",
            role="inbound",
            body=f"Remember {root_turn_id}",
            metadata={"run_id": root_turn_id},
        )
        RuntimeTerminalCaptureWriter(sessions).commit_terminal_capture_intent(
            identity=build_capture_identity(
                runtime_session_id="session-1", root_turn_id=root_turn_id
            ),
            event_payload={"status": "done", "agent_id": "agent-1"},
        )
    return sessions


class _LoggingSessions:
    """The session store; logs source message reads and result writes."""

    _LOGGED = {
        "list_messages": "source",
        "commit_capture_result_and_release_hold": "result",
    }

    def __init__(self, sessions: SessionStore, log: list[str]) -> None:
        self._sessions = sessions
        self._log = log

    def __getattr__(self, name: str) -> Any:
        if name in self._LOGGED:
            self._log.append(self._LOGGED[name])
        return getattr(self._sessions, name)


class _LoggingMemctl:
    """A memory API without `supports_capture_bundles`; logs bundle writes."""

    def __init__(self, memctl: MemctlAdapter, log: list[str]) -> None:
        self._memctl = memctl
        self._log = log

    def apply_capture_bundle(self, **kwargs: Any) -> dict[str, Any]:
        self._log.append("bundle")
        return self._memctl.apply_capture_bundle(**kwargs)


class _LoggingCapabilityMemctl(_LoggingMemctl):
    """The same memory API with the capability predicate; logs its queries."""

    def supports_capture_bundles(self) -> bool:
        self._log.append("capability")
        return self._memctl.supports_capture_bundles()


def _recover(
    sessions: SessionStore,
    memctl: MemctlAdapter,
    *,
    wrapper: type[_LoggingMemctl] = _LoggingCapabilityMemctl,
    denied: tuple[str, ...] = (),
) -> tuple[tuple[int, int, int], dict[str, list[str]]]:
    """One recovery pass: its counts, and what followed each authorization."""
    log: list[str] = []

    def authorize(_session_id: str, root_turn_id: str, *_ids: str) -> bool:
        log.append(f"authorize {root_turn_id}")
        return root_turn_id not in denied

    def extract(
        _sessions: Any, _session_id: str, root_turn_id: str, user_message: str
    ) -> list[dict[str, Any]]:
        log.append("extract")
        return [
            {
                "kind": "fact",
                "normalized_key": f"fact:{root_turn_id}",
                "title": "Remembered",
                "content": user_message,
                "tags": [],
                "confidence": 0.7,
            }
        ]

    result = recover_pending_capture_bundles(
        sessions=_LoggingSessions(sessions, log),
        memctl=wrapper(memctl, log),
        agent_id="agent-1",
        authorize=authorize,
        extract_candidates=extract,
    )
    steps_by_intent: dict[str, list[str]] = {}
    steps = steps_by_intent.setdefault("", [])
    for entry in log:
        if entry.startswith("authorize "):
            steps = steps_by_intent.setdefault(entry.removeprefix("authorize "), [])
        else:
            steps.append(entry)
    return (result.scanned, result.recovered, result.pending), {
        root_turn_id: steps
        for root_turn_id, steps in steps_by_intent.items()
        if root_turn_id or steps
    }


def _external_backend_without_capture(tmp_path) -> MemoryService:
    # The runtime's memory service for the `reference-sqlite` external backend.
    return MemoryService(backend=SophiaGraphSqliteStore(tmp_path / "reference.sqlite3"))


def _audited_store_without_capture(_tmp_path) -> MemoryService:
    return MemoryService(store=AuditedMemoryStore(InMemoryMemoryStore()))


@pytest.mark.parametrize(
    "service_for",
    [_external_backend_without_capture, _audited_store_without_capture],
    ids=["external-backend", "audited-store"],
)
def test_recovery_does_not_extract_while_the_store_cannot_commit_a_bundle(
    tmp_path, service_for
) -> None:
    sessions = _pending_intents("root-1", "root-2")
    memctl = MemctlAdapter(service_for(tmp_path), agent_id="agent-1")

    # Two passes, as two consolidation runs make them.
    passes = [_recover(sessions, memctl) for _run in range(2)]

    # Each intent is authorized, then the store is asked whether it can commit
    # a bundle. Nothing is read, extracted or written, and both stay pending.
    for counts, steps_by_intent in passes:
        assert counts == (2, 0, 2)
        assert steps_by_intent == {"root-1": ["capability"], "root-2": ["capability"]}
    for root_turn_id in ("root-1", "root-2"):
        identity = build_capture_identity(
            runtime_session_id="session-1", root_turn_id=root_turn_id
        )
        intent = sessions.get_event_by_canonical_id(identity.event_id)
        assert intent is not None
        assert intent.payload["capture_state"] == "pending"
        assert (
            sessions.get_event_by_canonical_id(
                f"memory.capture.result:{identity.capture_id}"
            )
            is None
        )


def test_recovery_through_the_audited_built_in_store_extracts_and_commits(
    tmp_path,
) -> None:
    sessions = _pending_intents("root-1", "root-2")
    memory_store = SQLiteMemoryStore(tmp_path / "memory.db", artifactctl=None)
    memctl = MemctlAdapter(
        MemoryService(store=AuditedMemoryStore(memory_store)), agent_id="agent-1"
    )
    try:
        first, steps_by_intent = _recover(sessions, memctl)
        second, _ = _recover(sessions, memctl)
        candidates = memory_store.candidate_list(CandidateListOptions(limit=10))
    finally:
        memory_store.close()

    assert (first, second) == ((2, 2, 0), (0, 0, 0))
    # Besides the capability query, recovery makes the same calls in the same
    # order: source, extraction, bundle write, result.
    assert {
        root_turn_id: [step for step in steps if step != "capability"]
        for root_turn_id, steps in steps_by_intent.items()
    } == {
        "root-1": ["source", "extract", "bundle", "result"],
        "root-2": ["source", "extract", "bundle", "result"],
    }
    assert len(candidates) == 2


def test_recovery_checks_identity_and_authorization_before_the_store_capability(
    tmp_path,
) -> None:
    sessions = _pending_intents("root-denied", "root-allowed")
    sessions.append_event(
        session_id="session-1",
        event_type="turn.outcome",
        canonical_event_id="turn.outcome:tampered",
        payload={
            "agent_id": "agent-1",
            "capture_id": "capture:tampered",
            "root_turn_id": "root-tampered",
            "capture_state": "pending",
        },
    )
    memctl = MemctlAdapter(
        _external_backend_without_capture(tmp_path), agent_id="agent-1"
    )

    counts, steps_by_intent = _recover(sessions, memctl, denied=("root-denied",))

    assert counts == (3, 0, 3)
    # The tampered intent stops at the identity check (it is never authorized)
    # and the denied one at authorization: only the allowed intent reaches the
    # capability query.
    assert steps_by_intent == {"root-denied": [], "root-allowed": ["capability"]}


def test_recovery_keeps_its_order_for_a_memctl_without_the_capability_predicate(
    tmp_path,
) -> None:
    sessions = _pending_intents("root-1", "root-2")
    memctl = MemctlAdapter(
        _external_backend_without_capture(tmp_path), agent_id="agent-1"
    )

    counts, steps_by_intent = _recover(sessions, memctl, wrapper=_LoggingMemctl)

    # As before: the source is read and extracted, and the bundle write's
    # unsupported error leaves each intent pending.
    assert counts == (2, 0, 2)
    assert steps_by_intent == {
        "root-1": ["source", "extract", "bundle"],
        "root-2": ["source", "extract", "bundle"],
    }


def test_capture_capability_falls_back_to_a_callable_capture_method(
    tmp_path,
) -> None:
    """Owners whose store has no predicate keep the callable-method check."""
    sqlite_store = SQLiteMemoryStore(tmp_path / "memory.db", artifactctl=None)
    # The in-memory store has no `apply_capture_bundle`.
    in_memory_store = InMemoryMemoryStore()
    try:
        capable = [
            AuditedMemoryStore(sqlite_store).supports_capture_bundles(),
            MemoryService(store=sqlite_store).supports_capture_bundles(),
            MemoryService(
                store=AuditedMemoryStore(sqlite_store)
            ).supports_capture_bundles(),
            MemctlAdapter(
                MemoryService(store=AuditedMemoryStore(sqlite_store))
            ).supports_capture_bundles(),
        ]
        incapable = [
            AuditedMemoryStore(in_memory_store).supports_capture_bundles(),
            MemoryService(store=in_memory_store).supports_capture_bundles(),
            MemoryService(
                store=AuditedMemoryStore(in_memory_store)
            ).supports_capture_bundles(),
            MemctlAdapter(
                MemoryService(store=AuditedMemoryStore(in_memory_store))
            ).supports_capture_bundles(),
        ]
    finally:
        sqlite_store.close()

    assert capable == [True, True, True, True]
    assert incapable == [False, False, False, False]
    # A memory service without the predicate: its callable method decides.
    assert MemctlAdapter(
        SimpleNamespace(apply_capture_bundle=lambda bundle: bundle)
    ).supports_capture_bundles()
    assert not MemctlAdapter(SimpleNamespace()).supports_capture_bundles()


def test_retryable_extraction_failure_leaves_capture_pending() -> None:
    identity = build_capture_identity(
        runtime_session_id="session-1", root_turn_id="root-1"
    )
    memory_api = MagicMock()
    writer = MagicMock()
    runner = SimpleNamespace(
        memory_api=memory_api,
        terminal_capture_writer=writer,
        _capture_authorized_for_turn=True,
    )
    result = SimpleNamespace(
        working_state=SimpleNamespace(
            memory_capture_report_root_turn_id="root-1",
            memory_capture_report={
                "items": [],
                "error_code": "extraction_failed",
            },
        ),
        memory_capture_bundle_result=None,
    )

    BrainRunner._apply_pending_capture_bundle(
        runner,
        identity=identity,
        result=result,
    )

    assert result.memory_capture_bundle_result == {
        "capture_id": identity.capture_id,
        "disposition": "pending",
        "error_code": "extraction_failed",
    }
    memory_api.apply_capture_bundle.assert_not_called()
    writer.commit_capture_result_and_release_hold.assert_not_called()
