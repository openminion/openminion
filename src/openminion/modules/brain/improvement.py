"""Public brain improvement contracts used by callers outside the brain module."""

from __future__ import annotations

from openminion.modules.brain.runtime.improvement.candidates import (
    ImprovementCandidate,
)
from openminion.modules.brain.runtime.improvement.instruction_apply import (
    apply_instruction_proposal,
    reject_instruction_proposal,
    rollback_instruction_proposal,
)
from openminion.modules.brain.runtime.improvement.instruction_store import (
    InstructionProposalStore,
)
from openminion.modules.brain.runtime.improvement.instructions import (
    InstructionApprovalRecord,
    InstructionOpportunity,
    InstructionProposalEvent,
    InstructionTargetSnapshot,
    build_instruction_proposal,
)

__all__ = [
    "ImprovementCandidate",
    "InstructionApprovalRecord",
    "InstructionOpportunity",
    "InstructionProposalEvent",
    "InstructionProposalStore",
    "InstructionTargetSnapshot",
    "apply_instruction_proposal",
    "build_instruction_proposal",
    "reject_instruction_proposal",
    "rollback_instruction_proposal",
]
