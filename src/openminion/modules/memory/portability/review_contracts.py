"""OpenMinion-owned compatibility contracts for reviewed memory imports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Literal
import uuid

from openminion.base.time import utc_now_iso
from openminion.modules.memory.errors import InvalidArgumentError
from openminion.modules.memory.portability.codec import (
    MEMORY_BUNDLE_VERSION,
    build_manifest,
)
from openminion.modules.memory.portability.models import MemoryBundleSnapshot

MEMORY_REVIEW_ARTIFACT_VERSION = "memory_review.v1"
MEMORY_REVIEW_PLAN_VERSION = "memory_review_plan.v1"
MEMORY_REVIEW_RECEIPT_VERSION = "memory_review_receipt.v1"

UNSUPPORTED_SECTION = "unsupported_section"
ORIGIN_PROVENANCE_REQUIRED = "origin_provenance_required"
REVIEW_REQUIRED = "review_required"
REVIEW_REJECTED = "review_rejected"
ARTIFACT_DIGEST_MISMATCH = "artifact_digest_mismatch"
PLAN_DIGEST_MISMATCH = "plan_digest_mismatch"
TARGET_STATE_CHANGED = "target_state_changed"
IMPORT_OPTIONS_CHANGED = "import_options_changed"
ROLLBACK_SUCCEEDED = "rollback_succeeded"
ROLLBACK_FAILED = "rollback_failed"
UNSUPPORTED_APPLY_BACKEND = "unsupported_apply_backend"
APPLY_INCOMPLETE = "apply_incomplete"
INVALID_REVIEW_DOCUMENT = "invalid_review_document"

SUPPORTED_SECTIONS = (
    "records",
    "candidates",
    "relations",
    "tier_transitions",
    "provenance_traces",
)
UNSUPPORTED_SECTIONS = (
    "memory_blocks",
    "ontologies",
    "active_embedding_model_sets",
    "retention_snapshots",
)


class MemoryReviewError(InvalidArgumentError):
    """Review contract failure with a stable workflow reason code."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message, details={"reason_code": reason_code, **(details or {})}
        )
        self.reason_code = reason_code


@dataclass(frozen=True)
class MemoryReviewSectionSummary:
    name: str
    count: int
    supported: bool = True
    disposition: str = "review"


@dataclass(frozen=True)
class MemoryReviewOperation:
    index: int
    section: str
    action: str
    source_id: str
    target_id: str
    disposition: str = "apply"
    reason_code: str | None = None


@dataclass(frozen=True)
class MemoryReviewArtifact:
    version: str
    generated_at: str
    source_bundle_version: str
    memory_contract_version: str
    bundle_id: str
    bundle_sha256: str
    source_backend: str
    source_instance: str
    source_scopes: tuple[str, ...]
    source_namespaces: tuple[dict[str, Any], ...]
    sections: dict[str, list[dict[str, Any]]]
    section_summaries: tuple[MemoryReviewSectionSummary, ...]
    warnings: tuple[str, ...] = ()
    artifact_sha256: str = ""


@dataclass(frozen=True)
class MemoryReviewPlan:
    version: str
    plan_id: str
    created_at: str
    artifact_sha256: str
    options: dict[str, Any]
    options_sha256: str
    target_backend: str
    target_identity: str
    target_fingerprint: str
    operations: tuple[MemoryReviewOperation, ...]
    section_summaries: tuple[MemoryReviewSectionSummary, ...]
    warnings: tuple[str, ...] = ()
    plan_sha256: str = ""


@dataclass(frozen=True)
class MemoryReviewDecisionReceipt:
    version: str
    receipt_id: str
    plan_id: str
    plan_sha256: str
    artifact_sha256: str
    options_sha256: str
    target_fingerprint: str
    reviewer: str
    decision: Literal["approve", "reject"]
    decided_at: str
    note: str | None = None
    receipt_sha256: str = ""


ReviewDocument = MemoryReviewArtifact | MemoryReviewPlan | MemoryReviewDecisionReceipt


def _json_ready(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_ready(asdict(value))
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_ready(value.to_dict())
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def canonical_review_json(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(
        _json_ready(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":") if indent is None else None,
        indent=indent,
        default=str,
    )


def review_sha256(value: Any, *, digest_field: str | None = None) -> str:
    payload = _json_ready(value)
    if digest_field and isinstance(payload, dict):
        payload = dict(payload)
        payload[digest_field] = ""
    return sha256(canonical_review_json(payload).encode("utf-8")).hexdigest()


def _row_id(section: str, row: dict[str, Any]) -> str:
    fields = {
        "records": "id",
        "candidates": "candidate_id",
        "relations": "relation_id",
        "tier_transitions": "transition_id",
        "provenance_traces": "turn_id",
    }
    return str(row.get(fields[section], ""))


def _section_rows(snapshot: MemoryBundleSnapshot) -> dict[str, list[dict[str, Any]]]:
    sections: dict[str, list[dict[str, Any]]] = {}
    for section in SUPPORTED_SECTIONS:
        rows = [_json_ready(row) for row in getattr(snapshot, section)]
        sections[section] = sorted(rows, key=lambda row: _row_id(section, row))
    return sections


def _validate_supported_sections(snapshot: MemoryBundleSnapshot) -> None:
    non_empty = [name for name in UNSUPPORTED_SECTIONS if getattr(snapshot, name)]
    if non_empty:
        raise MemoryReviewError(
            UNSUPPORTED_SECTION,
            "memory review v1 does not support one or more non-empty sections",
            details={"sections": non_empty},
        )


def _origin_for_record(
    row: dict[str, Any], *, bundle_id: str, bundle_sha256: str
) -> dict[str, Any]:
    scope = str(row.get("scope", ""))
    namespace = row.get("namespace")
    if not isinstance(namespace, dict) or not namespace:
        kind, value = scope.split(":", 1)
        namespace = {f"{kind if kind != 'global' else 'graph'}_id": value}
    source = str(row.get("source", ""))
    record_id = str(row.get("id", ""))
    if not all((record_id, scope, source, bundle_id, bundle_sha256)):
        raise MemoryReviewError(
            ORIGIN_PROVENANCE_REQUIRED,
            "reviewable record is missing structural origin provenance",
            details={"record_id": record_id},
        )
    return {
        "source_record_id": record_id,
        "source_scope": scope,
        "source_namespace": namespace,
        "record_source": source,
        "source_bundle_id": bundle_id,
        "source_bundle_sha256": bundle_sha256,
    }


def build_memory_review_artifact(
    snapshot: MemoryBundleSnapshot,
) -> MemoryReviewArtifact:
    _validate_supported_sections(snapshot)
    sections = _section_rows(snapshot)
    manifest = build_manifest(snapshot=snapshot)
    bundle_id = str(manifest.get("bundle_id", "")) or uuid.uuid4().hex
    bundle_payload = {
        "manifest": manifest,
        "sections": sections,
    }
    bundle_sha256 = review_sha256(bundle_payload)
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in sections["records"]:
        item = dict(row)
        item["review_origin"] = _origin_for_record(
            row, bundle_id=bundle_id, bundle_sha256=bundle_sha256
        )
        if not row.get("evidence_refs"):
            warnings.append(f"record_without_evidence_refs:{row.get('id', '')}")
        records.append(item)
    sections["records"] = records
    summaries = tuple(
        MemoryReviewSectionSummary(name=name, count=len(rows))
        for name, rows in sections.items()
    )
    namespaces = {
        canonical_review_json(row.get("review_origin", {}).get("source_namespace", {}))
        for row in records
    }
    artifact = MemoryReviewArtifact(
        version=MEMORY_REVIEW_ARTIFACT_VERSION,
        generated_at=utc_now_iso(),
        source_bundle_version=str(
            manifest.get("bundle_version", MEMORY_BUNDLE_VERSION)
        ),
        memory_contract_version=str(manifest.get("memory_contract_version", "")),
        bundle_id=bundle_id,
        bundle_sha256=bundle_sha256,
        source_backend=str(manifest.get("source_backend", "unknown")),
        source_instance=str(manifest.get("source_instance", "unknown")),
        source_scopes=tuple(sorted({str(row.get("scope", "")) for row in records})),
        source_namespaces=tuple(json.loads(item) for item in sorted(namespaces)),
        sections=sections,
        section_summaries=summaries,
        warnings=tuple(sorted(warnings)),
    )
    return replace(
        artifact,
        artifact_sha256=review_sha256(artifact, digest_field="artifact_sha256"),
    )


def _summary_from_dict(payload: dict[str, Any]) -> MemoryReviewSectionSummary:
    return MemoryReviewSectionSummary(
        name=str(payload.get("name", "")),
        count=max(0, int(payload.get("count", 0))),
        supported=bool(payload.get("supported", True)),
        disposition=str(payload.get("disposition", "review")),
    )


def _operation_from_dict(payload: dict[str, Any]) -> MemoryReviewOperation:
    return MemoryReviewOperation(
        index=max(0, int(payload.get("index", 0))),
        section=str(payload.get("section", "")),
        action=str(payload.get("action", "")),
        source_id=str(payload.get("source_id", "")),
        target_id=str(payload.get("target_id", "")),
        disposition=str(payload.get("disposition", "apply")),
        reason_code=str(payload["reason_code"])
        if payload.get("reason_code") is not None
        else None,
    )


def artifact_from_dict(payload: dict[str, Any]) -> MemoryReviewArtifact:
    if payload.get("version") != MEMORY_REVIEW_ARTIFACT_VERSION:
        raise MemoryReviewError(
            INVALID_REVIEW_DOCUMENT, "unsupported review artifact version"
        )
    artifact = MemoryReviewArtifact(
        version=str(payload["version"]),
        generated_at=str(payload.get("generated_at", "")),
        source_bundle_version=str(payload.get("source_bundle_version", "")),
        memory_contract_version=str(payload.get("memory_contract_version", "")),
        bundle_id=str(payload.get("bundle_id", "")),
        bundle_sha256=str(payload.get("bundle_sha256", "")),
        source_backend=str(payload.get("source_backend", "")),
        source_instance=str(payload.get("source_instance", "")),
        source_scopes=tuple(str(item) for item in payload.get("source_scopes", [])),
        source_namespaces=tuple(
            dict(item) for item in payload.get("source_namespaces", [])
        ),
        sections={
            str(key): [dict(row) for row in rows]
            for key, rows in dict(payload.get("sections", {})).items()
        },
        section_summaries=tuple(
            _summary_from_dict(dict(item))
            for item in payload.get("section_summaries", [])
        ),
        warnings=tuple(str(item) for item in payload.get("warnings", [])),
        artifact_sha256=str(payload.get("artifact_sha256", "")),
    )
    if (
        review_sha256(artifact, digest_field="artifact_sha256")
        != artifact.artifact_sha256
    ):
        raise MemoryReviewError(
            ARTIFACT_DIGEST_MISMATCH, "review artifact digest mismatch"
        )
    for row in artifact.sections.get("records", []):
        origin = row.get("review_origin")
        required = (
            "source_record_id",
            "source_scope",
            "source_namespace",
            "record_source",
            "source_bundle_id",
            "source_bundle_sha256",
        )
        if not isinstance(origin, dict) or any(
            not origin.get(field) for field in required
        ):
            raise MemoryReviewError(
                ORIGIN_PROVENANCE_REQUIRED,
                "reviewable record is missing structural origin provenance",
            )
    return artifact


def plan_from_dict(payload: dict[str, Any]) -> MemoryReviewPlan:
    if payload.get("version") != MEMORY_REVIEW_PLAN_VERSION:
        raise MemoryReviewError(
            INVALID_REVIEW_DOCUMENT, "unsupported review plan version"
        )
    plan = MemoryReviewPlan(
        version=str(payload["version"]),
        plan_id=str(payload.get("plan_id", "")),
        created_at=str(payload.get("created_at", "")),
        artifact_sha256=str(payload.get("artifact_sha256", "")),
        options=dict(payload.get("options", {})),
        options_sha256=str(payload.get("options_sha256", "")),
        target_backend=str(payload.get("target_backend", "")),
        target_identity=str(payload.get("target_identity", "")),
        target_fingerprint=str(payload.get("target_fingerprint", "")),
        operations=tuple(
            _operation_from_dict(dict(item)) for item in payload.get("operations", [])
        ),
        section_summaries=tuple(
            _summary_from_dict(dict(item))
            for item in payload.get("section_summaries", [])
        ),
        warnings=tuple(str(item) for item in payload.get("warnings", [])),
        plan_sha256=str(payload.get("plan_sha256", "")),
    )
    if review_sha256(plan, digest_field="plan_sha256") != plan.plan_sha256:
        raise MemoryReviewError(PLAN_DIGEST_MISMATCH, "review plan digest mismatch")
    return plan


def receipt_from_dict(payload: dict[str, Any]) -> MemoryReviewDecisionReceipt:
    if payload.get("version") != MEMORY_REVIEW_RECEIPT_VERSION:
        raise MemoryReviewError(
            INVALID_REVIEW_DOCUMENT, "unsupported review receipt version"
        )
    decision = str(payload.get("decision", ""))
    if decision not in {"approve", "reject"}:
        raise MemoryReviewError(INVALID_REVIEW_DOCUMENT, "invalid review decision")
    receipt = MemoryReviewDecisionReceipt(
        version=str(payload["version"]),
        receipt_id=str(payload.get("receipt_id", "")),
        plan_id=str(payload.get("plan_id", "")),
        plan_sha256=str(payload.get("plan_sha256", "")),
        artifact_sha256=str(payload.get("artifact_sha256", "")),
        options_sha256=str(payload.get("options_sha256", "")),
        target_fingerprint=str(payload.get("target_fingerprint", "")),
        reviewer=str(payload.get("reviewer", "")),
        decision=decision,  # type: ignore[arg-type]
        decided_at=str(payload.get("decided_at", "")),
        note=str(payload["note"]) if payload.get("note") is not None else None,
        receipt_sha256=str(payload.get("receipt_sha256", "")),
    )
    if review_sha256(receipt, digest_field="receipt_sha256") != receipt.receipt_sha256:
        raise MemoryReviewError(
            INVALID_REVIEW_DOCUMENT, "review receipt digest mismatch"
        )
    return receipt


def _read_json_object(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve(strict=False)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MemoryReviewError(
            INVALID_REVIEW_DOCUMENT, "review input must be canonical JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise MemoryReviewError(
            INVALID_REVIEW_DOCUMENT, "review document must be a JSON object"
        )
    return payload


def read_review_artifact(path: str | Path) -> MemoryReviewArtifact:
    return artifact_from_dict(_read_json_object(path))


def read_review_plan(path: str | Path) -> MemoryReviewPlan:
    return plan_from_dict(_read_json_object(path))


def read_review_receipt(path: str | Path) -> MemoryReviewDecisionReceipt:
    return receipt_from_dict(_read_json_object(path))


def write_review_document(document: ReviewDocument, path: str | Path) -> Path:
    out = Path(path).expanduser().resolve(strict=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(canonical_review_json(document, indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        out.chmod(0o600)
    return out


def render_review_markdown(artifact: MemoryReviewArtifact) -> str:
    rows = [
        "# Memory Review",
        "",
        f"Artifact digest: `{artifact.artifact_sha256}`",
        f"Bundle: `{artifact.bundle_id}` (`{artifact.bundle_sha256}`)",
        f"Source: `{artifact.source_backend}` / `{artifact.source_instance}`",
        "",
        "## Sections",
        "",
    ]
    rows.extend(
        f"- `{summary.name}`: {summary.count} ({summary.disposition})"
        for summary in artifact.section_summaries
    )
    if artifact.warnings:
        rows.extend(("", "## Warnings", ""))
        rows.extend(f"- `{warning}`" for warning in artifact.warnings)
    rows.extend(
        (
            "",
            "This Markdown is display-only. Apply uses the canonical JSON artifact.",
            "",
        )
    )
    return "\n".join(rows)


def write_review_markdown(artifact: MemoryReviewArtifact, path: str | Path) -> Path:
    out = Path(path).expanduser().resolve(strict=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_review_markdown(artifact), encoding="utf-8")
    if os.name != "nt":
        out.chmod(0o600)
    return out


__all__ = [
    "APPLY_INCOMPLETE",
    "ARTIFACT_DIGEST_MISMATCH",
    "IMPORT_OPTIONS_CHANGED",
    "INVALID_REVIEW_DOCUMENT",
    "MEMORY_REVIEW_ARTIFACT_VERSION",
    "MEMORY_REVIEW_PLAN_VERSION",
    "MEMORY_REVIEW_RECEIPT_VERSION",
    "MemoryReviewArtifact",
    "MemoryReviewDecisionReceipt",
    "MemoryReviewError",
    "MemoryReviewOperation",
    "MemoryReviewPlan",
    "MemoryReviewSectionSummary",
    "ORIGIN_PROVENANCE_REQUIRED",
    "PLAN_DIGEST_MISMATCH",
    "REVIEW_REJECTED",
    "REVIEW_REQUIRED",
    "ROLLBACK_FAILED",
    "ROLLBACK_SUCCEEDED",
    "SUPPORTED_SECTIONS",
    "TARGET_STATE_CHANGED",
    "UNSUPPORTED_APPLY_BACKEND",
    "UNSUPPORTED_SECTION",
    "artifact_from_dict",
    "build_memory_review_artifact",
    "canonical_review_json",
    "plan_from_dict",
    "read_review_artifact",
    "read_review_plan",
    "read_review_receipt",
    "receipt_from_dict",
    "render_review_markdown",
    "review_sha256",
    "write_review_document",
    "write_review_markdown",
]
