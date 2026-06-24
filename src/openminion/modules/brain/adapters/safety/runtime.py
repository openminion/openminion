from typing import Any

from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION
from openminion.modules.brain.schemas import Command, WorkingState, PolicyDecision

try:
    from openminion.modules.brain.runtime.safety import SafetyService, SafetyState

    SAFETY_AVAILABLE = True
except ImportError:
    SAFETY_AVAILABLE = False


class SafetyctlAdapter:
    """Adapter for safety operations wrapping SafetyService."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self) -> None:
        if SAFETY_AVAILABLE:
            self._svc = SafetyService()
        else:
            self._svc = None

    def evaluate(
        self,
        *,
        command: Command,
        working_state: WorkingState,
        session_context: dict[str, Any],
    ) -> PolicyDecision:
        if self._svc is None:
            return PolicyDecision(outcome="ALLOW")

        state = self._svc.state
        if state in (
            SafetyState.PANICKING,
            SafetyState.PANICKED,
            SafetyState.KILLING,
            SafetyState.KILLED,
        ):
            return PolicyDecision(
                outcome="DENY", explanation=f"Safety state: {state.value}"
            )

        return PolicyDecision(outcome="ALLOW")
