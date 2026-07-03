from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.schemas import BudgetCounters, WorkingState
from openminion.modules.brain.state import _apply_pending_permission_overrides


def _state_with_overrides(overrides: dict[str, str]) -> WorkingState:
    state = WorkingState(
        session_id="sess-permission-override",
        agent_id="agent-permission-override",
        budgets_remaining=BudgetCounters(
            ticks=8,
            tool_calls=8,
            a2a_calls=0,
            tokens=100000,
            time_ms=45000,
        ),
    )
    state.permission_overrides = dict(overrides)
    return state


def _runner(
    *,
    supplied: bool,
    overrides: dict[str, str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        _pending_permission_mode="default",
        _pending_permission_overrides_supplied=supplied,
        _pending_permission_overrides=dict(overrides or {}),
    )


def test_missing_inbound_permission_overrides_preserves_session_grants() -> None:
    state = _state_with_overrides({"exec.run": "auto"})

    changed = _apply_pending_permission_overrides(
        _runner(supplied=False),
        state,
    )

    assert changed is False
    assert state.permission_overrides == {"exec.run": "auto"}


def test_explicit_empty_inbound_permission_overrides_clear_session_grants() -> None:
    state = _state_with_overrides({"exec.run": "auto"})

    changed = _apply_pending_permission_overrides(
        _runner(supplied=True),
        state,
    )

    assert changed is True
    assert state.permission_overrides == {}


def test_explicit_inbound_permission_overrides_replace_session_grants() -> None:
    state = _state_with_overrides({"exec.run": "auto"})

    changed = _apply_pending_permission_overrides(
        _runner(supplied=True, overrides={"file.write": "auto"}),
        state,
    )

    assert changed is True
    assert state.permission_overrides == {"file.write": "auto"}
