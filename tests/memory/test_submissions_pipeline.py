from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from openminion.modules.memory.submissions import (
    PAYLOAD_KINDS,
    SUBMISSION_ENVELOPE_SCHEMA_VERSION,
    SubmissionEnvelope,
    SubmissionEnvelopeError,
    SubmissionNamespace,
    SubmissionProvenance,
    SubmissionQueue,
    emit_artifact,
    emit_episode,
    emit_file_document,
    emit_retrieval_feedback,
    emit_tool_outcome,
    emit_user_correction,
    emit_validation_outcome,
    provenance_from_artifact,
    provenance_from_file,
    provenance_from_tool_call,
    provenance_from_turn,
    provenance_from_user_correction,
    provenance_from_validation,
    reset_idempotency_registry,
    submit_envelope,
)
from sophiagraph import SophiaGraphMemoryStore


@pytest.fixture(autouse=True)
def _reset_idempotency():
    reset_idempotency_registry()
    yield
    reset_idempotency_registry()


@pytest.fixture
def store() -> SophiaGraphMemoryStore:
    return SophiaGraphMemoryStore()


def _ns() -> SubmissionNamespace:
    return SubmissionNamespace(tenant_id="acme", agent_id="alpha", session_id="sess-1")


def _prov(turn: str = "turn-1") -> SubmissionProvenance:
    return SubmissionProvenance(source_owner="task-runner", turn_id=turn)


def test_envelope_round_trips_as_dict() -> None:
    envelope = SubmissionEnvelope(
        namespace=_ns(),
        payload_kind="episode",
        payload={"content": "did a thing"},
        provenance=_prov(),
        idempotency_key="idem-1",
    )
    payload = envelope.as_dict()
    assert payload["schema_version"] == SUBMISSION_ENVELOPE_SCHEMA_VERSION
    assert payload["namespace"]["agent_id"] == "alpha"
    assert payload["payload_kind"] == "episode"
    assert payload["provenance"]["source_owner"] == "task-runner"
    assert payload["idempotency_key"] == "idem-1"
    assert payload["trust_mode"] == "direct"


def test_envelope_requires_namespace_with_at_least_one_id() -> None:
    with pytest.raises(SubmissionEnvelopeError) as info:
        SubmissionNamespace()
    assert info.value.field == "namespace"


def test_envelope_rejects_unknown_payload_kind() -> None:
    with pytest.raises(SubmissionEnvelopeError) as info:
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="invent_fact_from_prose",
            payload={},
            provenance=_prov(),
            idempotency_key="idem-1",
        )
    assert info.value.field == "payload_kind"


def test_envelope_rejects_unknown_trust_mode() -> None:
    with pytest.raises(SubmissionEnvelopeError) as info:
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="episode",
            payload={},
            provenance=_prov(),
            idempotency_key="idem-1",
            trust_mode="rogue_mode",  # type: ignore[arg-type]
        )
    assert info.value.field == "trust_mode"


def test_envelope_requires_idempotency_key() -> None:
    with pytest.raises(SubmissionEnvelopeError) as info:
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="episode",
            payload={},
            provenance=_prov(),
            idempotency_key="",
        )
    assert info.value.field == "idempotency_key"


def test_provenance_requires_source_owner() -> None:
    with pytest.raises(SubmissionEnvelopeError) as info:
        SubmissionProvenance(source_owner="")
    assert info.value.field == "provenance.source_owner"


def test_envelope_pins_schema_version() -> None:
    with pytest.raises(SubmissionEnvelopeError):
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="episode",
            payload={},
            provenance=_prov(),
            idempotency_key="idem-1",
            schema_version="openminion_sophiagraph_submission.v999",
        )


def test_every_payload_kind_is_listed_in_payload_kinds_constant() -> None:
    expected = {
        "memory_candidate",
        "episode",
        "outcome",
        "artifact",
        "retrieval_feedback",
        "approved_block",
        "document",
        "explicit_link",
        "tag",
        "property",
        "fact",
        "retrieval_event",
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
    assert PAYLOAD_KINDS == expected


def test_provenance_from_turn() -> None:
    prov = provenance_from_turn(turn_id="t-1", source_owner="x")
    assert prov.turn_id == "t-1"
    assert prov.source_owner == "x"


def test_provenance_from_tool_call() -> None:
    prov = provenance_from_tool_call(
        turn_id="t-1", tool_call_id="tc-1", source_owner="x"
    )
    assert prov.tool_call_id == "tc-1"


def test_provenance_from_file_artifact_validation_correction() -> None:
    assert provenance_from_file(file_path="/foo", source_owner="x").file_path == "/foo"
    assert (
        provenance_from_artifact(artifact_id="a-1", source_owner="x").artifact_id
        == "a-1"
    )
    assert (
        provenance_from_validation(
            validation_command="make check", source_owner="x"
        ).validation_command
        == "make check"
    )
    assert (
        provenance_from_user_correction(
            user_correction_id="uc-1", source_owner="x"
        ).user_correction_id
        == "uc-1"
    )


def test_submit_episode_writes_record(store: SophiaGraphMemoryStore) -> None:
    envelope = SubmissionEnvelope(
        namespace=_ns(),
        payload_kind="episode",
        payload={"content": "task A completed"},
        provenance=_prov(),
        idempotency_key="idem-ep-1",
    )
    result = submit_envelope(store, envelope)
    assert result.ok is True
    assert result.code == "SUBMITTED"
    assert result.object_id is not None
    record = store.get_record(result.object_id)
    assert record is not None
    assert record.content == "task A completed"


def test_submit_memory_candidate(store: SophiaGraphMemoryStore) -> None:
    envelope = SubmissionEnvelope(
        namespace=_ns(),
        payload_kind="memory_candidate",
        payload={"content": "candidate fact", "type": "fact"},
        provenance=_prov(),
        idempotency_key="idem-cand-1",
    )
    result = submit_envelope(store, envelope)
    assert result.ok is True
    assert result.object_id is not None
    assert store.get_candidate(result.object_id) is not None


def test_submit_approved_block(store: SophiaGraphMemoryStore) -> None:
    envelope = SubmissionEnvelope(
        namespace=_ns(),
        payload_kind="approved_block",
        payload={
            "class_name": "session_pin",
            "mode": "pinned",
            "content": "Use ripgrep for searches.",
            "token_estimate": 8,
        },
        provenance=_prov(),
        idempotency_key="idem-blk-1",
    )
    result = submit_envelope(store, envelope)
    assert result.ok is True


def test_submit_approved_block_rejects_deferred_mode(
    store: SophiaGraphMemoryStore,
) -> None:
    envelope = SubmissionEnvelope(
        namespace=_ns(),
        payload_kind="approved_block",
        payload={
            "class_name": "session_pin",
            "mode": "shared",  # deferred
            "content": "deferred",
            "token_estimate": 1,
        },
        provenance=_prov(),
        idempotency_key="idem-blk-deferred",
    )
    result = submit_envelope(store, envelope)
    assert result.ok is False
    assert result.code == "BACKEND_FAILURE"
    assert "MemoryBlockModeNotYetSupportedError" in (result.error_type or "")


def test_submit_explicit_link(store: SophiaGraphMemoryStore) -> None:
    ep = submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="episode",
            payload={"content": "source"},
            provenance=_prov(),
            idempotency_key="idem-src",
        ),
    )
    assert ep.ok and ep.object_id
    envelope = SubmissionEnvelope(
        namespace=_ns(),
        payload_kind="explicit_link",
        payload={
            "source_record_id": ep.object_id,
            "raw_target": "Target Note",
            "link_kind": "wikilink",
            "resolution_status": "unresolved",
        },
        provenance=_prov(),
        idempotency_key="idem-link",
    )
    result = submit_envelope(store, envelope)
    assert result.ok is True


def test_submit_tag_patches_target_record(store: SophiaGraphMemoryStore) -> None:
    ep = submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="episode",
            payload={"content": "needs-tag"},
            provenance=_prov(),
            idempotency_key="idem-ep-tag",
        ),
    )
    result = submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="tag",
            payload={"target_record_id": ep.object_id, "tags": ["urgent"]},
            provenance=_prov(),
            idempotency_key="idem-tag-1",
        ),
    )
    assert result.ok is True
    record = store.get_record(ep.object_id)
    assert record is not None
    assert "urgent" in record.tags


def test_submit_artifact_outcome_document_retrieval(
    store: SophiaGraphMemoryStore,
) -> None:
    for kind, payload in [
        ("outcome", {"content": "tool outcome"}),
        ("artifact", {"content": "artifact-meta"}),
        ("document", {"content": "doc"}),
        ("retrieval_event", {"content": "retrieved x"}),
        ("retrieval_feedback", {"content": "feedback"}),
        ("fact", {"content": "claimed fact"}),
    ]:
        envelope = SubmissionEnvelope(
            namespace=_ns(),
            payload_kind=kind,
            payload=payload,
            provenance=_prov(),
            idempotency_key=f"idem-{kind}",
        )
        result = submit_envelope(store, envelope)
        assert result.ok, f"{kind}: {result.error_message}"


def test_duplicate_idempotency_key_is_deduped(store: SophiaGraphMemoryStore) -> None:
    envelope = SubmissionEnvelope(
        namespace=_ns(),
        payload_kind="episode",
        payload={"content": "x"},
        provenance=_prov(),
        idempotency_key="idem-dup",
    )
    first = submit_envelope(store, envelope)
    second = submit_envelope(store, envelope)
    assert first.ok and not first.deduped
    assert second.ok and second.deduped
    assert second.code == "DEDUPED"


@dataclass
class _BrokenStore:
    raised: list[str] = field(default_factory=list)

    def __getattr__(self, name: str) -> Any:
        def _boom(*_args: Any, **_kwargs: Any) -> Any:
            self.raised.append(name)
            raise RuntimeError(f"boom from {name}")

        return _boom


def test_failure_does_not_raise_by_default() -> None:
    broken = _BrokenStore()
    envelope = SubmissionEnvelope(
        namespace=_ns(),
        payload_kind="episode",
        payload={"content": "x"},
        provenance=_prov(),
        idempotency_key="idem-failing",
    )
    result = submit_envelope(broken, envelope)
    assert result.ok is False
    assert result.code == "BACKEND_FAILURE"
    assert result.error_type == "RuntimeError"
    assert "boom from put_record" in (result.error_message or "")
    assert broken.raised == ["put_record"]


def test_failure_raises_when_caller_opts_in() -> None:
    broken = _BrokenStore()
    envelope = SubmissionEnvelope(
        namespace=_ns(),
        payload_kind="episode",
        payload={"content": "x"},
        provenance=_prov(),
        idempotency_key="idem-failing-raise",
    )
    with pytest.raises(RuntimeError):
        submit_envelope(broken, envelope, raise_on_failure=True)


def test_emit_episode_creates_record_with_turn_provenance(
    store: SophiaGraphMemoryStore,
) -> None:
    result = emit_episode(
        store,
        namespace=_ns(),
        turn_id="t-1",
        payload={"content": "task did x"},
        source_owner="agent",
        idempotency_key="idem-em-ep",
    )
    assert result.ok and result.object_id


def test_emit_tool_outcome(store: SophiaGraphMemoryStore) -> None:
    result = emit_tool_outcome(
        store,
        namespace=_ns(),
        turn_id="t-1",
        tool_call_id="tc-1",
        payload={"content": "outcome"},
        source_owner="execution",
        idempotency_key="idem-em-out",
    )
    assert result.ok


def test_emit_artifact_file_validation_correction_retrieval(
    store: SophiaGraphMemoryStore,
) -> None:
    assert emit_artifact(
        store,
        namespace=_ns(),
        artifact_id="art-1",
        payload={"content": "blob"},
        source_owner="agent",
        idempotency_key="idem-em-art",
    ).ok
    assert emit_file_document(
        store,
        namespace=_ns(),
        file_path="/x.md",
        payload={"content": "file"},
        source_owner="agent",
        idempotency_key="idem-em-file",
    ).ok
    assert emit_validation_outcome(
        store,
        namespace=_ns(),
        validation_command="make check",
        payload={"content": "ok"},
        source_owner="agent",
        idempotency_key="idem-em-val",
    ).ok
    assert emit_user_correction(
        store,
        namespace=_ns(),
        user_correction_id="uc-1",
        payload={"content": "correction", "type": "correction"},
        source_owner="user",
        idempotency_key="idem-em-uc",
    ).ok
    assert emit_retrieval_feedback(
        store,
        namespace=_ns(),
        turn_id="t-1",
        payload={"content": "fb"},
        source_owner="agent",
        idempotency_key="idem-em-fb",
    ).ok


class _FlakyStore:
    def __init__(self, fail_count: int) -> None:
        self._fail_remaining = fail_count
        self._real = SophiaGraphMemoryStore()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def put_record(self, record: Any) -> str:
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise RuntimeError("transient failure")
        return self._real.put_record(record)


def _env(idem: str, content: str = "x") -> SubmissionEnvelope:
    return SubmissionEnvelope(
        namespace=_ns(),
        payload_kind="episode",
        payload={"content": content},
        provenance=_prov(),
        idempotency_key=idem,
    )


def test_queue_retries_until_success() -> None:
    store = _FlakyStore(fail_count=2)
    q = SubmissionQueue(max_attempts=5)
    q.enqueue(_env("idem-q-1"))
    results = q.drain(store)
    assert len(results) == 1
    assert results[0].ok is True
    by_key = [a for a in q.audit if a.envelope_idempotency_key == "idem-q-1"]
    assert len(by_key) == 3
    assert by_key[-1].ok is True


def test_queue_exhausts_on_persistent_failure() -> None:
    store = _FlakyStore(fail_count=99)
    q = SubmissionQueue(max_attempts=2)
    q.enqueue(_env("idem-q-2"))
    results = q.drain(store)
    assert results[0].ok is False
    by_key = [a for a in q.audit if a.envelope_idempotency_key == "idem-q-2"]
    assert len(by_key) == 3
    assert any(a.exhausted for a in by_key)


def test_queue_dedupes_re_enqueued_envelope() -> None:
    store = SophiaGraphMemoryStore()
    q = SubmissionQueue()
    env = _env("idem-q-dup")
    q.enqueue(env)
    q.enqueue(env)
    results = q.drain(store)
    assert len(results) == 2
    codes = sorted(r.code for r in results)
    assert codes == ["DEDUPED", "SUBMITTED"]


def test_queue_does_not_retry_validation_errors() -> None:
    store = SophiaGraphMemoryStore()
    q = SubmissionQueue(max_attempts=5)
    bad_envelope = SubmissionEnvelope(
        namespace=_ns(),
        payload_kind="tag",
        payload={"tags": ["x"]},  # MISSING target_record_id → VALIDATION_ERROR
        provenance=_prov(),
        idempotency_key="idem-q-bad",
    )
    q.enqueue(bad_envelope)
    results = q.drain(store)
    assert results[0].ok is False
    by_key = [a for a in q.audit if a.envelope_idempotency_key == "idem-q-bad"]
    assert len(by_key) == 2


def test_queue_rejects_zero_max_attempts() -> None:
    with pytest.raises(ValueError):
        SubmissionQueue(max_attempts=0)


def test_no_prose_inference_helpers_in_public_surface() -> None:
    from openminion.modules.memory import submissions as mod

    forbidden = {
        "infer_facts_from_prose",
        "summarize_turn",
        "classify_outcome",
        "extract_claims",
        "auto_promote_from_retrieval",
    }
    public = set(mod.__all__)
    assert public & forbidden == set()
