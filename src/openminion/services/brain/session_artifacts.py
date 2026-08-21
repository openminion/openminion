from __future__ import annotations

from typing import Any

from openminion.modules.artifact.refs import is_canonical_artifact_ref
from openminion.modules.brain.interfaces import SessionArtifactAPI
from openminion.modules.session.artifact_lifecycle import ArtifactLifecycleError


class SessionArtifactUnavailable(RuntimeError):
    pass


class SessionArtifactOperationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SessionArtifactFacade:
    def __init__(self, session_api: SessionArtifactAPI) -> None:
        self._session_api = session_api

    def get_artifact_catalog_event_page(
        self,
        session_id: str,
        *,
        after_seq: int,
        high_water: int,
        limit: int,
    ) -> dict[str, Any]:
        page = self._session_api.get_artifact_catalog_event_page(
            session_id,
            after_seq=after_seq,
            high_water=high_water,
            limit=limit,
        )
        events = page.get("events") if isinstance(page, dict) else None
        if (
            not isinstance(page, dict)
            or set(page) != {"high_water", "events", "next_after_seq", "complete"}
            or type(page.get("high_water")) is not int
            or not isinstance(events, list)
            or len(events) > limit
            or type(page.get("next_after_seq")) is not int
            or type(page.get("complete")) is not bool
        ):
            raise SessionArtifactUnavailable(
                "Session artifact page does not match the bounded contract."
            )
        resolved_high_water = int(page["high_water"])
        next_after_seq = int(page["next_after_seq"])
        sequences = [
            event.get("seq") if isinstance(event, dict) else None for event in events
        ]
        if (
            resolved_high_water < 0
            or (high_water > 0 and resolved_high_water != high_water)
            or any(type(seq) is not int for seq in sequences)
            or any(
                not isinstance(event.get("event_type"), str)
                for event in events
                if isinstance(event, dict)
            )
            or any(
                current <= (after_seq if index == 0 else sequences[index - 1])
                for index, current in enumerate(sequences)
            )
            or any(seq > resolved_high_water for seq in sequences)
            or next_after_seq != (sequences[-1] if sequences else after_seq)
            or bool(page["complete"]) != (next_after_seq >= resolved_high_water)
        ):
            raise SessionArtifactOperationError(
                "event_invalid", "Session artifact event page is invalid."
            )
        return page

    def get_detached_artifact_refs(
        self, session_id: str, *, limit: int = 256
    ) -> list[str]:
        try:
            refs = self._session_api.get_detached_artifact_refs(session_id, limit=limit)
        except ArtifactLifecycleError as exc:
            raise SessionArtifactOperationError(
                exc.code, "Artifact state could not be read."
            ) from exc
        if (
            not isinstance(refs, list)
            or len(refs) > limit
            or any(not is_canonical_artifact_ref(ref) for ref in refs)
        ):
            raise SessionArtifactUnavailable(
                "Detached artifact state does not match the bounded contract."
            )
        return refs

    def apply_artifact_decision(
        self,
        session_id: str,
        *,
        artifact_ref: str,
        detached: bool,
        reason_code: str,
        request_id: str,
    ) -> str:
        try:
            outcome = self._session_api.apply_artifact_decision(
                session_id,
                artifact_ref=artifact_ref,
                detached=detached,
                reason_code=reason_code,
                request_id=request_id,
            )
        except ArtifactLifecycleError as exc:
            raise SessionArtifactOperationError(
                exc.code, "Artifact state could not be updated."
            ) from exc
        if outcome not in {"applied", "already_applied"}:
            raise SessionArtifactUnavailable(
                "Artifact decision returned an unsupported outcome."
            )
        return outcome
