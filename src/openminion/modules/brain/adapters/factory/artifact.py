from typing import Any

from .modes import mode_is_local, raise_if_strict


def create_artifact_adapter(mode: str = "auto", config: Any = None) -> Any:
    from openminion.modules.brain.adapters.tool import LocalToolAdapter

    if mode_is_local(mode):
        return LocalToolAdapter()
    try:
        from openminion.modules.artifact.control import ArtifactCtl
        from ..artifact import ArtifactctlAdapter

        return ArtifactctlAdapter(ArtifactCtl(config or {}))
    except ImportError:
        raise_if_strict(mode)
        return LocalToolAdapter()
