from typing import Any

from openminion.modules.memory.runtime.gc_capacity import enforce_scope_capacity
from openminion.modules.memory.runtime.gc_confidence import apply_confidence_decay
from openminion.modules.memory.runtime.gc_insights import evict_stale_insights
from openminion.modules.memory.runtime.gc_summaries import compress_old_summaries
from openminion.modules.memory.runtime.purge import GCResult, purge_soft_deleted
from openminion.modules.memory.storage.base import MemoryStore

__all__ = [
    "GCResult",
    "apply_confidence_decay",
    "compress_old_summaries",
    "enforce_scope_capacity",
    "evict_stale_insights",
    "run_gc",
]


def run_gc(
    store: MemoryStore,
    batch_size: int = 500,
    *,
    retention_config: Any | None = None,
) -> GCResult:
    """Purge soft-deleted and expired rows from all tables."""
    result = GCResult()

    if retention_config is not None:
        decayed, _evicted_by_decay = apply_confidence_decay(
            store,
            interval_days=int(
                getattr(retention_config, "confidence_decay_interval_days", 7)
            ),
            decay_rate=float(getattr(retention_config, "confidence_decay_rate", 0.05)),
            min_confidence=float(
                getattr(retention_config, "min_confidence_eviction", 0.3)
            ),
            disuse_threshold_days=int(
                getattr(retention_config, "disuse_threshold_days", 30)
            ),
            disuse_decay_multiplier=float(
                getattr(retention_config, "disuse_decay_multiplier", 2.0)
            ),
        )
        result.decayed_records = decayed
        compressed, _deleted_summaries = compress_old_summaries(
            store,
            max_age_days=int(retention_config.summary_compression_age_days),
            delete_age_days=int(retention_config.summary_delete_age_days),
            max_summary_chars=int(retention_config.summary_compression_max_chars),
        )
        result.compressed_summaries = compressed
        capacity_evicted = enforce_scope_capacity(
            store,
            max_records=int(getattr(retention_config, "max_records_per_scope", 500)),
        )
        result.capacity_evicted_records = sum(capacity_evicted.values())

    purge_result = purge_soft_deleted(store, batch_size=batch_size)
    result.deleted_records += purge_result.deleted_records
    result.deleted_candidates += purge_result.deleted_candidates
    result.cleaned_fts_rows += purge_result.cleaned_fts_rows
    result.cleaned_entity_rows += purge_result.cleaned_entity_rows
    return result
