"""Structural workflow-shape mining."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from .shapes import WorkflowEvidenceBundle, WorkflowShape


class WorkflowShapeMiner:
    """Group evidence by structural fields, not prose similarity."""

    def __init__(self, *, min_success_count: int = 2) -> None:
        self.min_success_count = max(1, int(min_success_count))

    def mine(self, bundles: Iterable[WorkflowEvidenceBundle]) -> list[WorkflowShape]:
        grouped: dict[tuple[object, ...], list[WorkflowEvidenceBundle]] = defaultdict(
            list
        )
        for bundle in bundles or []:
            if not self._has_structural_signal(bundle):
                continue
            grouped[self._key(bundle)].append(bundle)

        shapes = [self._shape_from_group(items) for items in grouped.values()]
        shapes.sort(key=lambda item: item.shape_id)
        return shapes

    def skill_ready_shapes(
        self, bundles: Iterable[WorkflowEvidenceBundle]
    ) -> list[WorkflowShape]:
        return [shape for shape in self.mine(bundles) if self.is_skill_ready(shape)]

    def is_skill_ready(self, shape: WorkflowShape) -> bool:
        return (
            shape.success_count >= self.min_success_count
            or shape.explicit_save_count >= 1
        )

    @staticmethod
    def _has_structural_signal(bundle: WorkflowEvidenceBundle) -> bool:
        return bool(
            bundle.intent_category
            and bundle.capability_category
            and bundle.strategy_id
            and (
                bundle.tool_names
                or bundle.command_fingerprints
                or bundle.test_fingerprints
                or bundle.artifact_types
                or bundle.explicit_save
            )
        )

    @staticmethod
    def _key(bundle: WorkflowEvidenceBundle) -> tuple[object, ...]:
        return (
            bundle.intent_category,
            bundle.capability_category,
            bundle.strategy_id,
            tuple(bundle.tool_names),
            tuple(bundle.command_fingerprints),
            tuple(bundle.test_fingerprints),
            tuple(bundle.artifact_types),
        )

    @staticmethod
    def _shape_from_group(items: list[WorkflowEvidenceBundle]) -> WorkflowShape:
        first = items[0]
        success = sum(1 for item in items if item.outcome == "success")
        partial = sum(1 for item in items if item.outcome == "partial")
        failure = sum(1 for item in items if item.outcome == "failure")
        explicit_save = sum(1 for item in items if item.explicit_save)
        evidence_refs = sorted({ref for item in items for ref in item.evidence_refs})
        seen = sorted(item.observed_at for item in items if item.observed_at)
        failure_refs = sorted(
            {
                ref
                for item in items
                if item.outcome == "failure"
                for ref in item.evidence_refs
            }
        )
        return WorkflowShape(
            intent_category=first.intent_category,
            capability_category=first.capability_category,
            strategy_id=first.strategy_id,
            tool_names=first.tool_names,
            command_fingerprints=first.command_fingerprints,
            test_fingerprints=first.test_fingerprints,
            artifact_types=first.artifact_types,
            success_count=success,
            partial_count=partial,
            failure_count=failure,
            evidence_refs=evidence_refs,
            performance_entry_refs=evidence_refs,
            failure_pattern_refs=failure_refs,
            knowledge_record_refs=evidence_refs,
            first_seen_at=seen[0] if seen else "",
            last_seen_at=seen[-1] if seen else "",
            explicit_save_count=explicit_save,
        )


__all__ = ("WorkflowShapeMiner",)
