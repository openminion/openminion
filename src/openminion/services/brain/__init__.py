from openminion.modules.brain.paths import (
    resolve_brain_runtime_db_path,
    resolve_brain_sessions_db_path,
)

__all__ = [
    "BrainBridgeService",
    "resolve_brain_runtime_db_path",
    "resolve_brain_sessions_db_path",
]


def __getattr__(name: str):
    if name == "BrainBridgeService":
        from openminion.services.brain.service import BrainBridgeService

        return BrainBridgeService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
