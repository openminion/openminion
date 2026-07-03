from __future__ import annotations

from typing import Mapping, Sequence

import pytest

from openminion.modules.memory.submissions import (
    SubmissionEnvelope,
    SubmissionNamespace,
    SubmissionProvenance,
    reset_idempotency_registry,
    submit_envelope,
)
from sophiagraph import SophiaGraphMemoryStore
from sophiagraph.query import (
    ContextBudget,
    ContextRequest,
    GlobalMode,
    HybridMode,
    StructuralSearchMode,
    assemble_context,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_idempotency_registry()
    yield
    reset_idempotency_registry()


@pytest.fixture
def store() -> SophiaGraphMemoryStore:
    return SophiaGraphMemoryStore()


def _ns() -> SubmissionNamespace:
    return SubmissionNamespace(agent_id="alpha", session_id="sess-1")


def _prov(turn: str = "t-1") -> SubmissionProvenance:
    return SubmissionProvenance(source_owner="task-runner", turn_id=turn)


def _seed_records_via_omss(store) -> None:
    for fid, content in (
        ("rec-task-1", "Investigate failing tracker for sophiagraph."),
        ("rec-task-2", "Run make check for sophiagraph package."),
        ("rec-task-3", "Update docs for context assembly lane."),
    ):
        submit_envelope(
            store,
            SubmissionEnvelope(
                namespace=_ns(),
                payload_kind="document",
                payload={
                    "id": fid,
                    "content": content,
                    "type": "fact",
                    "title": f"Task note {fid}",
                },
                provenance=_prov(),
                idempotency_key=f"idem-{fid}",
                trust_mode="direct",
            ),
        )


def test_openminion_assembles_structural_context_package(store) -> None:
    _seed_records_via_omss(store)

    package = assemble_context(
        store,
        ContextRequest(
            scopes=["session:sess-1"],
            mode="structural_search",
            structural_search=StructuralSearchMode(query="sophiagraph"),
            budget=ContextBudget(max_items=5),
        ),
    )
    record_ids = {item.item_id for item in package.items}
    assert "rec-task-1" in record_ids
    assert "rec-task-2" in record_ids
    for item in package.items:
        assert item.scores
        assert item.provenance["record_id"] == item.item_id


def test_openminion_attaches_vector_scores_and_summary_references(store) -> None:
    _seed_records_via_omss(store)

    submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="document",
            payload={
                "id": "sum-roadmap-1",
                "content": "Caller-authored summary of the roadmap.",
                "title": "Roadmap summary",
                "type": "summary",
            },
            provenance=_prov(),
            idempotency_key="idem-sum-1",
            trust_mode="direct",
        ),
    )

    def vector_scores(ids: Sequence[str]) -> Mapping[str, float]:
        return {rid: 0.5 for rid in ids}

    hybrid_package = assemble_context(
        store,
        ContextRequest(
            scopes=["session:sess-1"],
            mode="hybrid",
            hybrid=HybridMode(seed_query="sophiagraph"),
        ),
        vector_score_lookup=vector_scores,
    )
    assert hybrid_package.request_provenance["vector_adapter_attached"] is True

    global_package = assemble_context(
        store,
        ContextRequest(
            scopes=["session:sess-1"],
            mode="global",
            global_mode=GlobalMode(summary_record_ids=["sum-roadmap-1"]),
        ),
    )
    assert {item.item_id for item in global_package.items} == {"sum-roadmap-1"}
    for item in global_package.items:
        assert item.kind == "summary_reference"


def test_sophiagraph_context_assembly_does_not_import_openminion() -> None:
    import inspect

    from sophiagraph.query import context_assembly

    source = inspect.getsource(context_assembly)
    assert "openminion" not in source, (
        "sophiagraph.query.context_assembly must not reference openminion; "
        "SGCARM-06 import boundary"
    )
