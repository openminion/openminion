from __future__ import annotations

import ast
from pathlib import Path

import pytest

from openminion.modules.memory.submissions import (
    SubmissionEnvelope,
    SubmissionNamespace,
    SubmissionProvenance,
    reset_idempotency_registry,
    submit_envelope,
)
from sophiagraph import SophiaGraphMemoryStore
from sophiagraph.models import ArtifactRecord, MemoryNamespace


_SHA = "c" * 64


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


def _sg_ns() -> MemoryNamespace:
    return MemoryNamespace(agent_id="alpha", session_id="sess-1")


def _prov() -> SubmissionProvenance:
    return SubmissionProvenance(source_owner="task-runner", turn_id="t-1")


def test_openminion_submits_record_then_attaches_artifact_through_public_api(
    store,
) -> None:
    # 1. OpenMinion submits a tool-outcome record.
    submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="document",
            payload={
                "id": "rec-tool-1",
                "content": "ran build; produced screenshot",
                "type": "fact",
            },
            provenance=_prov(),
            idempotency_key="art-rec-1",
            trust_mode="direct",
        ),
    )
    # 2. OpenMinion stores the typed artifact reference for that record.
    artifact = ArtifactRecord(
        artifact_id="art-screenshot-1",
        uri="vault:screenshots/build-output.png",
        sha256=_SHA,
        mime="image/png",
        size_bytes=4096,
        namespace=_sg_ns(),
        source_class="screenshot",
        source_owner="task-runner",
        created_at="2026-05-29T00:00:00+00:00",
        retention="default",
        target_record_id="rec-tool-1",
        provenance={"turn_id": "t-1", "tool_call_id": "tc-1"},
    )
    store.put_artifact(artifact)

    fetched = store.get_artifact("art-screenshot-1")
    assert fetched is not None
    assert fetched.target_record_id == "rec-tool-1"
    assert fetched.provenance["tool_call_id"] == "tc-1"


def test_openminion_can_attach_derived_text_record(store) -> None:

    submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="document",
            payload={
                "id": "rec-derived-1",
                "content": "user-supplied OCR transcription of the screenshot",
                "type": "fact",
            },
            provenance=_prov(),
            idempotency_key="art-derived-1",
            trust_mode="direct",
        ),
    )
    artifact = ArtifactRecord(
        artifact_id="art-doc-1",
        uri="vault:docs/contract.pdf",
        sha256=_SHA,
        mime="application/pdf",
        size_bytes=8192,
        namespace=_sg_ns(),
        source_class="edited_file",
        source_owner="task-runner",
        created_at="2026-05-29T00:00:00+00:00",
        derived_text_record_id="rec-derived-1",
    )
    store.put_artifact(artifact)
    fetched = store.get_artifact("art-doc-1")
    assert fetched is not None
    assert fetched.derived_text_record_id == "rec-derived-1"


def test_openminion_artifact_listing_filters_by_target_record(store) -> None:
    store.put_artifact(
        ArtifactRecord(
            artifact_id="art-A",
            uri="vault:a",
            sha256=_SHA,
            mime="text/plain",
            size_bytes=1,
            namespace=_sg_ns(),
            source_class="tool_output",
            source_owner="x",
            created_at="2026-05-29T00:00:00+00:00",
            target_record_id="rec-A",
        )
    )
    store.put_artifact(
        ArtifactRecord(
            artifact_id="art-B",
            uri="vault:b",
            sha256="d" * 64,
            mime="text/plain",
            size_bytes=2,
            namespace=_sg_ns(),
            source_class="tool_output",
            source_owner="x",
            created_at="2026-05-29T00:00:00+00:00",
            target_record_id="rec-B",
        )
    )
    by_target = store.list_artifacts(target_record_id="rec-A")
    assert [a.artifact_id for a in by_target] == ["art-A"]


def test_artifact_test_file_imports_only_public_paths() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {
        "sophiagraph.storage.memory",
        "sophiagraph.storage.sqlite",
        "sophiagraph.models.artifact",
    }
    actual: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            actual.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                actual.add(alias.name)
    leaked = actual & forbidden
    assert not leaked, f"non-public path import: {sorted(leaked)}"
