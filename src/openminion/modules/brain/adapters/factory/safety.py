from typing import Any

from .modes import mode_is_local


def create_safety_adapter(mode: str = "auto") -> Any:
    from openminion.modules.brain.adapters.policy import LocalPolicyAdapter

    if mode_is_local(mode):
        return LocalPolicyAdapter()
    from ..safety import SafetyctlAdapter

    return SafetyctlAdapter()
