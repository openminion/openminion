"""API runtime access to session-scoped artifact services."""

from typing import Any


class RuntimeSessionArtifactsMixin:
    sessions: Any

    def resolve_agent_service(self, agent_id: str | None = None) -> Any:
        raise NotImplementedError

    def session_artifact_facade(self, session_id: str) -> Any:
        from openminion.services.brain.service import BrainBridgeService
        from openminion.services.brain.session_artifacts import (
            SessionArtifactUnavailable,
        )

        record = self.sessions.get_session(session_id)
        if record is None:
            raise SessionArtifactUnavailable("Session is unavailable.")
        agent_id = str(getattr(record, "active_agent_id", "") or "").strip() or None
        service = self.resolve_agent_service(agent_id)
        if not isinstance(service, BrainBridgeService):
            raise SessionArtifactUnavailable(
                "Session artifact operations are not supported by this runtime."
            )
        return service.session_artifact_facade()


__all__ = ["RuntimeSessionArtifactsMixin"]
