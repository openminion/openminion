"""Project and persist typed knowledge-consolidation candidates."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from openminion.base.time import utc_now_iso

ConsolidationCriterionId = Literal[
    "same_strategy_id_high_frequency",
    "same_tool_failure_pattern_recurring",
    "same_goal_subtree_completed",
]
DecisionStatus = Literal["accepted", "rejected", "deferred"]


class KnowledgeConsolidationCandidate(BaseModel):
    """Typed many-to-one consolidation candidate."""

    model_config = ConfigDict(extra="forbid")

    criterion_id: ConsolidationCriterionId
    source_record_refs: list[str] = Field(default_factory=list)
    evidence_window: dict[str, Any] = Field(default_factory=dict)
    proposed_consolidated_kind: str
    proposed_signature: str


class ConsolidatedKnowledgeRecord(BaseModel):
    """Typed durable consolidated-knowledge payload."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = ""
    record_type: Literal["consolidated_knowledge"] = "consolidated_knowledge"
    source_record_lineage: list[str] = Field(default_factory=list)
    consolidation_criterion_id: ConsolidationCriterionId
    freshness_metadata: dict[str, Any] = Field(default_factory=dict)
    superseded_records: list[str] = Field(default_factory=list)
    consolidated_kind: str
    consolidated_signature: str


class ConsolidationDecision(BaseModel):
    """Typed operator-visible consolidation decision."""

    model_config = ConfigDict(extra="forbid")

    candidate_ref: str
    status: DecisionStatus
    policy_id: str = ""
    decided_at: str


def _record_field(record: Any, field_name: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(field_name)
    return getattr(record, field_name, None)


def _source_record_content(record: Any) -> Mapping[str, Any]:
    if isinstance(record, Mapping):
        content = record.get("content")
        return content if isinstance(content, Mapping) else record
    content = getattr(record, "content", None)
    return content if isinstance(content, Mapping) else {}


def _record_id(record: Any) -> str:
    candidate = str(
        _record_field(record, "record_id") or _record_field(record, "id") or ""
    ).strip()
    return candidate


def _record_scope(record: Any) -> str:
    return str(_record_field(record, "scope") or "").strip()


def _candidate_ref(candidate: KnowledgeConsolidationCandidate) -> str:
    payload = {
        "criterion_id": candidate.criterion_id,
        "source_record_refs": list(candidate.source_record_refs),
        "evidence_window": dict(candidate.evidence_window),
        "proposed_consolidated_kind": candidate.proposed_consolidated_kind,
        "proposed_signature": candidate.proposed_signature,
    }
    return "kcon::" + json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _parse_candidate_ref(candidate_ref: str) -> dict[str, Any]:
    prefix = "kcon::"
    if not str(candidate_ref or "").startswith(prefix):
        raise ValueError("invalid candidate_ref")
    payload = json.loads(str(candidate_ref)[len(prefix) :])
    if not isinstance(payload, dict):
        raise ValueError("invalid candidate_ref payload")
    return payload


def _strategy_candidates(
    records: Iterable[Any],
) -> list[KnowledgeConsolidationCandidate]:
    by_strategy: dict[str, list[Any]] = defaultdict(list)
    for record in records or []:
        record_id = _record_id(record)
        strategy_id = str(
            _source_record_content(record).get("strategy_id") or ""
        ).strip()
        if not record_id or not strategy_id:
            continue
        by_strategy[strategy_id].append(record)

    candidates: list[KnowledgeConsolidationCandidate] = []
    for strategy_id, grouped in by_strategy.items():
        if len(grouped) < 2:
            continue
        refs = sorted(_record_id(record) for record in grouped if _record_id(record))
        target_scope = _record_scope(grouped[0]) or "global:default"
        candidates.append(
            KnowledgeConsolidationCandidate(
                criterion_id="same_strategy_id_high_frequency",
                source_record_refs=refs,
                evidence_window={
                    "family": "strategy_outcome",
                    "count": len(refs),
                    "target_scope": target_scope,
                },
                proposed_consolidated_kind="strategy_knowledge",
                proposed_signature=f"strategy::{strategy_id}",
            )
        )
    return candidates


def _tool_failure_candidates(
    records: Iterable[Any],
) -> list[KnowledgeConsolidationCandidate]:
    candidates: list[KnowledgeConsolidationCandidate] = []
    for record in records or []:
        record_id = _record_id(record)
        if not record_id:
            continue
        signature = str(
            _record_field(record, "signature")
            or _source_record_content(record).get("signature")
            or ""
        ).strip()
        occurrence_count = int(
            _record_field(record, "occurrence_count")
            or _source_record_content(record).get("occurrence_count")
            or 0
        )
        tags = (
            _record_field(record, "tags")
            or _source_record_content(record).get("tags")
            or []
        )
        tool_tags = sorted(
            str(tag).strip() for tag in tags if str(tag).strip().startswith("tool:")
        )
        if not signature or occurrence_count < 2 or not tool_tags:
            continue
        target_scope = _record_scope(record) or "global:default"
        candidates.append(
            KnowledgeConsolidationCandidate(
                criterion_id="same_tool_failure_pattern_recurring",
                source_record_refs=[record_id],
                evidence_window={
                    "family": "improvement_note",
                    "occurrence_count": occurrence_count,
                    "target_scope": target_scope,
                },
                proposed_consolidated_kind="failure_pattern_knowledge",
                proposed_signature=f"{tool_tags[0]}::{signature}",
            )
        )
    return candidates


def _goal_subtree_candidates(
    records: Iterable[Any],
) -> list[KnowledgeConsolidationCandidate]:
    by_parent: dict[str, list[Any]] = defaultdict(list)
    for record in records or []:
        record_id = _record_id(record)
        parent_goal_id = str(
            _source_record_content(record).get("parent_goal_id") or ""
        ).strip()
        if not record_id or not parent_goal_id:
            continue
        by_parent[parent_goal_id].append(record)

    candidates: list[KnowledgeConsolidationCandidate] = []
    for parent_goal_id, grouped in by_parent.items():
        if len(grouped) < 2:
            continue
        refs = sorted(_record_id(record) for record in grouped if _record_id(record))
        target_scope = _record_scope(grouped[0]) or "global:default"
        candidates.append(
            KnowledgeConsolidationCandidate(
                criterion_id="same_goal_subtree_completed",
                source_record_refs=refs,
                evidence_window={
                    "family": "declared_goal",
                    "child_count": len(refs),
                    "target_scope": target_scope,
                },
                proposed_consolidated_kind="goal_subtree_knowledge",
                proposed_signature=f"goal_subtree::{parent_goal_id}",
            )
        )
    return candidates


def project_source_records_to_candidates(
    records_by_family: Mapping[str, Iterable[Any]],
    *,
    criteria: Iterable[ConsolidationCriterionId],
) -> list[KnowledgeConsolidationCandidate]:
    """Project source-family records into consolidation candidates."""

    requested = list(criteria or [])
    candidates: list[KnowledgeConsolidationCandidate] = []
    if "same_strategy_id_high_frequency" in requested:
        candidates.extend(
            _strategy_candidates(records_by_family.get("strategy_outcome", []))
        )
    if "same_tool_failure_pattern_recurring" in requested:
        candidates.extend(
            _tool_failure_candidates(records_by_family.get("improvement_note", []))
        )
    if "same_goal_subtree_completed" in requested:
        candidates.extend(
            _goal_subtree_candidates(records_by_family.get("declared_goal", []))
        )
    candidates.sort(
        key=lambda item: (
            item.criterion_id,
            item.proposed_signature,
        )
    )
    return candidates


def decide_consolidation(
    candidate: KnowledgeConsolidationCandidate | Mapping[str, Any],
    *,
    policy_id: str,
    status: DecisionStatus = "accepted",
) -> ConsolidationDecision:
    """Produce one typed consolidation decision without side effects."""

    candidate_obj = (
        candidate
        if isinstance(candidate, KnowledgeConsolidationCandidate)
        else KnowledgeConsolidationCandidate.model_validate(candidate)
    )
    normalized_policy_id = str(policy_id or "").strip()
    if not normalized_policy_id:
        raise ValueError("policy_id is required")
    return ConsolidationDecision(
        candidate_ref=_candidate_ref(candidate_obj),
        status=status,
        policy_id=normalized_policy_id,
        decided_at=utc_now_iso(),
    )


def apply_consolidation(
    decision: ConsolidationDecision | Mapping[str, Any],
    *,
    memory_api: Any,
) -> ConsolidatedKnowledgeRecord | None:
    """Persist a consolidated record and supersede the source records."""

    decision_obj = (
        decision
        if isinstance(decision, ConsolidationDecision)
        else ConsolidationDecision.model_validate(decision)
    )
    if decision_obj.status != "accepted":
        return None
    payload = _parse_candidate_ref(decision_obj.candidate_ref)
    source_record_refs = [
        str(ref).strip()
        for ref in payload.get("source_record_refs", [])
        if str(ref).strip()
    ]
    consolidated = ConsolidatedKnowledgeRecord(
        source_record_lineage=source_record_refs,
        consolidation_criterion_id=payload["criterion_id"],
        freshness_metadata=dict(payload.get("evidence_window") or {}),
        superseded_records=list(source_record_refs),
        consolidated_kind=str(payload.get("proposed_consolidated_kind") or "").strip(),
        consolidated_signature=str(payload.get("proposed_signature") or "").strip(),
    )
    writer = getattr(memory_api, "write_record", None)
    if not callable(writer):
        raise ValueError("memory_api.write_record is required")
    target_scope = str(
        consolidated.freshness_metadata.get("target_scope") or "global:default"
    ).strip()
    record_id = str(
        writer(
            scope=target_scope,
            record_type="consolidated_knowledge",
            title=f"Consolidated {consolidated.consolidated_signature}",
            content=consolidated.model_dump(mode="json", exclude={"record_id"}),
            tags=[
                str(consolidated.consolidation_criterion_id),
                str(consolidated.consolidated_kind),
            ],
            evidence_refs=source_record_refs,
            confidence=1.0,
        )
    ).strip()
    superseder = getattr(memory_api, "supersede_by_contradiction", None)
    if callable(superseder):
        for source_ref in source_record_refs:
            if source_ref.startswith("mem_"):
                superseder(
                    source_ref,
                    record_id,
                    reason=str(consolidated.consolidation_criterion_id),
                )
    return consolidated.model_copy(update={"record_id": record_id})


__all__ = [
    "ConsolidatedKnowledgeRecord",
    "ConsolidationCriterionId",
    "ConsolidationDecision",
    "KnowledgeConsolidationCandidate",
    "apply_consolidation",
    "decide_consolidation",
    "project_source_records_to_candidates",
]
