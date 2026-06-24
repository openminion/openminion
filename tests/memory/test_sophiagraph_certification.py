from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.memory.submissions import (
    SubmissionEnvelope,
    SubmissionNamespace,
    SubmissionProvenance,
    reset_idempotency_registry,
    submit_envelope,
)
from sophiagraph import (
    SophiaGraphMemoryStore,
    SophiaGraphSqliteStore,
)
from sophiagraph.models import MemoryNamespace


@pytest.fixture(autouse=True)
def _reset():
    reset_idempotency_registry()
    yield
    reset_idempotency_registry()


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path: Path):
    if request.param == "memory":
        return SophiaGraphMemoryStore()
    return SophiaGraphSqliteStore(tmp_path / "cert.sqlite3")


def _ns() -> SubmissionNamespace:
    return SubmissionNamespace(agent_id="cert", session_id="cert-1")


def _prov() -> SubmissionProvenance:
    return SubmissionProvenance(source_owner="cert-runner", turn_id="t-1")


def test_certification_submit_then_retrieve_round_trip_cross_backend(store) -> None:

    result = submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="document",
            payload={
                "id": "cert-doc-1",
                "content": "certification document body",
                "type": "fact",
            },
            provenance=_prov(),
            idempotency_key="cert-idem-1",
            trust_mode="direct",
        ),
    )
    assert result.ok
    record = store.get_record("cert-doc-1")
    assert record is not None
    assert record.content == "certification document body"


def test_certification_namespace_isolation_through_submissions(store) -> None:

    alpha_ns = SubmissionNamespace(agent_id="alpha", session_id="s")
    beta_ns = SubmissionNamespace(agent_id="beta", session_id="s")
    submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=alpha_ns,
            payload_kind="document",
            payload={"id": "alpha-doc", "content": "alpha", "type": "fact"},
            provenance=_prov(),
            idempotency_key="cert-iso-alpha",
            trust_mode="direct",
        ),
    )
    submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=beta_ns,
            payload_kind="document",
            payload={"id": "beta-doc", "content": "beta", "type": "fact"},
            provenance=_prov(),
            idempotency_key="cert-iso-beta",
            trust_mode="direct",
        ),
    )
    alpha = store.get_record("alpha-doc")
    beta = store.get_record("beta-doc")
    assert alpha is not None and alpha.namespace is not None
    assert beta is not None and beta.namespace is not None
    assert alpha.namespace.agent_id == "alpha"
    assert beta.namespace.agent_id == "beta"


def test_certification_test_file_imports_only_public_sophiagraph_paths() -> None:

    import ast

    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {
        "sophiagraph.storage.memory",
        "sophiagraph.storage.sqlite",
        "sophiagraph.storage.lifecycle_policy",
        "sophiagraph.audit.events",
        "sophiagraph.audit.governance",
        "sophiagraph.audit.policy",
    }
    actual_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            actual_imports.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                actual_imports.add(alias.name)
    leaked = actual_imports & forbidden
    assert not leaked, f"cert test reaches into non-public paths: {sorted(leaked)}"


def test_certification_unused_namespace_for_static_check() -> None:

    assert MemoryNamespace(agent_id="x").agent_id == "x"
