from pathlib import Path

from openminion.base.generated_paths import resolve_generated_config_path

from .constants import (
    CHECKPOINT_ERROR_BUDGET_EXCEEDED,
    CHECKPOINT_ERROR_INVARIANT_VIOLATED,
    CHECKPOINT_ERROR_PIPELINE_FAILED,
    CHECKPOINT_ERROR_RANGE_INVALID,
    CHECKPOINT_ERROR_STABLE_ID_COLLISION,
    DEFAULT_CONFIG_FILENAME,
)
from .hashing import (
    build_canonical_payload,
    canonical_hash,
    compute_compression_hash,
    diff_payloads,
)
from .interfaces import (
    COMPRESS_INTERFACE_VERSION,
    CompactionServiceAPI,
    CompressionServiceAPI,
    ensure_compress_component_compatibility,
)
from .schemas import (
    CheckpointFailedPayload,
    CheckpointStats,
    CheckpointStructuredState,
    CompressionBudgets,
    CompressionBundle,
    CompressionCheckpoint,
    CompressionPolicy,
    CompressionReport,
    CompressionRequest,
    CompressionResult,
    CompressedBlock,
    InputBlock,
    SeedBundle,
    SeedBundleBudgets,
    SeedSection,
    StructuredConstraint,
    StructuredDecision,
    StructuredOpenLoop,
    StructuredToolDigest,
    TierEntry,
)
from .strategies import (
    AfterLastCheckpointSelector,
    CheckpointComposerV1,
    DeltaEvent,
    OpenLoopStrategy,
    StrategyRegistry,
    TierStrategy,
)
from .compaction import (
    BudgetArbiter,
    CompactionService,
    TriggerPolicy,
    TriggerReason,
    distill_tool_output,
    exclude_raw_tool_output,
)
from .storage.checkpoint_store import CheckpointStore

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent.parent.parent


def resolve_config_path(filename: str | None = None) -> Path:
    """Return the path to the default context.compress configuration file."""

    if filename is None:
        candidate = PROJECT_ROOT / DEFAULT_CONFIG_FILENAME
        if candidate.exists():
            return candidate
        generated = Path(resolve_generated_config_path(DEFAULT_CONFIG_FILENAME))
        if generated.exists():
            return generated
        raise FileNotFoundError(f"config file not found: {candidate}")

    path = Path(filename)
    candidate = path if path.is_absolute() else PROJECT_ROOT / path
    if not candidate.exists():
        raise FileNotFoundError(f"config file not found: {candidate}")
    return candidate


__all__ = [
    "resolve_config_path",
    "COMPRESS_INTERFACE_VERSION",
    "CompressionServiceAPI",
    "CompactionServiceAPI",
    "ensure_compress_component_compatibility",
    # Hashing
    "build_canonical_payload",
    "compute_compression_hash",
    "canonical_hash",
    "diff_payloads",
    # V1 schemas
    "CompressionBudgets",
    "CompressionBundle",
    "CompressionPolicy",
    "CompressionReport",
    "CompressionRequest",
    "CompressionResult",
    "CompressedBlock",
    "InputBlock",
    "SeedBundle",
    "SeedBundleBudgets",
    "SeedSection",
    "TierEntry",
    # Spec #3 schemas (C15-001/002)
    "CompressionCheckpoint",
    "CheckpointFailedPayload",
    "CheckpointStats",
    "CheckpointStructuredState",
    "StructuredDecision",
    "StructuredConstraint",
    "StructuredOpenLoop",
    "StructuredToolDigest",
    # Failure taxonomy
    "CHECKPOINT_ERROR_BUDGET_EXCEEDED",
    "CHECKPOINT_ERROR_RANGE_INVALID",
    "CHECKPOINT_ERROR_STABLE_ID_COLLISION",
    "CHECKPOINT_ERROR_INVARIANT_VIOLATED",
    "CHECKPOINT_ERROR_PIPELINE_FAILED",
    # Strategies + plugins (C15-003/004/005/008)
    "DeltaEvent",
    "StrategyRegistry",
    "TierStrategy",
    "OpenLoopStrategy",
    "AfterLastCheckpointSelector",
    "CheckpointComposerV1",
    # Checkpoint store (C15-012)
    "CheckpointStore",
    # Compaction runtime
    "BudgetArbiter",
    "CompactionService",
    "TriggerPolicy",
    "TriggerReason",
    "distill_tool_output",
    "exclude_raw_tool_output",
]
