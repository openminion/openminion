"""Workflow-learning helpers owned by the skill module."""

from .draft import SkillDraftError, render_skill_markdown
from .evidence import (
    bundle_from_autonomy_proof_packet,
    bundle_from_skill_run,
    bundle_from_strategy_outcome,
)
from .miner import WorkflowShapeMiner
from .proposer import LearningProposalResult, stage_shape_as_skill_proposal
from .replay import ReplayProof, apply_proposal_with_replay
from .reuse import record_learned_skill_reuse
from .shapes import WorkflowEvidenceBundle, WorkflowShape
from .telemetry import (
    WORKFLOW_LEARNING_EVENT_TYPES,
    workflow_learning_event,
)
from .trust import (
    SkillExecutionTrustRecord,
    record_skill_run_outcome,
    promote_execution_trust,
)

__all__ = (
    "LearningProposalResult",
    "ReplayProof",
    "SkillDraftError",
    "SkillExecutionTrustRecord",
    "WORKFLOW_LEARNING_EVENT_TYPES",
    "WorkflowEvidenceBundle",
    "WorkflowShape",
    "WorkflowShapeMiner",
    "apply_proposal_with_replay",
    "bundle_from_autonomy_proof_packet",
    "bundle_from_skill_run",
    "bundle_from_strategy_outcome",
    "promote_execution_trust",
    "record_learned_skill_reuse",
    "record_skill_run_outcome",
    "render_skill_markdown",
    "stage_shape_as_skill_proposal",
    "workflow_learning_event",
)
