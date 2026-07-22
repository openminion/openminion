"""Reviewed memory import workflow built on Sophiagraph portability contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
import uuid

from sophiagraph.audit.events import MemoryAuditEvent
from sophiagraph.contracts.provenance import TurnProvenanceTrace
from openminion.modules.memory.portability.review_contracts import (
    APPLY_INCOMPLETE,
    ARTIFACT_DIGEST_MISMATCH,
    IMPORT_OPTIONS_CHANGED,
    MEMORY_REVIEW_PLAN_VERSION,
    MEMORY_REVIEW_RECEIPT_VERSION,
    PLAN_DIGEST_MISMATCH,
    REVIEW_REJECTED,
    REVIEW_REQUIRED,
    ROLLBACK_FAILED,
    ROLLBACK_SUCCEEDED,
    TARGET_STATE_CHANGED,
    UNSUPPORTED_APPLY_BACKEND,
    MemoryReviewArtifact,
    MemoryReviewDecisionReceipt,
    MemoryReviewError,
    MemoryReviewOperation,
    MemoryReviewPlan,
    build_memory_review_artifact,
    review_sha256,
)
from sophiagraph.portability.row_codec import (
    candidate_from_dict,
    record_from_dict,
    relation_from_dict,
    tier_transition_from_dict,
)
from sophiagraph.temporal import utc_now_iso

from openminion.base.generated_paths import resolve_generated_root
from openminion.modules.memory.portability.codec import write_bundle_snapshot
from openminion.modules.memory.portability.merger import MemoryMerger
from openminion.modules.memory.portability.models import (
    MemoryBundleExportOptions,
    MemoryBundleImportOptions,
    MemoryBundleImportResult,
    MemoryBundleSnapshot,
)
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
)
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


def _normalized_options(options: MemoryBundleImportOptions) -> dict[str, Any]:
    return {
        "scope_rewrites": dict(sorted(options.scope_rewrites.items())),
        "trust_mode": options.trust_mode,
        "conflict_mode": options.conflict_mode,
        "id_mode": options.id_mode,
        "dry_run": bool(options.dry_run),
        "namespace_allowlist": [
            item.as_dict() for item in options.namespace_allowlist or []
        ],
    }


def _unwrap_store(service: Any) -> Any:
    store = service._store  # noqa: SLF001
    return getattr(store, "_store", store)


def _backend_identity(service: Any) -> tuple[str, str]:
    store = _unwrap_store(service)
    path = getattr(store, "db_path", None)
    return type(store).__name__, str(Path(path).resolve()) if path else type(
        store
    ).__name__


def _target_scopes(
    artifact: MemoryReviewArtifact, options: MemoryBundleImportOptions
) -> list[str]:
    return sorted(
        options.scope_rewrites.get(scope, scope) for scope in artifact.source_scopes
    )


def target_fingerprint(
    service: Any,
    artifact: MemoryReviewArtifact,
    options: MemoryBundleImportOptions,
) -> str:
    scopes = _target_scopes(artifact, options)
    records = service.list(
        ListQueryOptions(scopes=scopes, include_invalidated=True, limit=None)
    )
    record_rows = [
        {
            "id": row.id,
            "scope": row.scope,
            "updated_at": row.updated_at,
            "integrity_hash": row.integrity_hash,
            "is_deleted": row.is_deleted,
            "valid_to": row.valid_to,
            "superseded_by_id": row.superseded_by_id,
        }
        for row in records
    ]
    candidates: list[dict[str, Any]] = []
    for scope in scopes:
        for row in service.candidate_list(
            CandidateListOptions(proposed_scope=scope, status=None, limit=None)
        ):
            candidates.append(
                {
                    "candidate_id": row.candidate_id,
                    "status": row.status,
                    "updated_at": row.updated_at,
                }
            )
    relation_rows: dict[str, dict[str, Any]] = {}
    for record in records:
        for relation in service.list_relations(record_id=record.id, limit=None):
            relation_rows[relation.relation_id] = {
                "relation_id": relation.relation_id,
                "source_record_id": relation.source_record_id,
                "target_record_id": relation.target_record_id,
                "created_at": relation.created_at,
            }
    transitions = [
        {
            "transition_id": row.transition_id,
            "record_id": row.record_id,
            "transition_at": row.transition_at,
        }
        for row in service.list_tier_transitions(scopes=scopes, limit=None)
    ]
    backend, identity = _backend_identity(service)
    return review_sha256(
        {
            "backend": backend,
            "identity": identity,
            "scopes": scopes,
            "records": sorted(record_rows, key=lambda row: row["id"]),
            "candidates": sorted(candidates, key=lambda row: row["candidate_id"]),
            "relations": sorted(
                relation_rows.values(), key=lambda row: row["relation_id"]
            ),
            "tier_transitions": sorted(
                transitions, key=lambda row: row["transition_id"]
            ),
        }
    )


def _source_id(section: str, row: dict[str, Any]) -> str:
    field = {
        "records": "id",
        "candidates": "candidate_id",
        "relations": "relation_id",
        "tier_transitions": "transition_id",
        "provenance_traces": "turn_id",
    }[section]
    return str(row.get(field, ""))


def build_review_plan(
    service: Any,
    artifact: MemoryReviewArtifact,
    options: MemoryBundleImportOptions,
) -> MemoryReviewPlan:
    normalized = _normalized_options(options)
    operations: list[MemoryReviewOperation] = []
    index = 0
    for section, rows in artifact.sections.items():
        for row in rows:
            source_id = _source_id(section, row)
            target_id = source_id
            action = "import"
            disposition = "apply"
            if options.trust_mode == "candidate":
                if section == "records":
                    action = "stage_candidate"
                    target_id = "generated"
                else:
                    action = "skip"
                    disposition = "skip"
            operations.append(
                MemoryReviewOperation(
                    index=index,
                    section=section,
                    action=action,
                    source_id=source_id,
                    target_id=target_id,
                    disposition=disposition,
                )
            )
            index += 1
    backend, identity = _backend_identity(service)
    plan = MemoryReviewPlan(
        version=MEMORY_REVIEW_PLAN_VERSION,
        plan_id=f"mrp_{uuid.uuid4().hex[:12]}",
        created_at=utc_now_iso(),
        artifact_sha256=artifact.artifact_sha256,
        options=normalized,
        options_sha256=review_sha256(normalized),
        target_backend=backend,
        target_identity=identity,
        target_fingerprint=target_fingerprint(service, artifact, options),
        operations=tuple(operations),
        section_summaries=artifact.section_summaries,
        warnings=artifact.warnings,
    )
    return replace(plan, plan_sha256=review_sha256(plan, digest_field="plan_sha256"))


def decide_review_plan(
    plan: MemoryReviewPlan,
    *,
    reviewer: str,
    decision: str,
    note: str | None = None,
) -> MemoryReviewDecisionReceipt:
    if not reviewer.strip() or decision not in {"approve", "reject"}:
        raise MemoryReviewError(
            REVIEW_REQUIRED, "reviewer and approve/reject decision are required"
        )
    receipt = MemoryReviewDecisionReceipt(
        version=MEMORY_REVIEW_RECEIPT_VERSION,
        receipt_id=f"mrr_{uuid.uuid4().hex[:12]}",
        plan_id=plan.plan_id,
        plan_sha256=plan.plan_sha256,
        artifact_sha256=plan.artifact_sha256,
        options_sha256=plan.options_sha256,
        target_fingerprint=plan.target_fingerprint,
        reviewer=reviewer.strip(),
        decision=decision,  # type: ignore[arg-type]
        decided_at=utc_now_iso(),
        note=note,
    )
    return replace(
        receipt,
        receipt_sha256=review_sha256(receipt, digest_field="receipt_sha256"),
    )


def snapshot_from_review_artifact(
    artifact: MemoryReviewArtifact,
) -> MemoryBundleSnapshot:
    records = []
    for item in artifact.sections.get("records", []):
        payload = dict(item)
        payload.pop("review_origin", None)
        records.append(record_from_dict(payload))
    return MemoryBundleSnapshot(
        manifest={
            "bundle_version": artifact.source_bundle_version,
            "memory_contract_version": artifact.memory_contract_version,
            "bundle_id": artifact.bundle_id,
            "source_backend": artifact.source_backend,
            "source_instance": artifact.source_instance,
            "scopes": list(artifact.source_scopes),
        },
        records=records,
        candidates=[
            candidate_from_dict(row) for row in artifact.sections.get("candidates", [])
        ],
        relations=[
            relation_from_dict(row) for row in artifact.sections.get("relations", [])
        ],
        tier_transitions=[
            tier_transition_from_dict(row)
            for row in artifact.sections.get("tier_transitions", [])
        ],
        provenance_traces=[
            TurnProvenanceTrace.from_dict(row)
            for row in artifact.sections.get("provenance_traces", [])
        ],
    )


def emit_review_event(service: Any, event_type: str, **details: Any) -> None:
    append = getattr(service._store, "append_audit_event", None)  # noqa: SLF001
    if append is None:
        return
    append(
        MemoryAuditEvent(
            event_type=event_type,
            target_kind="memory_review",
            target_id=str(
                details.get("plan_id") or details.get("artifact_sha256") or ""
            ),
            details={key: value for key, value in details.items() if value is not None},
        )
    )


def export_review_artifact(
    service: Any, options: MemoryBundleExportOptions
) -> MemoryReviewArtifact:
    artifact = build_memory_review_artifact(service.export_bundle_snapshot(options))
    emit_review_event(
        service,
        "memory.review.exported",
        artifact_sha256=artifact.artifact_sha256,
        bundle_id=artifact.bundle_id,
        counts={item.name: item.count for item in artifact.section_summaries},
    )
    return artifact


def _validate_apply(
    service: Any,
    artifact: MemoryReviewArtifact,
    plan: MemoryReviewPlan,
    receipt: MemoryReviewDecisionReceipt | None,
    options: MemoryBundleImportOptions,
) -> SQLiteMemoryStore:
    if receipt is None:
        raise MemoryReviewError(
            REVIEW_REQUIRED, "an approved review receipt is required"
        )
    if receipt.decision != "approve":
        raise MemoryReviewError(REVIEW_REJECTED, "review receipt rejected the plan")
    if (
        receipt.artifact_sha256 != artifact.artifact_sha256
        or plan.artifact_sha256 != artifact.artifact_sha256
    ):
        raise MemoryReviewError(
            ARTIFACT_DIGEST_MISMATCH, "artifact digest no longer matches approval"
        )
    if receipt.plan_sha256 != plan.plan_sha256 or receipt.plan_id != plan.plan_id:
        raise MemoryReviewError(
            PLAN_DIGEST_MISMATCH, "plan digest no longer matches approval"
        )
    normalized = _normalized_options(options)
    if (
        receipt.options_sha256 != review_sha256(normalized)
        or plan.options != normalized
    ):
        raise MemoryReviewError(
            IMPORT_OPTIONS_CHANGED, "import options changed after planning"
        )
    current_fingerprint = target_fingerprint(service, artifact, options)
    if (
        receipt.target_fingerprint != current_fingerprint
        or plan.target_fingerprint != current_fingerprint
    ):
        raise MemoryReviewError(
            TARGET_STATE_CHANGED, "target memory changed after planning"
        )
    store = _unwrap_store(service)
    if not isinstance(store, SQLiteMemoryStore):
        raise MemoryReviewError(
            UNSUPPORTED_APPLY_BACKEND,
            "reviewed apply supports only the built-in SQLite backend",
        )
    return store


def apply_review_plan(
    service: Any,
    artifact: MemoryReviewArtifact,
    plan: MemoryReviewPlan,
    receipt: MemoryReviewDecisionReceipt,
    options: MemoryBundleImportOptions,
    *,
    generated_root: str | Path | None = None,
) -> MemoryBundleImportResult:
    store = _validate_apply(service, artifact, plan, receipt, options)
    from openminion.modules.memory.runtime.provenance import (
        default_provenance_recorder,
    )

    provenance_recorder = default_provenance_recorder()
    provenance_before = list(provenance_recorder.iter_all_traces())
    root = (
        Path(generated_root) if generated_root is not None else resolve_generated_root()
    )
    backup_dir = root / "memory-review" / plan.plan_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    scopes = _target_scopes(artifact, options)
    backup_snapshot = service.export_bundle_snapshot(
        MemoryBundleExportOptions(
            scopes=scopes,
            include_candidates=True,
            include_relations=True,
            include_tier_history=True,
        )
    )
    write_bundle_snapshot(backup_snapshot, backup_dir / "target-before.tar.gz")
    sqlite_backup = store.backup_to(backup_dir / "target-before.sqlite3")
    try:
        result = MemoryMerger(service).import_snapshot(
            snapshot_from_review_artifact(artifact), options
        )
        if not result.applied:
            raise MemoryReviewError(
                APPLY_INCOMPLETE,
                "reviewed import did not complete",
                details={"conflicts": len(result.conflicts)},
            )
    except (MemoryReviewError, OSError, RuntimeError, TypeError, ValueError) as exc:
        try:
            store.restore_from(sqlite_backup)
            provenance_recorder.clear()
            for trace in provenance_before:
                provenance_recorder.record_turn_trace(trace)
        except (OSError, RuntimeError, TypeError, ValueError) as rollback_exc:
            emit_review_event(
                service,
                "memory.review.failed",
                plan_id=plan.plan_id,
                artifact_sha256=artifact.artifact_sha256,
                reason_code=ROLLBACK_FAILED,
                backup_path=str(backup_dir),
            )
            raise MemoryReviewError(
                ROLLBACK_FAILED,
                "reviewed apply failed and rollback did not complete",
            ) from rollback_exc
        emit_review_event(
            service,
            "memory.review.rolled_back",
            plan_id=plan.plan_id,
            artifact_sha256=artifact.artifact_sha256,
            reason_code=ROLLBACK_SUCCEEDED,
            backup_path=str(backup_dir),
        )
        raise MemoryReviewError(
            ROLLBACK_SUCCEEDED,
            "reviewed apply failed and the target was restored",
        ) from exc
    emit_review_event(
        service,
        "memory.review.applied",
        plan_id=plan.plan_id,
        artifact_sha256=artifact.artifact_sha256,
        reviewer=receipt.reviewer,
        counts={
            "records": result.imported_records,
            "candidates": result.imported_candidates + result.staged_candidates,
            "relations": result.imported_relations,
            "tier_transitions": result.imported_tier_transitions,
        },
        backup_path=str(backup_dir),
    )
    return result


__all__ = [
    "apply_review_plan",
    "build_review_plan",
    "decide_review_plan",
    "emit_review_event",
    "export_review_artifact",
    "snapshot_from_review_artifact",
    "target_fingerprint",
]
