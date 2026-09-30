from __future__ import annotations

import uuid

import pytest

from openminion.modules.a2a.models import (
    A2AObservabilityContext,
    Envelope,
    EnvelopeValidationError,
    validate_envelope_contract,
    is_valid_traceparent,
)
from openminion.modules.a2a.runtime import A2ARuntime
from openminion.modules.a2a.storage import MemoryAuditStore, MemoryStateStore


def _context(
    *,
    traceparent: str = ("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"),
) -> A2AObservabilityContext:
    return A2AObservabilityContext(
        invocation_id=str(uuid.uuid4()),
        execution_id=str(uuid.uuid4()),
        handoff_id=str(uuid.uuid4()),
        traceparent=traceparent,
        tracestate="vendor=value",
    )


def _request(context: A2AObservabilityContext) -> Envelope:
    return Envelope.new(
        from_agent="caller",
        to_agent="worker",
        to_capability=None,
        type="call",
        method="work.run",
        idempotency_key="observability-replay",
        observability=context,
    )


def test_context_round_trip_is_top_level_and_exact() -> None:
    request = _request(_context())
    payload = request.to_dict()
    assert "observability" in payload
    assert "observability" not in payload["meta"]
    assert Envelope.from_dict(payload).observability == request.observability


def test_malformed_identifier_fails() -> None:
    invalid = _context()
    object.__setattr__(invalid, "handoff_id", "not-a-uuid")
    with pytest.raises(EnvelopeValidationError):
        validate_envelope_contract(_request(invalid))


@pytest.mark.parametrize(
    "traceparent",
    [
        "",
        "malformed",
        " 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "00-4BF92F3577B34DA6A3CE929D0E0E4736-00f067aa0ba902b7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00F067AA0BA902B7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e47+6-00f067aa0ba902b7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e47_6-00f067aa0ba902b7-01",
        "01-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "00-00000000000000000000000000000000-00f067aa0ba902b7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01-extra",
    ],
)
def test_missing_or_malformed_traceparent_fails(traceparent: str) -> None:
    with pytest.raises(
        EnvelopeValidationError,
        match="traceparent must be a valid W3C traceparent",
    ):
        validate_envelope_contract(_request(_context(traceparent=traceparent)))


def test_version_zero_traceparent_is_accepted() -> None:
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    assert is_valid_traceparent(traceparent) is True
    validate_envelope_contract(_request(_context(traceparent=traceparent)))


def test_fresh_cached_and_audit_shapes_preserve_context() -> None:
    audit_store = MemoryAuditStore()
    runtime = A2ARuntime(
        state_store=MemoryStateStore(),
        audit_store=audit_store,
    )
    runtime.register_agent("worker", ["work."], lambda _: {"ok": True})
    request = _request(_context())

    fresh = runtime.call(request)
    cached = runtime.call(request)
    audit = audit_store.query_audit({"trace_id": request.trace_id})

    assert fresh.observability == request.observability
    assert cached.observability == request.observability
    expected = request.observability.to_dict()
    expected.pop("tracestate")
    assert audit[0].envelope == {"observability": expected}
    runtime.close()
