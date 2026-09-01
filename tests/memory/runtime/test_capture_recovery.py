import json
import sqlite3

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

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
from openminion.modules.memory.storage import CandidateListOptions
from openminion.modules.brain.adapters.memory.runtime import MemctlAdapter
from openminion.modules.brain.runner.coordinator import BrainRunner
from openminion.modules.session.capture import (
    RuntimeTerminalCaptureWriter,
    TerminalCaptureReceiptError,
    build_capture_identity,
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


def test_retryable_extraction_failure_leaves_capture_pending() -> None:
    identity = build_capture_identity(
        runtime_session_id="session-1", root_turn_id="root-1"
    )
    memory_api = MagicMock()
    writer = MagicMock()
    runner = SimpleNamespace(memory_api=memory_api, terminal_capture_writer=writer)
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
