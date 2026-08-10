from __future__ import annotations

from openminion.services.gateway.turn.flow_setup import GatewayTurnSetupMixin


class _Sessions:
    def __init__(self, records):
        self.records = records

    def list_run_records_by_thread(self, session_id: str, thread_id: str):
        assert session_id == "session-1"
        assert thread_id == "thread-1"
        return list(self.records)


class _Setup(GatewayTurnSetupMixin):
    def __init__(self, records):
        self._sessions = _Sessions(records)


def test_new_thread_issues_invocation_without_telemetry() -> None:
    invocation_id, reason = _Setup([])._resolve_invocation_id(
        session_id="session-1", thread_id="thread-1"
    )
    assert invocation_id
    assert reason == "new_thread"


def test_resumed_thread_recovers_durable_invocation() -> None:
    invocation_id, reason = _Setup(
        [{"run_id": "run-1", "invocation_id": "invocation-1"}]
    )._resolve_invocation_id(session_id="session-1", thread_id="thread-1")
    assert invocation_id == "invocation-1"
    assert reason == "resumed_thread"


def test_legacy_thread_starts_new_invocation_with_reason() -> None:
    invocation_id, reason = _Setup(
        [{"run_id": "legacy-run", "invocation_id": None}]
    )._resolve_invocation_id(session_id="session-1", thread_id="thread-1")
    assert invocation_id
    assert reason == "legacy_thread_without_invocation"
