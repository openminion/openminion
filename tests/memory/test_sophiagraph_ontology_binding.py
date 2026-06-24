from __future__ import annotations

import pytest

from openminion.modules.memory.submissions import (
    OntologyBinding,
    SubmissionEnvelope,
    SubmissionEnvelopeError,
    SubmissionNamespace,
    SubmissionProvenance,
    reset_idempotency_registry,
    submit_envelope,
)
from sophiagraph import SophiaGraphMemoryStore
from sophiagraph.models import (
    CategorySchema,
    EntityTypeSchema,
    MemoryNamespace,
    OntologyDefinition,
    PropertySchema,
)
from sophiagraph.storage.ontology_validator import validate_record_for_ontology


@pytest.fixture(autouse=True)
def _reset():
    reset_idempotency_registry()
    yield
    reset_idempotency_registry()


@pytest.fixture
def store() -> SophiaGraphMemoryStore:
    s = SophiaGraphMemoryStore()
    s.put_ontology(
        OntologyDefinition(
            ontology_id="coding",
            version="1.0.0",
            owner="openminion-coding",
            namespace=MemoryNamespace(agent_id="alpha"),
            categories=[CategorySchema(name="Function")],
            entity_types=[
                EntityTypeSchema(
                    name="Function",
                    properties=[
                        PropertySchema(
                            name="language",
                            value_type="str",
                            required=True,
                        )
                    ],
                )
            ],
        )
    )
    return s


def _ns() -> SubmissionNamespace:
    return SubmissionNamespace(agent_id="alpha", session_id="sess-1")


def _prov() -> SubmissionProvenance:
    return SubmissionProvenance(source_owner="task-runner", turn_id="t-1")


# OntologyBinding validation


def test_ontology_binding_requires_id_and_version() -> None:
    OntologyBinding(ontology_id="coding", version="1.0.0")
    with pytest.raises(SubmissionEnvelopeError):
        OntologyBinding(ontology_id="", version="1.0.0")
    with pytest.raises(SubmissionEnvelopeError):
        OntologyBinding(ontology_id="coding", version="")


def test_envelope_serializes_ontology_binding_in_as_dict() -> None:
    envelope = SubmissionEnvelope(
        namespace=_ns(),
        payload_kind="document",
        payload={"content": "x"},
        provenance=_prov(),
        idempotency_key="idem-1",
        ontology_binding=OntologyBinding(ontology_id="coding", version="1.0.0"),
    )
    payload = envelope.as_dict()
    assert payload["ontology_binding"] == {
        "ontology_id": "coding",
        "version": "1.0.0",
    }


def test_envelope_without_binding_carries_null_binding() -> None:
    envelope = SubmissionEnvelope(
        namespace=_ns(),
        payload_kind="document",
        payload={"content": "x"},
        provenance=_prov(),
        idempotency_key="idem-2",
    )
    assert envelope.as_dict()["ontology_binding"] is None


# SOCC-04 — binding propagates to record.meta


def test_binding_propagates_to_record_meta(store) -> None:
    result = submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="document",
            payload={
                "id": "rec-fn-1",
                "content": "function body",
                "type": "fact",
                "meta": {
                    "ontology_category": "Function",
                    "properties": {"language": "python"},
                },
            },
            provenance=_prov(),
            idempotency_key="idem-binding-1",
            trust_mode="direct",
            ontology_binding=OntologyBinding(ontology_id="coding", version="1.0.0"),
        ),
    )
    assert result.ok
    record = store.get_record("rec-fn-1")
    assert record is not None
    assert record.meta["ontology_id"] == "coding"
    assert record.meta["ontology_version"] == "1.0.0"
    # And it validates against the stored ontology.
    validate_record_for_ontology(store, record, ontology_id="coding", version="1.0.0")


def test_envelope_payload_meta_overrides_binding_when_explicit(store) -> None:
    submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="document",
            payload={
                "id": "rec-fn-2",
                "content": "x",
                "type": "fact",
                "meta": {"ontology_id": "research", "ontology_version": "9"},
            },
            provenance=_prov(),
            idempotency_key="idem-binding-2",
            trust_mode="direct",
            ontology_binding=OntologyBinding(ontology_id="coding", version="1.0.0"),
        ),
    )
    record = store.get_record("rec-fn-2")
    assert record is not None
    # Explicit meta wins over the envelope binding.
    assert record.meta["ontology_id"] == "research"
    assert record.meta["ontology_version"] == "9"


# Anti-LLM boundary


def test_no_prose_inference_helpers_for_ontology_binding() -> None:
    from openminion.modules.memory import submissions as mod

    forbidden = {
        "infer_ontology_from_prose",
        "guess_ontology_id",
        "auto_bind_from_content",
    }
    assert set(mod.__all__) & forbidden == set()
