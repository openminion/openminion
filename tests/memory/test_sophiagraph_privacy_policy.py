from __future__ import annotations

import ast
from pathlib import Path

from sophiagraph import (
    ConsentState,
    MemoryNamespace,
    MemoryRecord,
    PrivacyPolicyState,
    RedactionPlan,
    RedactionTarget,
    SophiaGraphMemoryStore,
    filter_records_for_retrieval,
    filter_snapshot_for_export,
    privacy_policy_from_record,
    record_with_privacy_policy,
)
from sophiagraph.portability.models import MemoryBundleExportOptions


def _ns() -> MemoryNamespace:
    return MemoryNamespace(tenant_id="tenant", agent_id="openminion", graph_id="main")


def _record(record_id: str, content: dict[str, object]) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        scope="agent:openminion",
        type="fact",
        key=record_id,
        title=record_id,
        content=content,
        created_at="2026-06-14T00:00:00+00:00",
        updated_at="2026-06-14T00:00:00+00:00",
        namespace=_ns(),
        meta={},
    )


def test_openminion_can_apply_typed_privacy_policy_for_retrieval_and_export() -> None:
    store = SophiaGraphMemoryStore()
    policy = PrivacyPolicyState(
        policy_id="privacy-1",
        consent=ConsentState(
            status="granted",
            granted_at="2026-06-14T00:00:00+00:00",
            source_owner="openminion",
        ),
        retrieval_visibility="redacted",
        export_visibility="hidden",
        retention_class="retain_hidden",
        erase_intent="none",
        decision_reason="export_restricted",
        source_owner="openminion",
        applied_at="2026-06-14T00:00:00+00:00",
        redaction_plan=RedactionPlan(
            plan_id="redaction-1",
            reason="export_minimization",
            targets=(
                RedactionTarget(kind="record_content"),
                RedactionTarget(kind="metadata_key", key="email"),
            ),
        ),
    )
    stored = record_with_privacy_policy(
        _record("rec-privacy", {"body": "private", "email": "user@example.com"}),
        policy,
    )
    store.put_record(stored)

    fetched = store.get_record("rec-privacy")
    assert fetched is not None
    fetched_policy = privacy_policy_from_record(fetched)
    assert fetched_policy is not None
    assert fetched_policy.policy_id == "privacy-1"

    retrieval = filter_records_for_retrieval(
        [fetched],
        source_owner="openminion",
    )
    assert [record.id for record in retrieval.records] == ["rec-privacy"]
    assert retrieval.omitted == []
    assert isinstance(retrieval.records[0].content, dict)
    assert retrieval.records[0].content["_redacted"] is True

    snapshot = store.export_snapshot(
        MemoryBundleExportOptions(
            scopes=["agent:openminion"],
            namespaces=[_ns()],
        )
    )
    export = filter_snapshot_for_export(snapshot, source_owner="openminion")
    assert export.snapshot.records == []
    assert [item.record_id for item in export.omitted] == ["rec-privacy"]


def test_privacy_fixture_uses_public_sophiagraph_imports_only() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {
        "sophiagraph.privacy",
        "sophiagraph.models.privacy",
        "sophiagraph.storage.memory",
        "sophiagraph.storage.sqlite",
    }
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
    leaked = imports & forbidden
    assert not leaked, (
        f"fixture reaches into private SophiaGraph paths: {sorted(leaked)}"
    )
