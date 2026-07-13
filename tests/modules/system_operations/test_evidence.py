import pytest

from openminion.modules.system_operations.evidence import EvidenceStore, build_evidence
from openminion.modules.system_operations.schemas import (
    OperationRequest,
    TransportResult,
)


def _request() -> OperationRequest:
    return OperationRequest(
        operation_id="observe",
        target_id="local",
        profile_id="host.snapshot",
        parameters={"token": "secret-value"},
        session_id="session",
    )


@pytest.mark.parametrize(
    ("result", "status"),
    [
        (TransportResult(argv=("true",), return_code=0, stdout="ok"), "observed"),
        (TransportResult(argv=("false",), return_code=1, stderr="failed"), "failed"),
        (
            TransportResult(argv=("sleep",), return_code=124, timed_out=True),
            "partial",
        ),
        (
            TransportResult(argv=("sleep",), return_code=130, cancelled=True),
            "unknown",
        ),
        (TransportResult(argv=("true",), return_code=0), "unknown"),
    ],
)
def test_evidence_claim_status_is_derived_from_transport_result(
    result: TransportResult,
    status: str,
) -> None:
    evidence = build_evidence(_request(), result)

    assert evidence.claim_status == status
    assert evidence.command_hash
    assert evidence.output_digest


def test_evidence_redacts_outputs_and_parameters() -> None:
    evidence = build_evidence(
        _request(),
        TransportResult(
            argv=("printf",),
            return_code=0,
            stdout="secret-value",
            stderr="secret-value",
        ),
        redactions=("secret-value",),
    )

    assert evidence.stdout_preview == "[REDACTED]"
    assert evidence.stderr_preview == "[REDACTED]"
    assert evidence.redacted_parameters == {"token": "[REDACTED]"}


def test_evidence_store_persists_and_scopes_records(tmp_path) -> None:
    path = tmp_path / "evidence.db"
    first = build_evidence(
        _request(),
        TransportResult(argv=("true",), return_code=0, stdout="ok"),
    )
    other = build_evidence(
        _request().model_copy(update={"operation_id": "other", "target_id": "other"}),
        TransportResult(argv=("true",), return_code=0, stdout="ok"),
    )
    store = EvidenceStore(path)
    store.put(first)
    store.put(other)

    reopened = EvidenceStore(path)
    assert reopened.get(first.evidence_id) == first
    assert reopened.list(target_id="local", session_id="session") == (first,)
