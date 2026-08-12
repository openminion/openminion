from typing import Any

from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION
from openminion.modules.brain.runtime.safety import SafetyService, SafetyState
from openminion.modules.brain.schemas import Command, WorkingState, PolicyDecision


class SafetyctlAdapter:
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self) -> None:
        self._svc = SafetyService()

    def evaluate(
        self,
        *,
        command: Command,
        working_state: WorkingState,
        session_context: dict[str, Any],
    ) -> PolicyDecision:
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
