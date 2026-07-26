"""Evidence-only utility planning for memory review candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from openminion.modules.memory.constants import (
    MEMORY_UTILITY_DISPOSITION_NEEDS_MORE_EVIDENCE,
    MEMORY_UTILITY_DISPOSITION_REVIEW_ARCHIVE,
    MEMORY_UTILITY_DISPOSITION_REVIEW_DEMOTE,
    MEMORY_UTILITY_DISPOSITION_REVIEW_FORGET,
    MEMORY_UTILITY_DISPOSITION_REVIEW_KEEP,
    MEMORY_UTILITY_DISPOSITIONS,
)
from openminion.modules.memory.contracts.utility_plan import (
    MEMORY_CONTEXT_OPERATIONAL_CANARY_VERSION,
    MEMORY_UTILITY_PLAN_SCHEMA_VERSION,
)
from openminion.modules.memory.errors import InvalidArgumentError

_FORGET_METRICS = frozenset({"governance_regression", "permission_safety"})
_DEMOTE_METRICS = frozenset({"block_usefulness", "memory_influence"})
_ARCHIVE_METRICS = frozenset({"budget_stability", "context_budget_stability"})


@dataclass(frozen=True)
class UtilityPlanItem:
    case_id: str
    disposition: str
    reason_code: str
    scorecard_metric: str
    score: float
    threshold: float
    evidence_refs: tuple[str, ...]
    source_scorecard_run_id: str
    source_artifact_sha256: str
    staged_candidate_id: str | None = None

    def __post_init__(self) -> None:
        if self.disposition not in MEMORY_UTILITY_DISPOSITIONS:
            raise InvalidArgumentError(
                f"invalid utility disposition: {self.disposition!r}"
            )
        if not self.evidence_refs:
            raise InvalidArgumentError("evidence_refs is required")


@dataclass(frozen=True)
class UtilityPlan:
    schema_version: str
    source_report_version: str
    source_run_id: str
    source_artifact_sha256: str
    dry_run: bool
    staged: bool
    items: tuple[UtilityPlanItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_report_version": self.source_report_version,
            "source_run_id": self.source_run_id,
            "source_artifact_sha256": self.source_artifact_sha256,
            "dry_run": self.dry_run,
            "staged": self.staged,
            "items": [asdict(item) for item in self.items],
        }


def build_utility_plan_from_canary(path: str | Path) -> UtilityPlan:
    source_path = Path(path).expanduser().resolve(strict=False)
    raw = source_path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise InvalidArgumentError("canary artifact must be a JSON object")
    report_version = str(payload.get("report_version", "") or "")
    if report_version != MEMORY_CONTEXT_OPERATIONAL_CANARY_VERSION:
        raise InvalidArgumentError(
            f"unsupported canary report_version: {report_version!r}"
        )
    run_id = _required_string(payload, "run_id")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise InvalidArgumentError("canary cases are required")
    artifact_hash = sha256(raw).hexdigest()
    items = tuple(
        _item_from_case(
            case, source_run_id=run_id, source_artifact_sha256=artifact_hash
        )
        for case in cases
    )
    return UtilityPlan(
        schema_version=MEMORY_UTILITY_PLAN_SCHEMA_VERSION,
        source_report_version=report_version,
        source_run_id=run_id,
        source_artifact_sha256=artifact_hash,
        dry_run=True,
        staged=False,
        items=items,
    )


def stage_utility_plan(plan: UtilityPlan, service: Any) -> UtilityPlan:
    staged_items: list[UtilityPlanItem] = []
    for item in plan.items:
        candidate_id = service.stage_candidate(
            scope=f"session:{plan.source_run_id}",
            record_type="fact",
            title=f"Memory utility review: {item.case_id}",
            content={
                "disposition": item.disposition,
                "reason_code": item.reason_code,
                "scorecard_metric": item.scorecard_metric,
                "score": item.score,
                "threshold": item.threshold,
            },
            tags=["memory-utility-plan", item.disposition],
            evidence_refs=list(item.evidence_refs),
            confidence=_confidence(item),
            meta={
                "utility_disposition": item.disposition,
                "utility_reason_code": item.reason_code,
                "source_scorecard_run_id": plan.source_run_id,
                "source_artifact_sha256": plan.source_artifact_sha256,
                "normalized_key": f"utility:{plan.source_run_id}:{item.case_id}",
            },
        )
        staged_items.append(
            UtilityPlanItem(**{**asdict(item), "staged_candidate_id": candidate_id})
        )
    return UtilityPlan(
        schema_version=plan.schema_version,
        source_report_version=plan.source_report_version,
        source_run_id=plan.source_run_id,
        source_artifact_sha256=plan.source_artifact_sha256,
        dry_run=False,
        staged=True,
        items=tuple(staged_items),
    )


def _item_from_case(
    case: object, *, source_run_id: str, source_artifact_sha256: str
) -> UtilityPlanItem:
    if not isinstance(case, Mapping):
        raise InvalidArgumentError("canary case entries must be JSON objects")
    evidence_refs = tuple(
        str(ref or "").strip() for ref in case.get("evidence_refs", ())
    )
    evidence_refs = tuple(ref for ref in evidence_refs if ref)
    if not evidence_refs:
        raise InvalidArgumentError("canary case evidence_refs are required")
    metric = _required_string(case, "scorecard_metric")
    status = str(case.get("status", "") or "").strip()
    score = float(case.get("score", 0.0))
    threshold = float(case.get("threshold", 0.0))
    disposition = _disposition_for(
        metric=metric, status=status, score=score, threshold=threshold
    )
    return UtilityPlanItem(
        case_id=_required_string(case, "case_id"),
        disposition=disposition,
        reason_code=f"{metric}_{status or 'unknown'}",
        scorecard_metric=metric,
        score=score,
        threshold=threshold,
        evidence_refs=evidence_refs,
        source_scorecard_run_id=source_run_id,
        source_artifact_sha256=source_artifact_sha256,
    )


def _disposition_for(
    *, metric: str, status: str, score: float, threshold: float
) -> str:
    if status == "pass" and score >= threshold:
        return MEMORY_UTILITY_DISPOSITION_REVIEW_KEEP
    if metric in _FORGET_METRICS:
        return MEMORY_UTILITY_DISPOSITION_REVIEW_FORGET
    if metric in _DEMOTE_METRICS:
        return MEMORY_UTILITY_DISPOSITION_REVIEW_DEMOTE
    if metric in _ARCHIVE_METRICS:
        return MEMORY_UTILITY_DISPOSITION_REVIEW_ARCHIVE
    return MEMORY_UTILITY_DISPOSITION_NEEDS_MORE_EVIDENCE


def _confidence(item: UtilityPlanItem) -> float:
    return max(0.1, min(0.95, round(abs(item.score - item.threshold), 4)))


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = str(payload.get(key, "") or "").strip()
    if not value:
        raise InvalidArgumentError(f"{key} is required")
    return value


__all__ = [
    "UtilityPlan",
    "UtilityPlanItem",
    "build_utility_plan_from_canary",
    "stage_utility_plan",
]
