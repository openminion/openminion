from unittest.mock import Mock, patch

import pytest

from openminion.modules.a2a.interfaces import (
    A2A_INTERFACE_VERSION,
    ensure_a2a_compatibility,
)
from openminion.modules.a2a.runtime import A2ARuntime
from openminion.modules.a2a.storage.base import AuditStore, StateStore


def _runtime() -> A2ARuntime:
    state_store = Mock(spec=StateStore.__abstractmethods__)
    audit_store = Mock(spec=AuditStore.__abstractmethods__)
    with patch.object(A2ARuntime, "recover_stale_jobs", return_value=[]):
        return A2ARuntime(state_store=state_store, audit_store=audit_store)


def test_contract_version_declared():
    runtime = _runtime()
    assert hasattr(runtime, "contract_version")
    assert runtime.contract_version == A2A_INTERFACE_VERSION


def test_valid_implementation_passes():
    runtime = _runtime()
    success, errors = ensure_a2a_compatibility(runtime, strict=False)
    assert success is True
    assert len(errors) == 0


def test_missing_method_fails():
    class BrokenRuntime:
        contract_version = A2A_INTERFACE_VERSION

    runtime = BrokenRuntime()
    success, errors = ensure_a2a_compatibility(runtime, strict=False)
    assert success is False
    assert len(errors) > 0
    assert "Missing required method" in str(errors[0])


def test_version_mismatch_fails():
    class WrongVersionRuntime:
        contract_version = "v99"

    runtime = WrongVersionRuntime()
    success, errors = ensure_a2a_compatibility(runtime, strict=False)
    assert success is False
    assert len(errors) > 0
    assert "Version mismatch" in str(errors[0])


def test_strict_mode_raises_error():
    class BadRuntime:
        contract_version = "v99"

    runtime = BadRuntime()
    with pytest.raises(Exception):
        ensure_a2a_compatibility(runtime, strict=True)
