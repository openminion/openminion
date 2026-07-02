from typing import Any

def create_safety_adapter(mode: str = "auto") -> Any:
    del mode
    from openminion.modules.brain.runtime.safety import SafetyService

    return SafetyService()
