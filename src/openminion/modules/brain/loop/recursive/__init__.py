from .contracts import (
    ArtifactClient,
    CompressionClient,
    ContextClient,
    LLMClient,
    MemoryClient,
    RetrievalClient,
    SessionClient,
    SkillClient,
    RLM_CONTRACT_VERSION,
    RLM_INTERFACE_VERSION,
    RLMServiceInterface,
    ensure_rlm_compatibility,
)
from .schemas import (
    EvidenceRef,
    MemoryWriteIntent,
    MetaDirective,
    RetrievedContext,
    RLMBudgets,
    RLMConfig,
    RLMConstraints,
    RLMContinuation,
    RLMResponse,
    RLMTelemetry,
    RetrievalEval,
    RetrievalFilters,
    RetrievalLimits,
    TaskState,
    TickTelemetry,
    WMState,
)
from .service import RLMService

from openminion.modules.brain.schemas.state import BrainMode

BRAIN_LOOP_RECURSIVE_SELECTION_MODE = BrainMode.AUTONOMOUS
"""Typed selection signal for the recursive loop family (spec §5.3).

The runner-tick orchestrator routes to `run_recursive_turn` when
`state.mode == BRAIN_LOOP_RECURSIVE_SELECTION_MODE` and `runner.rlm_api`
is not None. Named so later rows can cite a canonical signal instead of
re-deriving the string `"autonomous"`.
"""

__all__ = [
    "ArtifactClient",
    "BRAIN_LOOP_RECURSIVE_SELECTION_MODE",
    "CompressionClient",
    "ContextClient",
    "EvidenceRef",
    "LLMClient",
    "MemoryClient",
    "MemoryWriteIntent",
    "MetaDirective",
    "RetrievalClient",
    "RetrievalEval",
    "RetrievalFilters",
    "RetrievalLimits",
    "RetrievedContext",
    "RLM_CONTRACT_VERSION",
    "RLM_INTERFACE_VERSION",
    "RLMBudgets",
    "RLMConfig",
    "RLMConstraints",
    "RLMContinuation",
    "RLMResponse",
    "RLMService",
    "RLMServiceInterface",
    "RLMTelemetry",
    "SessionClient",
    "SkillClient",
    "TaskState",
    "TickTelemetry",
    "WMState",
    "ensure_rlm_compatibility",
]
