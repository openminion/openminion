from __future__ import annotations

import pytest

from openminion.modules.task.interfaces import (
    TASK_INTERFACE_VERSION,
    ensure_task_compatibility,
)


class _ValidTaskCtl:
    contract_version = TASK_INTERFACE_VERSION

    def create_task(self, input, *, trace_id=None):
        del input, trace_id
        return None

    def attach_plan(self, task_id, draft, *, trace_id=None):
        del task_id, draft, trace_id
        return None

    def step_update(self, task_id, step_id, input, *, trace_id=None):
        del task_id, step_id, input, trace_id
        return None

    def transition_task(self, task_id, status, *, trace_id=None):
        del task_id, status, trace_id
        return None

    def apply_ops(self, task_ops, *, trace_id=None):
        del task_ops, trace_id
        return []

    def get_task(self, task_id):
        del task_id
        return None

    def get_digest(self, *, agent_id, session_id, limit=5):
        del agent_id, session_id, limit
        return None

    def record_pending_action(self, *, policy_request_id, cursor, reason=None):
        del policy_request_id, cursor, reason
        return None

    def resume_pending_action(self, *, policy_request_id, decision_id, trace_id=None):
        del policy_request_id, decision_id, trace_id
        return None

    def list_events(self):
        return []


def test_valid_task_controller_passes() -> None:
    success, errors = ensure_task_compatibility(_ValidTaskCtl(), strict=False)
    assert success is True
    assert errors == []


def test_missing_method_fails() -> None:
    class _BrokenTaskCtl:
        contract_version = TASK_INTERFACE_VERSION

    success, errors = ensure_task_compatibility(_BrokenTaskCtl(), strict=False)
    assert success is False
    assert any("Missing required member" in e for e in errors)


def test_version_mismatch_strict_raises() -> None:
    class _WrongVersionTaskCtl(_ValidTaskCtl):
        contract_version = "v999"

    with pytest.raises(TypeError):
        ensure_task_compatibility(_WrongVersionTaskCtl(), strict=True)
