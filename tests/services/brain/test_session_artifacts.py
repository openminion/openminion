from __future__ import annotations

from openminion.modules.brain.interfaces import SessionArtifactAPI
from openminion.modules.session.artifact_lifecycle import ArtifactLifecycleError
from openminion.services.brain.session_artifacts import (
    SessionArtifactFacade,
    SessionArtifactOperationError,
    SessionArtifactUnavailable,
)


class _SessionArtifacts:
    def get_artifact_catalog_event_page(
        self, session_id, *, after_seq, high_water, limit
    ):
        return {
            "high_water": high_water,
            "events": [],
            "next_after_seq": after_seq,
            "complete": True,
        }

    def get_detached_artifact_refs(self, session_id, *, limit=256):
        return [f"artifact://sha256/{'a' * 64}"]

    def apply_artifact_decision(
        self,
        session_id,
        *,
        artifact_ref,
        detached,
        reason_code,
        request_id,
    ):
        return "applied"


def test_session_artifact_facade_uses_narrow_runtime_protocol() -> None:
    api = _SessionArtifacts()
    assert isinstance(api, SessionArtifactAPI)
    facade = SessionArtifactFacade(api)

    assert facade.get_artifact_catalog_event_page(
        "session-1", after_seq=2, high_water=7, limit=500
    ) == {
        "high_water": 7,
        "events": [],
        "next_after_seq": 2,
        "complete": True,
    }
    assert len(facade.get_detached_artifact_refs("session-1")) == 1
    assert (
        facade.apply_artifact_decision(
            "session-1",
            artifact_ref=f"artifact://sha256/{'a' * 64}",
            detached=True,
            reason_code="desktop_user_action",
            request_id="request-1",
        )
        == "applied"
    )


def test_session_artifact_facade_rejects_invalid_owner_results() -> None:
    api = _SessionArtifacts()
    api.get_artifact_catalog_event_page = lambda *args, **kwargs: {"events": []}
    facade = SessionArtifactFacade(api)

    try:
        facade.get_artifact_catalog_event_page(
            "session-1", after_seq=0, high_water=0, limit=500
        )
    except SessionArtifactUnavailable:
        pass
    else:
        raise AssertionError("invalid owner page crossed the service boundary")


def test_session_artifact_facade_normalizes_projection_errors() -> None:
    api = _SessionArtifacts()

    def fail(*args, **kwargs):
        raise ArtifactLifecycleError(
            "artifact_state_backpressure", "internal projection detail"
        )

    api.apply_artifact_decision = fail
    facade = SessionArtifactFacade(api)

    try:
        facade.apply_artifact_decision(
            "session-1",
            artifact_ref=f"artifact://sha256/{'a' * 64}",
            detached=True,
            reason_code="desktop_user_action",
            request_id="request-1",
        )
    except SessionArtifactOperationError as exc:
        assert exc.code == "artifact_state_backpressure"
        assert "internal projection detail" not in str(exc)
    else:
        raise AssertionError("projection error was not normalized")
