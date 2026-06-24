"""OpenMinion compatibility surface for durable-memory models."""

from dataclasses import dataclass

import sophiagraph.models as _sg_models
from sophiagraph.models import *  # noqa: F401,F403
from sophiagraph.models import (
    MemoryRecord as _BaseMemoryRecord,
    _as_candidate_status as _as_candidate_status,
    _as_claim_key_polarity as _as_claim_key_polarity,
    _as_memory_relation_type as _as_memory_relation_type,
    _as_memory_relation_type_list as _as_memory_relation_type_list,
    _as_memory_source as _as_memory_source,
    _as_memory_source_class as _as_memory_source_class,
    _as_memory_tier as _as_memory_tier,
    _as_memory_tier_transition_reason as _as_memory_tier_transition_reason,
    _as_memory_type as _as_memory_type,
    _as_memory_type_list as _as_memory_type_list,
    _coerce_temporal_dt as _coerce_temporal_dt,
)
from sophiagraph.models.core import _SCOPE_PATTERN as _SCOPE_PATTERN


@dataclass(frozen=True)
class MemoryRecord(_BaseMemoryRecord):
    """OpenMinion compatibility extension for goal-scoped recall."""

    goal_id: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.goal_id is not None and not str(self.goal_id).strip():
            raise ValueError(
                "goal_id must be non-empty when supplied"
            )  # allow-bare-raise: dataclass field validator preserves historic constructor contract


__all__ = [
    name for name in getattr(_sg_models, "__all__", []) if name != "MemoryRecord"
]
__all__.append("MemoryRecord")
