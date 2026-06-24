"""PRV parallel-rollout substrate for verifiable substeps."""

from openminion.modules.brain.loop.rollouts.config import ParallelRolloutConfig
from openminion.modules.brain.loop.rollouts.interfaces import (
    RolloutScorer,
    RolloutSelector,
)
from openminion.modules.brain.loop.rollouts.isolator import WorktreeIsolator
from openminion.modules.brain.loop.rollouts.runner import (
    HighestScoreSelector,
    parallel_rollout,
)
from openminion.modules.brain.loop.rollouts.schemas import RolloutPlan, RolloutResult
from openminion.modules.brain.loop.rollouts.scorer import (
    LLMRolloutScorer,
    StubRolloutScorer,
)
from openminion.modules.brain.loop.rollouts.strategy_gate import (
    PARALLEL_ROLLOUT_ELIGIBLE_STEP_KINDS,
    is_step_eligible_for_parallel_rollout,
)

__all__ = [
    "HighestScoreSelector",
    "LLMRolloutScorer",
    "PARALLEL_ROLLOUT_ELIGIBLE_STEP_KINDS",
    "ParallelRolloutConfig",
    "RolloutPlan",
    "RolloutResult",
    "RolloutScorer",
    "RolloutSelector",
    "StubRolloutScorer",
    "WorktreeIsolator",
    "is_step_eligible_for_parallel_rollout",
    "parallel_rollout",
]
