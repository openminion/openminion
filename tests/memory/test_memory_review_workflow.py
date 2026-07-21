from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.portability.models import (
    MemoryBundleExportOptions,
    MemoryBundleImportOptions,
)
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.audit import (
    AuditedMemoryStore,
    InMemoryMemoryAuditSink,
)
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from sophiagraph.portability import MemoryReviewError


def _record(record_id: str, content: str = "source") -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        scope="agent:source",
        type="fact",
        key=f"fact:{record_id}",
        content=content,
        source="user_said",
        created_at="2026-07-20T00:00:00+00:00",
        updated_at="2026-07-20T00:00:00+00:00",
    )


def _service(path: Path, sink=None) -> MemoryService:
    store = SQLiteMemoryStore(path, artifactctl=None)
    return MemoryService(store=AuditedMemoryStore(store, sink=sink))


def _artifact() -> object:
    source = MemoryService(store=InMemoryMemoryStore())
    source._store.put(_record("record-1"))  # noqa: SLF001
    return source.export_review_artifact(
        MemoryBundleExportOptions(scopes=["agent:source"])
    )


def _options() -> MemoryBundleImportOptions:
    return MemoryBundleImportOptions(
        scope_rewrites={"agent:source": "agent:target"},
        trust_mode="direct",
        conflict_mode="error",
        id_mode="preserve",
    )


def test_export_plan_decision_are_read_only_and_approved_apply_mutates(
    tmp_path,
) -> None:
    sink = InMemoryMemoryAuditSink()
    service = _service(tmp_path / "target.db", sink=sink)
    artifact = _artifact()
    options = _options()

    plan = service.plan_review_import(artifact, options)
    rejected = service.decide_review_import(
        plan, reviewer="operator", decision="reject"
    )
    assert service._store.get("record-1") is None  # noqa: SLF001
    with pytest.raises(MemoryReviewError) as exc_info:
        service.apply_review_import(
            artifact, plan, rejected, options, generated_root=tmp_path
        )
    assert exc_info.value.reason_code == "review_rejected"
    assert service._store.get("record-1") is None  # noqa: SLF001

    approved = service.decide_review_import(
        plan, reviewer="operator", decision="approve"
    )
    result = service.apply_review_import(
        artifact, plan, approved, options, generated_root=tmp_path
    )
    assert result.applied is True
    imported = service.get("record-1")
    assert imported.scope == "agent:target"
    assert imported.namespace.agent_id == "target"
    review_events = [
        event for event in sink.events if event.event_type.startswith("memory.review.")
    ]
    assert {event.event_type for event in review_events} >= {
        "memory.review.planned",
        "memory.review.decided",
        "memory.review.applied",
    }
    assert all("source" not in str(event.details) for event in review_events)


def test_apply_rejects_target_and_option_drift_before_mutation(tmp_path) -> None:
    service = _service(tmp_path / "target.db")
    artifact = _artifact()
    options = _options()
    plan = service.plan_review_import(artifact, options)
    receipt = service.decide_review_import(
        plan, reviewer="operator", decision="approve"
    )

    changed_options = MemoryBundleImportOptions(
        scope_rewrites=options.scope_rewrites,
        trust_mode="direct",
        conflict_mode="skip",
        id_mode="preserve",
    )
    with pytest.raises(MemoryReviewError) as exc_info:
        service.apply_review_import(
            artifact, plan, receipt, changed_options, generated_root=tmp_path
        )
    assert exc_info.value.reason_code == "import_options_changed"

    service._store.put(  # noqa: SLF001
        MemoryRecord(
            id="drift",
            scope="agent:target",
            type="fact",
            content="changed",
            created_at="2026-07-20T00:00:00+00:00",
            updated_at="2026-07-20T00:00:00+00:00",
        )
    )
    with pytest.raises(MemoryReviewError) as exc_info:
        service.apply_review_import(
            artifact, plan, receipt, options, generated_root=tmp_path
        )
    assert exc_info.value.reason_code == "target_state_changed"
    assert service._store.get("record-1") is None  # noqa: SLF001


def test_mid_apply_failure_restores_sqlite_target(tmp_path, monkeypatch) -> None:
    sink = InMemoryMemoryAuditSink()
    service = _service(tmp_path / "target.db", sink=sink)
    artifact = _artifact()
    options = _options()
    plan = service.plan_review_import(artifact, options)
    receipt = service.decide_review_import(
        plan, reviewer="operator", decision="approve"
    )
    store = service._store._store  # noqa: SLF001

    original_put = store.put

    def fail_after_write(record):
        original_put(record)
        raise RuntimeError("injected failure")

    monkeypatch.setattr(store, "put", fail_after_write)
    with pytest.raises(MemoryReviewError) as exc_info:
        service.apply_review_import(
            artifact, plan, receipt, options, generated_root=tmp_path
        )
    assert exc_info.value.reason_code == "rollback_succeeded"
    assert service._store.get("record-1") is None  # noqa: SLF001
    assert any(event.event_type == "memory.review.rolled_back" for event in sink.events)


def test_non_sqlite_backend_can_plan_but_not_apply(tmp_path) -> None:
    service = MemoryService(store=InMemoryMemoryStore())
    artifact = _artifact()
    options = _options()
    plan = service.plan_review_import(artifact, options)
    receipt = service.decide_review_import(
        plan, reviewer="operator", decision="approve"
    )

    with pytest.raises(MemoryReviewError) as exc_info:
        service.apply_review_import(
            artifact, plan, receipt, options, generated_root=tmp_path
        )
    assert exc_info.value.reason_code == "unsupported_apply_backend"
    assert service._store.get("record-1") is None  # noqa: SLF001


def test_missing_receipt_and_digest_drift_fail_before_mutation(tmp_path) -> None:
    service = _service(tmp_path / "target.db")
    artifact = _artifact()
    options = _options()
    plan = service.plan_review_import(artifact, options)
    receipt = service.decide_review_import(
        plan, reviewer="operator", decision="approve"
    )

    with pytest.raises(MemoryReviewError) as exc_info:
        service.apply_review_import(
            artifact, plan, None, options, generated_root=tmp_path
        )
    assert exc_info.value.reason_code == "review_required"

    with pytest.raises(MemoryReviewError) as exc_info:
        service.apply_review_import(
            replace(artifact, artifact_sha256="changed"),
            plan,
            receipt,
            options,
            generated_root=tmp_path,
        )
    assert exc_info.value.reason_code == "artifact_digest_mismatch"

    with pytest.raises(MemoryReviewError) as exc_info:
        service.apply_review_import(
            artifact,
            replace(plan, plan_sha256="changed"),
            receipt,
            options,
            generated_root=tmp_path,
        )
    assert exc_info.value.reason_code == "plan_digest_mismatch"
    assert service._store.get("record-1") is None  # noqa: SLF001


def test_rollback_failure_is_reported_without_partial_success(
    tmp_path, monkeypatch
) -> None:
    service = _service(tmp_path / "target.db")
    artifact = _artifact()
    options = _options()
    plan = service.plan_review_import(artifact, options)
    receipt = service.decide_review_import(
        plan, reviewer="operator", decision="approve"
    )
    store = service._store._store  # noqa: SLF001
    original_put = store.put

    def fail_after_write(record):
        original_put(record)
        raise RuntimeError("injected apply failure")

    def fail_restore(path):
        raise RuntimeError("injected rollback failure")

    monkeypatch.setattr(store, "put", fail_after_write)
    monkeypatch.setattr(store, "restore_from", fail_restore)
    with pytest.raises(MemoryReviewError) as exc_info:
        service.apply_review_import(
            artifact, plan, receipt, options, generated_root=tmp_path
        )
    assert exc_info.value.reason_code == "rollback_failed"
