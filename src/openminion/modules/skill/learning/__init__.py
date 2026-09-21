"""Workflow-learning helpers owned by the skill module."""

from .draft import SkillDraftError, render_skill_markdown
from .evidence import (
    bundle_from_autonomy_proof_packet,
    bundle_from_skill_run,
    bundle_from_strategy_outcome,
)
from .miner import WorkflowShapeMiner
from .proposer import LearningProposalResult, stage_shape_as_skill_proposal
from .replay import ReplayEvaluationResult, ReplayProof, apply_proposal_with_replay
from .reuse import record_learned_skill_reuse
from .shapes import WorkflowEvidenceBundle, WorkflowShape
from .telemetry import (
    WORKFLOW_LEARNING_EVENT_TYPES,
    workflow_learning_event,
)
from .trust import (
    SkillExecutionTrustDiagnostic,
    SkillExecutionTrustRecord,
    execution_trust_diagnostic,
    record_skill_run_outcome,
    promote_execution_trust,
)

__all__ = (
    "LearningProposalResult",
    "ReplayEvaluationResult",
    "ReplayProof",
    "SkillDraftError",
    "SkillExecutionTrustRecord",
    "SkillExecutionTrustDiagnostic",
    "WORKFLOW_LEARNING_EVENT_TYPES",
    "WorkflowEvidenceBundle",
    "WorkflowShape",
    "WorkflowShapeMiner",
    "apply_proposal_with_replay",
    "bundle_from_autonomy_proof_packet",
    "bundle_from_skill_run",
    "bundle_from_strategy_outcome",
    "execution_trust_diagnostic",
    "promote_execution_trust",
    "record_learned_skill_reuse",
    "record_skill_run_outcome",
    "render_skill_markdown",
    "stage_shape_as_skill_proposal",
    "workflow_learning_event",
)
