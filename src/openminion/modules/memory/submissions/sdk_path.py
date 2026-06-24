"""Direct SDK adapter from OpenMinion submission envelopes to Sophiagraph."""

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Mapping
from uuid import uuid4

from openminion.base.time import utc_now_iso
from openminion.modules.memory.submissions.envelope import (
    SUBMISSION_ENVELOPE_SCHEMA_VERSION,
    SubmissionEnvelope,
    SubmissionEnvelopeError,
)


_LOG = logging.getLogger(__name__)
_RECORD_PAYLOAD_KINDS = frozenset(
    {
        "episode",
        "outcome",
        "fact",
        "document",
        "retrieval_event",
        "retrieval_feedback",
        "artifact",
    }
)
_ENTITY_EPISODE_PAYLOAD_KINDS = frozenset(
    {
        "entity_candidate",
        "entity_alias_candidate",
        "fact_candidate",
        "contradiction_decision",
        "entity_summary",
        "episode_event",
        "episode_step",
        "decision",
        "procedure",
    }
)


@dataclass(frozen=True)
class SubmissionResult:
    """Typed outcome of one ``submit_envelope`` call."""

    ok: bool
    envelope_idempotency_key: str
    payload_kind: str
    code: str = "SUBMITTED"
    object_id: str | None = None
    deduped: bool = False
    error_type: str | None = None
    error_message: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


_SEEN_IDEMPOTENCY_KEYS: set[tuple[str, str]] = set()


def reset_idempotency_registry() -> None:
    """Reset the module-level idempotency cache. Used by tests."""
    _SEEN_IDEMPOTENCY_KEYS.clear()


def _to_package_namespace(envelope: SubmissionEnvelope):
    """Lazy-import ``sophiagraph`` so this module does not import at file scope."""
    from sophiagraph.models import MemoryNamespace

    return MemoryNamespace(**envelope.namespace.as_dict())


def _generated_id(payload: Mapping[str, Any], key: str, prefix: str) -> str:
    return str(payload.get(key) or f"{prefix}-{uuid4()}")


def _timestamp(
    payload: Mapping[str, Any],
    key: str,
    *,
    default: str | None = None,
) -> str:
    return str(payload.get(key, utc_now_iso() if default is None else default))


def _model_authored_provenance(envelope: SubmissionEnvelope):
    from sophiagraph.models.entity_fact import EntityFactProvenance

    return EntityFactProvenance(
        source_kind="model_authored",
        source_id=envelope.provenance.turn_id or envelope.idempotency_key,
        actor=envelope.provenance.source_owner,
    )


def _candidate_fallback_envelope(
    envelope: SubmissionEnvelope,
    payload: Mapping[str, Any],
) -> SubmissionEnvelope:
    return SubmissionEnvelope(
        namespace=envelope.namespace,
        payload_kind="memory_candidate",
        payload={
            "session_id": envelope.namespace.session_id or "om-submission",
            "type": "fact",
            "content": payload,
            "source": "agent_inferred",
            "meta": {"original_payload_kind": envelope.payload_kind},
        },
        provenance=envelope.provenance,
        idempotency_key=envelope.idempotency_key,
    )


def _build_record(
    envelope: SubmissionEnvelope,
    *,
    record_type: str,
):
    from sophiagraph import MemoryRecord

    payload = dict(envelope.payload)
    record_id = _generated_id(payload, "id", "rec")
    scope = str(payload.get("scope") or _envelope_scope(envelope))
    namespace = _to_package_namespace(envelope)
    now = utc_now_iso()
    meta = dict(payload.get("meta", {}))
    if envelope.ontology_binding is not None:
        meta.setdefault("ontology_id", envelope.ontology_binding.ontology_id)
        meta.setdefault("ontology_version", envelope.ontology_binding.version)
    return MemoryRecord(
        id=record_id,
        scope=scope,
        type=payload.get("type", record_type),
        content=payload.get("content", ""),
        created_at=_timestamp(payload, "created_at", default=now),
        updated_at=_timestamp(payload, "updated_at", default=now),
        key=payload.get("key"),
        title=payload.get("title"),
        tags=list(payload.get("tags", [])),
        entities=list(payload.get("entities", [])),
        source=payload.get("source", "agent_inferred"),
        confidence=float(payload.get("confidence", 0.5)),
        meta=meta,
        namespace=namespace,
    )


def _envelope_scope(envelope: SubmissionEnvelope) -> str:
    """Derive a default scope from the envelope namespace."""
    ns = envelope.namespace
    if ns.session_id:
        return f"session:{ns.session_id}"
    if ns.agent_id:
        return f"agent:{ns.agent_id}"
    if ns.project_id:
        return f"project:{ns.project_id}"
    if ns.graph_id:
        return f"global:{ns.graph_id}"
    if ns.tenant_id:
        return f"global:{ns.tenant_id}"
    return "global:om"


def _put_memory_candidate(store: Any, envelope: SubmissionEnvelope) -> str:
    from sophiagraph.models import MemoryCandidate

    payload = dict(envelope.payload)
    namespace = _to_package_namespace(envelope)
    candidate_id = _generated_id(payload, "candidate_id", "cand")
    session_id = str(payload.get("session_id") or envelope.namespace.session_id or "")
    proposed_scope = str(payload.get("proposed_scope") or _envelope_scope(envelope))
    if not session_id:
        raise SubmissionEnvelopeError(
            "memory_candidate submission requires session_id in payload or namespace",
            field="payload.session_id",
        )
    candidate = MemoryCandidate(
        candidate_id=candidate_id,
        session_id=session_id,
        proposed_scope=proposed_scope,
        type=payload.get("type", "fact"),
        content=payload.get("content", ""),
        tags=list(payload.get("tags", [])),
        entities=list(payload.get("entities", [])),
        source=payload.get("source", "agent_inferred"),
        confidence=float(payload.get("confidence", 0.5)),
        meta=dict(payload.get("meta", {})),
        namespace=namespace,
    )
    return store.put_candidate(candidate)


def _put_approved_block(store: Any, envelope: SubmissionEnvelope) -> str:
    from sophiagraph.models import MemoryBlock, validate_block_for_creation

    payload = dict(envelope.payload)
    namespace = _to_package_namespace(envelope)
    now = utc_now_iso()
    block_id = _generated_id(payload, "block_id", "blk")
    block = MemoryBlock(
        block_id=block_id,
        class_name=str(payload["class_name"]),
        mode=str(payload.get("mode", "pinned")),
        content=str(payload.get("content", "")),
        token_estimate=int(payload.get("token_estimate", 1)),
        owner_namespace=namespace,
        source=str(payload.get("source", "operator_pin")),
        created_at=_timestamp(payload, "created_at", default=now),
        last_updated_at=_timestamp(payload, "last_updated_at", default=now),
        last_updated_by=str(
            payload.get("last_updated_by", envelope.provenance.source_owner)
        ),
        stale_after=payload.get("stale_after"),
    )
    validate_block_for_creation(block)
    return store.put_memory_block(block)


def _put_explicit_link(store: Any, envelope: SubmissionEnvelope) -> str:
    from sophiagraph.models import StructuralLink

    payload = dict(envelope.payload)
    namespace = _to_package_namespace(envelope)
    link_id = _generated_id(payload, "link_id", "lnk")
    link = StructuralLink(
        link_id=link_id,
        source_record_id=str(payload["source_record_id"]),
        target_record_id=payload.get("target_record_id"),
        raw_target=str(payload.get("raw_target", "")),
        link_kind=str(payload.get("link_kind", "wikilink")),
        resolution_status=str(payload.get("resolution_status", "unresolved")),
        relation_type=payload.get("relation_type"),
        display_text=payload.get("display_text"),
        source_path=payload.get("source_path"),
        namespace=namespace,
        created_at=_timestamp(payload, "created_at"),
    )
    return store.put_link(link)


def _dispatch_entity_episode(store: Any, envelope: SubmissionEnvelope) -> str | None:
    payload = dict(envelope.payload)
    namespace = _to_package_namespace(envelope)

    if envelope.trust_mode == "candidate":
        return _put_memory_candidate(
            store,
            _candidate_fallback_envelope(envelope, payload),
        )

    if envelope.payload_kind == "entity_candidate":
        from sophiagraph.models import Entity

        prov = _model_authored_provenance(envelope)
        entity = Entity(
            entity_id=_generated_id(payload, "entity_id", "ent"),
            canonical_name=str(payload["canonical_name"]),
            namespace=namespace,
            provenance=prov,
            entity_type=str(payload.get("entity_type", "unspecified")),
            created_at=_timestamp(payload, "created_at"),
            updated_at=_timestamp(payload, "updated_at"),
            confidence=float(payload.get("confidence", 0.5)),
        )
        return store.put_entity(entity)

    if envelope.payload_kind == "entity_alias_candidate":
        from sophiagraph.models import EntityAlias

        prov = _model_authored_provenance(envelope)
        alias = EntityAlias(
            alias_id=_generated_id(payload, "alias_id", "alias"),
            alias_name=str(payload["alias_name"]),
            entity_id=str(payload["entity_id"]),
            original_entity_id=str(
                payload.get("original_entity_id", payload["entity_id"])
            ),
            namespace=namespace,
            provenance=prov,
            created_at=_timestamp(payload, "created_at"),
            is_primary=bool(payload.get("is_primary", False)),
        )
        return store.put_entity_alias(alias)

    if envelope.payload_kind == "fact_candidate":
        from sophiagraph.models import Fact

        prov = _model_authored_provenance(envelope)
        fact = Fact(
            fact_id=_generated_id(payload, "fact_id", "fact"),
            namespace=namespace,
            subject_entity_id=str(payload["subject_entity_id"]),
            predicate=str(payload["predicate"]),
            object_entity_id=payload.get("object_entity_id"),
            object_literal=payload.get("object_literal"),
            provenance=prov,
            confidence=float(payload.get("confidence", 0.5)),
            valid_from=payload.get("valid_from"),
            valid_to=payload.get("valid_to"),
            observed_at=_timestamp(payload, "observed_at"),
            created_at=_timestamp(payload, "created_at"),
            updated_at=_timestamp(payload, "updated_at"),
            source_episode_ids=list(payload.get("source_episode_ids", [])),
        )
        return store.put_fact(fact)

    if envelope.payload_kind == "contradiction_decision":
        from sophiagraph.models import Contradiction

        contra = Contradiction(
            contradiction_id=_generated_id(payload, "contradiction_id", "contra"),
            namespace=namespace,
            target_fact_id=str(payload["target_fact_id"]),
            contradicting_fact_id=str(payload["contradicting_fact_id"]),
            decision=str(payload["decision"]),
            deciding_actor=str(
                payload.get("deciding_actor", envelope.provenance.source_owner)
            ),
            decided_at=_timestamp(payload, "decided_at"),
            reason=str(payload.get("reason", "")),
        )
        applied = store.record_contradiction(contra)
        return applied.contradiction_id

    if envelope.payload_kind == "entity_summary":
        from sophiagraph.models import EntitySummary

        prov = _model_authored_provenance(envelope)
        summary = EntitySummary(
            summary_id=_generated_id(payload, "summary_id", "esum"),
            entity_id=str(payload["entity_id"]),
            namespace=namespace,
            summary_text=str(payload["summary_text"]),
            provenance=prov,
            created_at=_timestamp(payload, "created_at"),
            updated_at=_timestamp(payload, "updated_at"),
        )
        return store.put_entity_summary(summary)

    if envelope.payload_kind == "episode_event":
        from sophiagraph.models import Episode

        episode = Episode(
            episode_id=_generated_id(payload, "episode_id", "ep"),
            namespace=namespace,
            title=str(payload["title"]),
            status=str(payload.get("status", "in_progress")),
            started_at=_timestamp(payload, "started_at"),
            ended_at=payload.get("ended_at"),
            parent_episode_id=payload.get("parent_episode_id"),
            task_id=payload.get("task_id"),
            artifact_ids=list(payload.get("artifact_ids", [])),
            tool_ids=list(payload.get("tool_ids", [])),
            summary=str(payload.get("summary", "")),
        )
        return store.put_episode(episode)

    if envelope.payload_kind == "episode_step":
        from sophiagraph.models import EpisodeStep

        step = EpisodeStep(
            step_id=_generated_id(payload, "step_id", "step"),
            episode_id=str(payload["episode_id"]),
            namespace=namespace,
            kind=str(payload["kind"]),
            sequence=int(payload.get("sequence", 0)),
            occurred_at=_timestamp(payload, "occurred_at"),
            content=str(payload.get("content", "")),
            tool_id=payload.get("tool_id"),
            tool_call_id=payload.get("tool_call_id"),
            artifact_id=payload.get("artifact_id"),
            file_path=payload.get("file_path"),
        )
        return store.put_episode_step(step)

    if envelope.payload_kind == "decision":
        from sophiagraph.models import Decision

        decision = Decision(
            decision_id=_generated_id(payload, "decision_id", "dec"),
            namespace=namespace,
            title=str(payload["title"]),
            chosen=str(payload["chosen"]),
            occurred_at=_timestamp(payload, "occurred_at"),
            episode_id=payload.get("episode_id"),
            step_id=payload.get("step_id"),
            alternatives=list(payload.get("alternatives", [])),
            rationale=str(payload.get("rationale", "")),
            deciding_actor=str(
                payload.get("deciding_actor", envelope.provenance.source_owner)
            ),
        )
        return store.put_decision(decision)

    if envelope.payload_kind == "procedure":
        from sophiagraph.models import Procedure, ProcedureStep

        steps_raw = payload.get("steps", [])
        steps = [
            s
            if isinstance(s, ProcedureStep)
            else ProcedureStep(
                sequence=int(s.get("sequence", i)),
                title=str(s.get("title", "")),
                body=str(s.get("body", "")),
                tool_id=s.get("tool_id"),
            )
            for i, s in enumerate(steps_raw)
        ]
        procedure = Procedure(
            procedure_id=_generated_id(payload, "procedure_id", "proc"),
            namespace=namespace,
            title=str(payload["title"]),
            promotion_tier=str(payload.get("promotion_tier", "experimental")),
            created_at=_timestamp(payload, "created_at"),
            updated_at=_timestamp(payload, "updated_at"),
            steps=steps,
            rollback_hint=str(payload.get("rollback_hint", "")),
            source_episode_ids=list(payload.get("source_episode_ids", [])),
        )
        return store.put_procedure(procedure)

    raise SubmissionEnvelopeError(
        f"_dispatch_entity_episode: unhandled payload kind {envelope.payload_kind!r}"
    )


def _record_type_for(payload_kind: str) -> str:
    return {
        "episode": "session_summary",
        "outcome": "tool_outcome",
        "fact": "fact",
        "document": "fact",
        "retrieval_event": "meta_insight",
        "retrieval_feedback": "meta_insight",
        "artifact": "artifact_digest",
    }[payload_kind]


def _schema_mismatch_result(envelope: SubmissionEnvelope) -> SubmissionResult:
    return SubmissionResult(
        ok=False,
        envelope_idempotency_key=envelope.idempotency_key,
        payload_kind=envelope.payload_kind,
        code="SCHEMA_VERSION_MISMATCH",
        error_type="SubmissionEnvelopeError",
        error_message=(
            f"envelope schema_version {envelope.schema_version!r} != "
            f"pinned {SUBMISSION_ENVELOPE_SCHEMA_VERSION!r}"
        ),
    )


def _patch_record_metadata(store: Any, envelope: SubmissionEnvelope) -> str:
    target_id = str(envelope.payload.get("target_record_id") or "")
    if not target_id:
        raise SubmissionEnvelopeError(
            f"{envelope.payload_kind} submission requires payload.target_record_id"
        )
    existing = store.get_record(target_id)
    if existing is None:
        raise SubmissionEnvelopeError(
            f"{envelope.payload_kind} submission: target record {target_id!r} not found"
        )
    if envelope.payload_kind == "tag":
        tags = set(existing.tags) | set(envelope.payload.get("tags", []))
        store.put_record(replace(existing, tags=sorted(tags)))
        return target_id

    patched_meta = dict(existing.meta)
    patched_meta.setdefault("properties", {}).update(
        dict(envelope.payload.get("properties", {}))
    )
    store.put_record(replace(existing, meta=patched_meta))
    return target_id


def submit_envelope(
    store: Any,
    envelope: SubmissionEnvelope,
    *,
    raise_on_failure: bool = False,
) -> SubmissionResult:
    """Apply ``envelope`` to ``store`` via the direct SDK path."""

    if envelope.schema_version != SUBMISSION_ENVELOPE_SCHEMA_VERSION:
        result = _schema_mismatch_result(envelope)
        if raise_on_failure:
            raise SubmissionEnvelopeError(
                result.error_message or "schema version mismatch"
            )
        return result

    dedup_key = (envelope.payload_kind, envelope.idempotency_key)
    if dedup_key in _SEEN_IDEMPOTENCY_KEYS:
        return SubmissionResult(
            ok=True,
            envelope_idempotency_key=envelope.idempotency_key,
            payload_kind=envelope.payload_kind,
            code="DEDUPED",
            deduped=True,
        )

    try:
        if envelope.payload_kind == "memory_candidate":
            object_id = _put_memory_candidate(store, envelope)
        elif envelope.payload_kind == "approved_block":
            object_id = _put_approved_block(store, envelope)
        elif envelope.payload_kind == "explicit_link":
            object_id = _put_explicit_link(store, envelope)
        elif envelope.payload_kind in _RECORD_PAYLOAD_KINDS:
            record_type = _record_type_for(envelope.payload_kind)
            record = _build_record(envelope, record_type=record_type)
            object_id = store.put_record(record)
        elif envelope.payload_kind in _ENTITY_EPISODE_PAYLOAD_KINDS:
            object_id = _dispatch_entity_episode(store, envelope)
        elif envelope.payload_kind in {"tag", "property"}:
            object_id = _patch_record_metadata(store, envelope)
        else:  # pragma: no cover - exhaustive guard
            raise SubmissionEnvelopeError(
                f"submit_envelope: payload kind {envelope.payload_kind!r} not handled"
            )

        _SEEN_IDEMPOTENCY_KEYS.add(dedup_key)
        return SubmissionResult(
            ok=True,
            envelope_idempotency_key=envelope.idempotency_key,
            payload_kind=envelope.payload_kind,
            code="SUBMITTED",
            object_id=str(object_id) if object_id is not None else None,
        )
    except SubmissionEnvelopeError as err:
        result = SubmissionResult(
            ok=False,
            envelope_idempotency_key=envelope.idempotency_key,
            payload_kind=envelope.payload_kind,
            code="VALIDATION_ERROR",
            error_type="SubmissionEnvelopeError",
            error_message=str(err),
        )
        if raise_on_failure:
            raise
        return result
    except Exception as err:  # noqa: BLE001 - non-blocking failure surface
        _LOG.warning(
            "sophiagraph submission failed for kind=%s key=%s: %s",
            envelope.payload_kind,
            envelope.idempotency_key,
            err,
        )
        result = SubmissionResult(
            ok=False,
            envelope_idempotency_key=envelope.idempotency_key,
            payload_kind=envelope.payload_kind,
            code="BACKEND_FAILURE",
            error_type=type(err).__name__,
            error_message=str(err),
        )
        if raise_on_failure:
            raise
        return result


__all__ = (
    "SubmissionResult",
    "reset_idempotency_registry",
    "submit_envelope",
)
