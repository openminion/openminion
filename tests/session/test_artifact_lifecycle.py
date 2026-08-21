from __future__ import annotations

import pytest

from openminion.modules.session.artifact_lifecycle import ArtifactLifecycleError
from openminion.modules.session.storage.store import SQLiteSessionStore
from openminion.modules.telemetry.events.catalog import (
    DESKTOP_ARTIFACT_DETACHED,
    DESKTOP_ARTIFACT_RESTORED,
)


def test_artifact_decisions_are_transactional_and_idempotent(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    session_id = store.create_session(initial_agent_id="agent.main")
    artifact_ref = f"artifact://sha256/{'a' * 64}"

    assert (
        store.apply_artifact_decision(
            session_id,
            artifact_ref=artifact_ref,
            detached=True,
            reason_code="desktop_user_action",
            request_id="detach-1",
        )
        == "applied"
    )
    assert (
        store.apply_artifact_decision(
            session_id,
            artifact_ref=artifact_ref,
            detached=True,
            reason_code="desktop_user_action",
            request_id="detach-2",
        )
        == "already_applied"
    )
    assert store.get_detached_artifact_refs(session_id) == [artifact_ref]

    assert (
        store.apply_artifact_decision(
            session_id,
            artifact_ref=artifact_ref,
            detached=False,
            reason_code="desktop_user_action",
            request_id="restore-1",
        )
        == "applied"
    )
    assert store.get_detached_artifact_refs(session_id) == []
    page = store.get_artifact_catalog_event_page(
        session_id,
        after_seq=0,
        high_water=0,
        limit=500,
    )
    event_types = [event["event_type"] for event in page["events"]]
    assert event_types.count(DESKTOP_ARTIFACT_DETACHED) == 1
    assert event_types.count(DESKTOP_ARTIFACT_RESTORED) == 1


def test_artifact_catalog_page_freezes_high_water_and_is_bounded(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    session_id = store.create_session(initial_agent_id="agent.main")
    for index in range(3):
        store.append_turn(
            session_id,
            "user",
            f"turn {index}",
            attachments=[f"artifact://sha256/{index:064x}"],
        )

    first = store.get_artifact_catalog_event_page(
        session_id,
        after_seq=0,
        high_water=0,
        limit=2,
    )
    assert len(first["events"]) == 2
    assert first["complete"] is False
    frozen_high_water = first["high_water"]

    store.append_turn(session_id, "user", "later")
    second = store.get_artifact_catalog_event_page(
        session_id,
        after_seq=first["next_after_seq"],
        high_water=frozen_high_water,
        limit=500,
    )
    assert second["complete"] is True
    assert all(event["seq"] <= frozen_high_water for event in second["events"])


def test_artifact_projection_rejects_identity_257_and_corrupt_overflow(
    tmp_path,
) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    session_id = store.create_session(initial_agent_id="agent.main")
    for index in range(256):
        assert (
            store.apply_artifact_decision(
                session_id,
                artifact_ref=f"artifact://sha256/{index:064x}",
                detached=True,
                reason_code="desktop_user_action",
                request_id=f"detach-{index}",
            )
            == "applied"
        )

    with pytest.raises(ArtifactLifecycleError) as full:
        store.apply_artifact_decision(
            session_id,
            artifact_ref=f"artifact://sha256/{256:064x}",
            detached=True,
            reason_code="desktop_user_action",
            request_id="detach-256",
        )
    assert full.value.code == "artifact_state_backpressure"
    assert len(store.get_detached_artifact_refs(session_id)) == 256

    store._record_store.execute_count(
        """
        INSERT INTO session_detached_artifacts(
          session_id, artifact_ref, event_id, event_seq, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            session_id,
            f"artifact://sha256/{256:064x}",
            "corrupt-overflow",
            1000,
            "2026-08-20T00:00:00+00:00",
        ),
    )
    with pytest.raises(ArtifactLifecycleError) as corrupt:
        store.get_detached_artifact_refs(session_id)
    assert corrupt.value.code == "artifact_state_too_large"
