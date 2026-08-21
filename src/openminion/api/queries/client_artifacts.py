from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openminion.api.runtime import APIRuntime
from openminion.services.brain.session_artifacts import (
    SessionArtifactFacade,
    SessionArtifactUnavailable,
)


@dataclass
class ArtifactQueryError(RuntimeError):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def resolve_session_artifact_facade(
    runtime: APIRuntime,
    session_id: str,
) -> SessionArtifactFacade:
    record = runtime.sessions.get_session(session_id)
    if record is None:
        raise ArtifactQueryError("artifact_not_found", "Artifact is unavailable.")
    if getattr(record, "status", None) != "active":
        raise ArtifactQueryError("session_closed", "Session is not active.")
    try:
        facade = runtime.session_artifact_facade(session_id)
    except SessionArtifactUnavailable as exc:
        raise ArtifactQueryError(
            "artifact_unsupported",
            "Artifact operations are unavailable for this session.",
        ) from exc
    if not isinstance(facade, SessionArtifactFacade):
        raise ArtifactQueryError(
            "artifact_unsupported",
            "Artifact operations are unavailable for this session.",
        )
    return facade


def artifact_event_refs(event: dict[str, Any]) -> list[str]:
    if event.get("event_type") != "turn.user":
        return []
    refs = event.get("refs")
    values = refs.get("artifact_refs") if isinstance(refs, dict) else None
    if not isinstance(values, list):
        return []
    return [str(value) for value in values]
