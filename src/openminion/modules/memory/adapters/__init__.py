"""OpenMinion adapters for package-owned memory contracts."""

from .contracts import ActivePolicyGrantResolver, DelegatedRunContextView
from .delegated_access import (
    DelegatedContextBudgetError,
    DelegatedContextBudgetResult,
    OpenMinionDelegationMemoryGrantResolver,
    authorize_and_enforce_delegated_context,
    enforce_delegated_context_budget,
)
from .events import (
    CanonicalSessionEventSink,
    DelegatedMemorySessionEventType,
    emit_delegated_memory_session_event,
)
from .delegated_handback import (
    CandidateSubmissionStore,
    DelegatedMemoryProposal,
    submit_delegated_memory_proposal,
)
from .delegated_transport import (
    DelegatedTransportProjection,
    map_delegated_memory_transport,
)

__all__ = [
    "CandidateSubmissionStore",
    "CanonicalSessionEventSink",
    "ActivePolicyGrantResolver",
    "DelegatedContextBudgetResult",
    "DelegatedContextBudgetError",
    "DelegatedMemoryProposal",
    "DelegatedMemorySessionEventType",
    "DelegatedRunContextView",
    "DelegatedTransportProjection",
    "OpenMinionDelegationMemoryGrantResolver",
    "authorize_and_enforce_delegated_context",
    "enforce_delegated_context_budget",
    "emit_delegated_memory_session_event",
    "map_delegated_memory_transport",
    "submit_delegated_memory_proposal",
]
