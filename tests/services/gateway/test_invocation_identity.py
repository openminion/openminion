from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.task.run import THREAD_STATE_AWAITING, THREAD_STATE_SETTLED
from openminion.services.gateway.turn.flow_setup import GatewayTurnSetupMixin


class _Setup(GatewayTurnSetupMixin):
    pass


def _lifecycle(
    *,
    invocation_id: str = "",
    state: str = THREAD_STATE_AWAITING,
    source_event_id: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        invocation_id=invocation_id,
        invocation_source_event_id=source_event_id,
        thread_state=state,
    )


def test_new_thread_issues_invocation_without_telemetry() -> None:
    invocation_id, reason, parent = _Setup()._resolve_invocation_id(
        lifecycle=_lifecycle()
    )
    assert invocation_id
    assert reason == "new_thread"
    assert parent == ""


def test_resumed_thread_recovers_durable_invocation() -> None:
    invocation_id, reason, parent = _Setup()._resolve_invocation_id(
        lifecycle=_lifecycle(invocation_id="invocation-1", source_event_id=7)
    )
    assert invocation_id == "invocation-1"
    assert reason == "resumed_thread"
    assert parent == ""


def test_legacy_thread_starts_new_invocation_with_reason() -> None:
    invocation_id, reason, parent = _Setup()._resolve_invocation_id(
        lifecycle=_lifecycle(source_event_id=7)
    )
    assert invocation_id
    assert reason == "legacy_thread_without_invocation"
    assert parent == ""


def test_terminal_thread_issues_child_invocation() -> None:
    invocation_id, reason, parent = _Setup()._resolve_invocation_id(
        lifecycle=_lifecycle(
            invocation_id="invocation-1",
            state=THREAD_STATE_SETTLED,
            source_event_id=7,
        )
    )

    assert invocation_id != "invocation-1"
    assert reason == "terminal_parent"
    assert parent == "invocation-1"
