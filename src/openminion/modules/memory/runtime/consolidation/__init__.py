"""Lazy public surface for memory consolidation runtime helpers."""

from .coordinator import (
    MAINTENANCE_MODULE_STATE_KEY,
    ConsolidationCycleResult,
    ConsolidationConfig,
    ConsolidationCoordinator,
    ExtractionPayload,
    MergeDecision,
    MergeDecisions,
    run_consolidation_cycle,
)


def collect_memory_consolidation_candidates(*args, **kwargs):
    from .extract import collect_memory_consolidation_candidates as _impl

    return _impl(*args, **kwargs)


def extract_consolidation_payload(*args, **kwargs):
    from .extract import extract_consolidation_payload as _impl

    return _impl(*args, **kwargs)


def apply_memory_consolidation_decisions(*args, **kwargs):
    from .merge import apply_memory_consolidation_decisions as _impl

    return _impl(*args, **kwargs)


def apply_merge_decisions_via_service(*args, **kwargs):
    from .merge import apply_merge_decisions_via_service as _impl

    return _impl(*args, **kwargs)


__all__ = [
    "ConsolidationConfig",
    "ConsolidationCoordinator",
    "ConsolidationCycleResult",
    "ExtractionPayload",
    "MAINTENANCE_MODULE_STATE_KEY",
    "MergeDecision",
    "MergeDecisions",
    "apply_merge_decisions_via_service",
    "apply_memory_consolidation_decisions",
    "collect_memory_consolidation_candidates",
    "extract_consolidation_payload",
    "run_consolidation_cycle",
]
