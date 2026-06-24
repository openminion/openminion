from __future__ import annotations

import pytest

from openminion.modules.a2a.models import (  # noqa: E402
    Envelope,
    EnvelopeValidationError,
    MESSAGE_TYPE_CALL,
    MESSAGE_TYPE_JOB_START,
    MESSAGE_TYPE_JOB_STATUS,
    validate_envelope_contract,
)


def _base_envelope(
    *, type: str, method: str = "agent.echo", idempotency_key: str = "idem-1"
) -> Envelope:  # noqa: A002
    return Envelope.new(
        from_agent="tester",
        to_agent="worker",
        to_capability=None,
        type=type,
        method=method,
        params={"payload": True},
        idempotency_key=idempotency_key,
        timeout_ms=1000,
    )


def test_valid_call_passes_contract() -> None:
    envelope = _base_envelope(type=MESSAGE_TYPE_CALL)
    validate_envelope_contract(envelope)


def test_missing_destination_rejected() -> None:
    envelope = _base_envelope(type=MESSAGE_TYPE_CALL)
    envelope.to_agent = None
    envelope.to_capability = None
    with pytest.raises(EnvelopeValidationError):
        validate_envelope_contract(envelope)


def test_missing_idempotency_for_call_rejected() -> None:
    envelope = _base_envelope(type=MESSAGE_TYPE_CALL, idempotency_key="")
    with pytest.raises(EnvelopeValidationError):
        validate_envelope_contract(envelope)


def test_missing_idempotency_for_job_start_rejected() -> None:
    envelope = _base_envelope(type=MESSAGE_TYPE_JOB_START, idempotency_key="")
    with pytest.raises(EnvelopeValidationError):
        validate_envelope_contract(envelope)


def test_idempotency_optional_for_job_status() -> None:
    envelope = _base_envelope(type=MESSAGE_TYPE_JOB_STATUS, idempotency_key="")
    validate_envelope_contract(envelope)


def test_invalid_type_raises_error() -> None:
    envelope = _base_envelope(type="invalid.type")
    with pytest.raises(EnvelopeValidationError):
        validate_envelope_contract(envelope)
