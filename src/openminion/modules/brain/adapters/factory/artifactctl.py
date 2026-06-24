"""ArtifactCtl resolution for brain adapter factories."""

from typing import Any

from openminion.modules.artifact.refs import create_default_artifactctl


def resolve_artifactctl(*, artifactctl: Any | None) -> Any | None:
    if artifactctl is not None:
        return artifactctl
    try:
        return create_default_artifactctl()
    except Exception:
        return None


__all__ = ["create_default_artifactctl", "resolve_artifactctl"]
