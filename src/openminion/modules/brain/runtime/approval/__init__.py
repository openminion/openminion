"""Pre-action approval verifier exports for destructive tool gates.

This package has no in-tree production caller: nothing under ``src/`` constructs
``LLMActionApprovalVerifier`` or calls ``gate_destructive_action``, the only caller
being an integration test, and the ``enabled`` flag defaults to false. In-tree
reference absence says nothing about importers outside this repository, so treat the
exports as a retained import surface rather than as dead code. Destructive-action
approval runs through the policy confirmation flow (``modules/policy``), which owns
the confirmation, grant and replay contracts; read this package as available code,
not as the gate that judges a destructive action today.
"""

from .registry import ApprovalCriteriaRegistry, default_criteria_registry
from .gate import ActionApprovalConfig, gate_destructive_action
from .llm import LLMActionApprovalVerifier
from .protocol import ActionApprovalVerifier, ApprovalCriteria, ApprovalVerdict

__all__ = [
    "ActionApprovalConfig",
    "ActionApprovalVerifier",
    "ApprovalCriteria",
    "ApprovalCriteriaRegistry",
    "ApprovalVerdict",
    "LLMActionApprovalVerifier",
    "default_criteria_registry",
    "gate_destructive_action",
]
