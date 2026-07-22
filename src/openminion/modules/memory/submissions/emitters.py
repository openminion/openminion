from typing import Any
from collections.abc import Mapping

from openminion.modules.memory.submissions.envelope import (
    SubmissionEnvelope,
    SubmissionNamespace,
    SubmissionProvenance,
    SubmissionTrustMode,
)
from openminion.modules.memory.submissions.provenance_adapters import (
    provenance_from_artifact,
    provenance_from_file,
    provenance_from_tool_call,
    provenance_from_turn,
    provenance_from_user_correction,
    provenance_from_validation,
)
from openminion.modules.memory.submissions.sdk_path import (
    SubmissionResult,
    submit_envelope,
)


def _submit_envelope_for_payload(
    store: Any,
    *,
    namespace: SubmissionNamespace,
    payload_kind: str,
    payload: Mapping[str, Any],
    provenance: SubmissionProvenance,
    idempotency_key: str,
    trust_mode: SubmissionTrustMode,
    raise_on_failure: bool,
) -> SubmissionResult:
    return submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=namespace,
            payload_kind=payload_kind,
            payload=dict(payload),
            provenance=provenance,
            idempotency_key=idempotency_key,
            trust_mode=trust_mode,
        ),
        raise_on_failure=raise_on_failure,
    )


def _submit_turn_payload(
    store: Any,
    *,
    namespace: SubmissionNamespace,
    payload_kind: str,
    payload: Mapping[str, Any],
    turn_id: str,
    source_owner: str,
    idempotency_key: str,
    trust_mode: SubmissionTrustMode,
    raise_on_failure: bool,
) -> SubmissionResult:
    return _submit_envelope_for_payload(
        store,
        namespace=namespace,
        payload_kind=payload_kind,
        payload=payload,
        provenance=provenance_from_turn(turn_id=turn_id, source_owner=source_owner),
        idempotency_key=idempotency_key,
        trust_mode=trust_mode,
        raise_on_failure=raise_on_failure,
    )


def emit_episode(
    store: Any,
    *,
    namespace: SubmissionNamespace,
    turn_id: str,
    payload: Mapping[str, Any],
    source_owner: str,
    idempotency_key: str,
    trust_mode: SubmissionTrustMode = "direct",
    raise_on_failure: bool = False,
) -> SubmissionResult:
    """Emit a turn-scoped episode submission."""
    return _submit_turn_payload(
        store,
        namespace=namespace,
        payload_kind="episode",
        payload=payload,
        turn_id=turn_id,
        source_owner=source_owner,
        idempotency_key=idempotency_key,
        trust_mode=trust_mode,
        raise_on_failure=raise_on_failure,
    )


def emit_tool_outcome(
    store: Any,
    *,
    namespace: SubmissionNamespace,
    turn_id: str,
    tool_call_id: str,
    payload: Mapping[str, Any],
    source_owner: str,
    idempotency_key: str,
    trust_mode: SubmissionTrustMode = "direct",
    raise_on_failure: bool = False,
) -> SubmissionResult:
    """Emit a tool-call outcome submission."""
    return _submit_envelope_for_payload(
        store,
        namespace=namespace,
        payload_kind="outcome",
        payload=payload,
        provenance=provenance_from_tool_call(
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            source_owner=source_owner,
        ),
        idempotency_key=idempotency_key,
        trust_mode=trust_mode,
        raise_on_failure=raise_on_failure,
    )


def emit_artifact(
    store: Any,
    *,
    namespace: SubmissionNamespace,
    artifact_id: str,
    payload: Mapping[str, Any],
    source_owner: str,
    idempotency_key: str,
    turn_id: str | None = None,
    trust_mode: SubmissionTrustMode = "direct",
    raise_on_failure: bool = False,
) -> SubmissionResult:
    """Emit an artifact-derived submission."""
    return _submit_envelope_for_payload(
        store,
        namespace=namespace,
        payload_kind="artifact",
        payload=payload,
        provenance=provenance_from_artifact(
            artifact_id=artifact_id,
            source_owner=source_owner,
            turn_id=turn_id,
        ),
        idempotency_key=idempotency_key,
        trust_mode=trust_mode,
        raise_on_failure=raise_on_failure,
    )


def emit_file_document(
    store: Any,
    *,
    namespace: SubmissionNamespace,
    file_path: str,
    payload: Mapping[str, Any],
    source_owner: str,
    idempotency_key: str,
    turn_id: str | None = None,
    trust_mode: SubmissionTrustMode = "direct",
    raise_on_failure: bool = False,
) -> SubmissionResult:
    """Emit a file-derived document submission."""
    return _submit_envelope_for_payload(
        store,
        namespace=namespace,
        payload_kind="document",
        payload=payload,
        provenance=provenance_from_file(
            file_path=file_path,
            source_owner=source_owner,
            turn_id=turn_id,
        ),
        idempotency_key=idempotency_key,
        trust_mode=trust_mode,
        raise_on_failure=raise_on_failure,
    )


def emit_validation_outcome(
    store: Any,
    *,
    namespace: SubmissionNamespace,
    validation_command: str,
    payload: Mapping[str, Any],
    source_owner: str,
    idempotency_key: str,
    turn_id: str | None = None,
    trust_mode: SubmissionTrustMode = "direct",
    raise_on_failure: bool = False,
) -> SubmissionResult:
    """Emit a validation-command outcome submission."""
    return _submit_envelope_for_payload(
        store,
        namespace=namespace,
        payload_kind="outcome",
        payload=payload,
        provenance=provenance_from_validation(
            validation_command=validation_command,
            source_owner=source_owner,
            turn_id=turn_id,
        ),
        idempotency_key=idempotency_key,
        trust_mode=trust_mode,
        raise_on_failure=raise_on_failure,
    )


def emit_user_correction(
    store: Any,
    *,
    namespace: SubmissionNamespace,
    user_correction_id: str,
    payload: Mapping[str, Any],
    source_owner: str,
    idempotency_key: str,
    turn_id: str | None = None,
    trust_mode: SubmissionTrustMode = "candidate",
    raise_on_failure: bool = False,
) -> SubmissionResult:
    """Emit a user-correction submission as a candidate by default."""
    return _submit_envelope_for_payload(
        store,
        namespace=namespace,
        payload_kind="memory_candidate",
        payload=payload,
        provenance=provenance_from_user_correction(
            user_correction_id=user_correction_id,
            source_owner=source_owner,
            turn_id=turn_id,
        ),
        idempotency_key=idempotency_key,
        trust_mode=trust_mode,
        raise_on_failure=raise_on_failure,
    )


def emit_entity_candidate(
    store: Any,
    *,
    namespace: SubmissionNamespace,
    turn_id: str,
    payload: Mapping[str, Any],
    source_owner: str,
    idempotency_key: str,
    trust_mode: SubmissionTrustMode = "candidate",
    raise_on_failure: bool = False,
) -> SubmissionResult:
    """Emit a model-authored entity candidate with turn provenance."""
    return _submit_envelope_for_payload(
        store,
        namespace=namespace,
        payload_kind="entity_candidate",
        payload=payload,
        provenance=provenance_from_turn(turn_id=turn_id, source_owner=source_owner),
        idempotency_key=idempotency_key,
        trust_mode=trust_mode,
        raise_on_failure=raise_on_failure,
    )


def emit_fact_candidate(
    store: Any,
    *,
    namespace: SubmissionNamespace,
    turn_id: str,
    payload: Mapping[str, Any],
    source_owner: str,
    idempotency_key: str,
    tool_call_id: str | None = None,
    file_path: str | None = None,
    trust_mode: SubmissionTrustMode = "candidate",
    raise_on_failure: bool = False,
) -> SubmissionResult:
    """Emit a model-authored fact candidate."""
    if tool_call_id:
        provenance = provenance_from_tool_call(
            turn_id=turn_id, tool_call_id=tool_call_id, source_owner=source_owner
        )
    elif file_path:
        provenance = provenance_from_file(
            file_path=file_path, source_owner=source_owner, turn_id=turn_id
        )
    else:
        provenance = provenance_from_turn(turn_id=turn_id, source_owner=source_owner)
    return _submit_envelope_for_payload(
        store,
        namespace=namespace,
        payload_kind="fact_candidate",
        payload=payload,
        provenance=provenance,
        idempotency_key=idempotency_key,
        trust_mode=trust_mode,
        raise_on_failure=raise_on_failure,
    )


def emit_episode_event(
    store: Any,
    *,
    namespace: SubmissionNamespace,
    turn_id: str,
    payload: Mapping[str, Any],
    source_owner: str,
    idempotency_key: str,
    trust_mode: SubmissionTrustMode = "direct",
    raise_on_failure: bool = False,
) -> SubmissionResult:
    """Emit a typed episode write."""
    return _submit_turn_payload(
        store,
        namespace=namespace,
        payload_kind="episode_event",
        payload=payload,
        turn_id=turn_id,
        source_owner=source_owner,
        idempotency_key=idempotency_key,
        trust_mode=trust_mode,
        raise_on_failure=raise_on_failure,
    )


def emit_episode_step(
    store: Any,
    *,
    namespace: SubmissionNamespace,
    turn_id: str,
    payload: Mapping[str, Any],
    source_owner: str,
    idempotency_key: str,
    tool_call_id: str | None = None,
    trust_mode: SubmissionTrustMode = "direct",
    raise_on_failure: bool = False,
) -> SubmissionResult:
    """SEPM-04: emit an episode step (thought / tool_call / validation / ...)."""
    if tool_call_id:
        provenance = provenance_from_tool_call(
            turn_id=turn_id, tool_call_id=tool_call_id, source_owner=source_owner
        )
    else:
        provenance = provenance_from_turn(turn_id=turn_id, source_owner=source_owner)
    return _submit_envelope_for_payload(
        store,
        namespace=namespace,
        payload_kind="episode_step",
        payload=payload,
        provenance=provenance,
        idempotency_key=idempotency_key,
        trust_mode=trust_mode,
        raise_on_failure=raise_on_failure,
    )


def emit_decision(
    store: Any,
    *,
    namespace: SubmissionNamespace,
    turn_id: str,
    payload: Mapping[str, Any],
    source_owner: str,
    idempotency_key: str,
    trust_mode: SubmissionTrustMode = "direct",
    raise_on_failure: bool = False,
) -> SubmissionResult:
    """SEPM-04: emit a typed decision (chosen alternative + rationale)."""
    return _submit_turn_payload(
        store,
        namespace=namespace,
        payload_kind="decision",
        payload=payload,
        turn_id=turn_id,
        source_owner=source_owner,
        idempotency_key=idempotency_key,
        trust_mode=trust_mode,
        raise_on_failure=raise_on_failure,
    )


def emit_procedure(
    store: Any,
    *,
    namespace: SubmissionNamespace,
    payload: Mapping[str, Any],
    source_owner: str,
    idempotency_key: str,
    turn_id: str | None = None,
    trust_mode: SubmissionTrustMode = "direct",
    raise_on_failure: bool = False,
) -> SubmissionResult:
    """SEPM-04: emit a reusable procedure (default tier ``experimental``)."""
    return _submit_envelope_for_payload(
        store,
        namespace=namespace,
        payload_kind="procedure",
        payload=payload,
        provenance=provenance_from_turn(
            turn_id=turn_id or "no-turn", source_owner=source_owner
        ),
        idempotency_key=idempotency_key,
        trust_mode=trust_mode,
        raise_on_failure=raise_on_failure,
    )


def emit_retrieval_feedback(
    store: Any,
    *,
    namespace: SubmissionNamespace,
    turn_id: str,
    payload: Mapping[str, Any],
    source_owner: str,
    idempotency_key: str,
    trust_mode: SubmissionTrustMode = "direct",
    raise_on_failure: bool = False,
) -> SubmissionResult:
    """Emit a retrieval-feedback submission tied to a specific turn."""
    return _submit_turn_payload(
        store,
        namespace=namespace,
        payload_kind="retrieval_feedback",
        payload=payload,
        turn_id=turn_id,
        source_owner=source_owner,
        idempotency_key=idempotency_key,
        trust_mode=trust_mode,
        raise_on_failure=raise_on_failure,
    )


__all__ = [
    "emit_artifact",
    "emit_decision",
    "emit_entity_candidate",
    "emit_episode",
    "emit_episode_event",
    "emit_episode_step",
    "emit_fact_candidate",
    "emit_file_document",
    "emit_procedure",
    "emit_retrieval_feedback",
    "emit_tool_outcome",
    "emit_user_correction",
    "emit_validation_outcome",
]
