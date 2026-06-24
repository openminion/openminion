from __future__ import annotations

from dataclasses import MISSING, fields, is_dataclass
import json
import os
from pathlib import Path
from typing import Any, Literal, TypedDict, get_args, get_origin
import types
import unittest

from openminion.modules.memory.audit.trust_gate import (
    TrustGateDecision,
    TrustGateEvent,
    TrustGateReasonCode,
)
from openminion.modules.memory.models import (
    ArtifactRef,
    CandidateReview,
    CandidateStatus,
    MemoryCandidate,
    MemoryPatchResult,
    MemoryRecord,
    MemoryRelation,
    MemoryRelationType,
    MemoryScope,
    MemorySource,
    MemoryTier,
    MemoryTierTransition,
    MemoryTierTransitionReason,
    MemoryType,
    RecordVisibility,
    RetrievalFilters,
    ScopeKind,
    SessionSummaryActiveThread,
    SessionSummaryContent,
    SessionSummaryOutcome,
    SessionSummaryThreadStatus,
)
from openminion.modules.memory.portability.models import (
    MemoryBundleExportOptions,
    MemoryBundleImportOptions,
    MemoryBundleImportResult,
    MemoryBundleSnapshot,
)
from openminion.modules.memory.storage.audit import MemoryAuditEvent
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
    RecordOrder,
    SearchQueryOptions,
)
from openminion.modules.memory.trust.types import ClaimKeyPolarity, MemorySourceClass

SNAPSHOT_FILE = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "trackers"
    / "artifacts"
    / "memory-snapshots"
    / "kce00_public_surface_v1.json"
)
UPDATE_ENV_VAR = "OPENMINION_KCE_SURFACE_UPDATE"
SNAPSHOT_SCHEMA_VERSION = "1"


class _SurfaceTypedDictSpec(TypedDict):
    kind: str
    required_keys: list[str]
    optional_keys: list[str]
    annotations: dict[str, Any]


def _is_update_requested() -> bool:
    return os.environ.get(UPDATE_ENV_VAR, "").strip().lower() in {"1", "true", "yes"}


def _render_annotation(annotation: Any) -> Any:
    if annotation is Any:
        return "Any"
    if annotation is None or annotation is type(None):  # noqa: E721
        return "None"
    origin = get_origin(annotation)
    if origin is Literal:
        return {"literal": [_render_annotation(arg) for arg in get_args(annotation)]}
    if origin in (list, set, tuple):
        label = getattr(origin, "__name__", str(origin))
        return {label: [_render_annotation(arg) for arg in get_args(annotation)]}
    if origin is dict:
        key, value = get_args(annotation)
        return {"dict": [_render_annotation(key), _render_annotation(value)]}
    if origin in (types.UnionType, None) and hasattr(annotation, "__args__"):
        return {"union": [_render_annotation(arg) for arg in annotation.__args__]}
    if origin is not None:
        return {
            getattr(origin, "__name__", str(origin)): [
                _render_annotation(arg) for arg in get_args(annotation)
            ]
        }
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    return str(annotation).replace("typing.", "")


def _dataclass_surface(cls: type[Any]) -> dict[str, Any]:
    assert is_dataclass(cls)
    field_entries: list[dict[str, Any]] = []
    for field_def in fields(cls):
        if field_def.default is not MISSING:
            default_kind = "value"
        elif field_def.default_factory is not MISSING:  # type: ignore[comparison-overlap]
            default_kind = "factory"
        else:
            default_kind = "required"
        field_entries.append(
            {
                "name": field_def.name,
                "annotation": _render_annotation(field_def.type),
                "default_kind": default_kind,
            }
        )
    return {"kind": "dataclass", "fields": field_entries}


def _typed_dict_surface(cls: type[Any]) -> _SurfaceTypedDictSpec:
    required = sorted(str(key) for key in getattr(cls, "__required_keys__", set()))
    optional = sorted(str(key) for key in getattr(cls, "__optional_keys__", set()))
    annotations = {
        key: _render_annotation(value)
        for key, value in sorted(getattr(cls, "__annotations__", {}).items())
    }
    return {
        "kind": "typed_dict",
        "required_keys": required,
        "optional_keys": optional,
        "annotations": annotations,
    }


def _literal_surface(name: str, annotation: Any) -> dict[str, Any]:
    return {
        "kind": "literal",
        "name": name,
        "values": [_render_annotation(v) for v in get_args(annotation)],
    }


def _enum_surface(enum_cls: type[Any]) -> dict[str, Any]:
    return {
        "kind": "enum",
        "values": [member.value for member in enum_cls],
    }


def _capture_surface() -> dict[str, Any]:
    models = {
        name: _dataclass_surface(cls)
        for name, cls in sorted(
            {
                "ArtifactRef": ArtifactRef,
                "CandidateReview": CandidateReview,
                "MemoryScope": MemoryScope,
                "MemoryRecord": MemoryRecord,
                "MemoryCandidate": MemoryCandidate,
                "MemoryRelation": MemoryRelation,
                "MemoryTierTransition": MemoryTierTransition,
                "RetrievalFilters": RetrievalFilters,
                "MemoryPatchResult": MemoryPatchResult,
            }.items()
        )
    }
    portability = {
        name: _dataclass_surface(cls)
        for name, cls in sorted(
            {
                "MemoryBundleExportOptions": MemoryBundleExportOptions,
                "MemoryBundleImportOptions": MemoryBundleImportOptions,
                "MemoryBundleSnapshot": MemoryBundleSnapshot,
                "MemoryBundleImportResult": MemoryBundleImportResult,
            }.items()
        )
    }
    audit = {
        name: _dataclass_surface(cls)
        for name, cls in sorted(
            {
                "MemoryAuditEvent": MemoryAuditEvent,
                "TrustGateEvent": TrustGateEvent,
            }.items()
        )
    }
    query_dtos = {
        name: _dataclass_surface(cls)
        for name, cls in sorted(
            {
                "ListQueryOptions": ListQueryOptions,
                "SearchQueryOptions": SearchQueryOptions,
                "CandidateListOptions": CandidateListOptions,
            }.items()
        )
    }
    typed_dicts = {
        name: _typed_dict_surface(cls)
        for name, cls in sorted(
            {
                "SessionSummaryActiveThread": SessionSummaryActiveThread,
                "SessionSummaryContent": SessionSummaryContent,
            }.items()
        )
    }
    literals = {
        name: _literal_surface(name, annotation)
        for name, annotation in sorted(
            {
                "ScopeKind": ScopeKind,
                "MemoryType": MemoryType,
                "MemorySource": MemorySource,
                "RecordVisibility": RecordVisibility,
                "CandidateStatus": CandidateStatus,
                "MemoryRelationType": MemoryRelationType,
                "MemoryTier": MemoryTier,
                "MemoryTierTransitionReason": MemoryTierTransitionReason,
                "SessionSummaryThreadStatus": SessionSummaryThreadStatus,
                "SessionSummaryOutcome": SessionSummaryOutcome,
                "ClaimKeyPolarity": ClaimKeyPolarity,
                "MemorySourceClass": MemorySourceClass,
                "TrustGateDecision": TrustGateDecision,
                "TrustGateReasonCode": TrustGateReasonCode,
            }.items()
        )
    }
    enums = {
        "RecordOrder": _enum_surface(RecordOrder),
    }
    return {
        "_meta": {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "generator": "tests/memory/test_kce_public_surface_snapshot.py (KCE-00)",
        },
        "models": models,
        "portability": portability,
        "audit": audit,
        "query_dtos": query_dtos,
        "typed_dicts": typed_dicts,
        "literals": literals,
        "enums": enums,
    }


def _dump_snapshot(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, indent=2, sort_keys=True) + "\n"


def _read_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))


def _write_snapshot(snapshot: dict[str, Any]) -> None:
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_FILE.write_text(_dump_snapshot(snapshot), encoding="utf-8")


class KCEPublicSurfaceSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current = _capture_surface()

    def test_surface_matches_snapshot(self) -> None:
        if _is_update_requested():
            _write_snapshot(self.current)
            self.skipTest(
                f"{UPDATE_ENV_VAR}=1 refreshed snapshot at {SNAPSHOT_FILE}. "
                "Review the JSON diff before marking KCE-00 done."
            )
        if not SNAPSHOT_FILE.exists():
            self.fail(
                f"KCE surface snapshot missing at {SNAPSHOT_FILE}. "
                f"Run with {UPDATE_ENV_VAR}=1 to create it."
            )
        expected = _read_snapshot()
        if self.current == expected:
            return
        lines = ["KCE memory public surface drifted from snapshot."]
        for section in (
            "models",
            "audit",
            "portability",
            "query_dtos",
            "typed_dicts",
            "literals",
            "enums",
        ):
            current_entries = self.current[section]
            expected_entries = expected[section]
            added = sorted(set(current_entries) - set(expected_entries))
            removed = sorted(set(expected_entries) - set(current_entries))
            changed = [
                name
                for name in sorted(set(current_entries) & set(expected_entries))
                if current_entries[name] != expected_entries[name]
            ]
            if not (added or removed or changed):
                continue
            lines.append(f"  [{section}]")
            lines.append(f"    added ({len(added)}): {added or '(none)'}")
            lines.append(f"    removed ({len(removed)}): {removed or '(none)'}")
            lines.append(f"    changed ({len(changed)}): {changed or '(none)'}")
            for name in changed:
                lines.append(f"      expected {name}: {expected_entries[name]}")
                lines.append(f"      current  {name}: {current_entries[name]}")
        if self.current["_meta"] != expected["_meta"]:
            lines.append(
                f"  [meta] expected={expected['_meta']} current={self.current['_meta']}"
            )
        lines.append(
            f"If the drift is intentional, refresh with {UPDATE_ENV_VAR}=1 and record it in the KCE tracker."
        )
        self.fail("\n".join(lines))

    def test_snapshot_generator_is_stable(self) -> None:
        self.assertEqual(_dump_snapshot(self.current), _dump_snapshot(self.current))

    def test_snapshot_has_expected_top_level_sections(self) -> None:
        self.assertEqual(
            sorted(self.current.keys()),
            sorted(
                [
                    "_meta",
                    "models",
                    "portability",
                    "audit",
                    "query_dtos",
                    "typed_dicts",
                    "literals",
                    "enums",
                ]
            ),
        )
