from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openminion.base.time import utc_now_iso as iso_now  # noqa: F401

from . import constants as _artifact_constants


ARTIFACT_REF_PREFIX = "artifact://sha256/"
VALID_OWNER_TYPES = _artifact_constants.VALID_OWNER_TYPES
_HEX_DIGITS = frozenset("0123456789abcdef")


def sha_to_ref(sha256: str) -> str:
    return f"{ARTIFACT_REF_PREFIX}{sha256}"


def parse_ref_or_sha(ref_or_sha: str) -> str:
    raw = (ref_or_sha or "").strip()
    candidate = raw.removeprefix(ARTIFACT_REF_PREFIX)
    if len(candidate) != 64:
        raise ValueError(f"Expected sha256/ref, got: {raw}")
    lowered = candidate.lower()
    if not set(lowered) <= _HEX_DIGITS:
        raise ValueError(f"Invalid sha256: {candidate}")
    return lowered


@dataclass
class ArtifactRef:
    ref: str
    sha256: str
    mime: str
    size_bytes: int
    created_at: str
    label: str | None = None
    meta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ref": self.ref,
            "sha256": self.sha256,
            "mime": self.mime,
            "size_bytes": int(self.size_bytes),
            "created_at": self.created_at,
        }
        if self.label is not None:
            out["label"] = self.label
        if self.meta is not None:
            out["meta"] = self.meta
        return out


@dataclass
class ArtifactMeta:
    sha256: str
    size_bytes: int
    mime: str
    created_at: str
    original_name: str | None = None
    original_path: str | None = None
    label: str | None = None
    session_id: str | None = None
    trace_id: str | None = None
    agent_id: str | None = None
    encoding: str | None = None
    deleted_at: str | None = None
    meta_json: dict[str, Any] | None = None

    def to_ref(self) -> ArtifactRef:
        return ArtifactRef(
            ref=sha_to_ref(self.sha256),
            sha256=self.sha256,
            mime=self.mime,
            size_bytes=self.size_bytes,
            created_at=self.created_at,
            label=self.label,
            meta=self.meta_json,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "ref": sha_to_ref(self.sha256),
            "size_bytes": self.size_bytes,
            "mime": self.mime,
            "created_at": self.created_at,
            "original_name": self.original_name,
            "original_path": self.original_path,
            "label": self.label,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "agent_id": self.agent_id,
            "encoding": self.encoding,
            "deleted_at": self.deleted_at,
            "meta": self.meta_json,
        }


@dataclass
class ViewRecord:
    raw_sha256: str
    view_type: str
    schema_version: str
    view_sha256: str | None
    view_path: str | None
    mime: str
    size_bytes: int
    created_at: str
    deleted_at: str | None = None
    policy_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_sha256": self.raw_sha256,
            "view_type": self.view_type,
            "schema_version": self.schema_version,
            "view_sha256": self.view_sha256,
            "view_path": self.view_path,
            "mime": self.mime,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "deleted_at": self.deleted_at,
            "policy_hash": self.policy_hash,
        }


@dataclass
class AliasRecord:
    alias: str
    sha256: str
    updated_at: str
    expires_at: str | None = None
    meta_json: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "sha256": self.sha256,
            "ref": sha_to_ref(self.sha256),
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "meta": self.meta_json,
        }


@dataclass
class ReferenceEdge:
    ref_id: str
    owner_type: str
    owner_id: str
    sha256: str
    created_at: str
    deleted_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_id": self.ref_id,
            "owner_type": self.owner_type,
            "owner_id": self.owner_id,
            "sha256": self.sha256,
            "created_at": self.created_at,
            "deleted_at": self.deleted_at,
        }


@dataclass
class GCReport:
    keep_days: int
    delete_unreferenced_after_days: int
    roots: int
    protected: int
    eligible: int
    marked_deleted: int
    candidates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "keep_days": self.keep_days,
            "delete_unreferenced_after_days": self.delete_unreferenced_after_days,
            "roots": self.roots,
            "protected": self.protected,
            "eligible": self.eligible,
            "marked_deleted": self.marked_deleted,
            "candidates": list(self.candidates),
        }


@dataclass
class PurgeReport:
    grace_days: int
    purged_views: int
    purged_blobs: int
    missing_files: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "grace_days": self.grace_days,
            "purged_views": self.purged_views,
            "purged_blobs": self.purged_blobs,
            "missing_files": self.missing_files,
        }


@dataclass
class VerifyIssue:
    sha256: str
    issue: str
    expected_sha256: str | None = None
    actual_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "issue": self.issue,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
        }


@dataclass
class VerifyReport:
    checked: int
    ok: int
    failed: int
    issues: list[VerifyIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "ok": self.ok,
            "failed": self.failed,
            "issues": [item.to_dict() for item in self.issues],
        }
