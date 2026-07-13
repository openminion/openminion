from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.storage.runtime.context import build_runtime_storage
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import DEFAULT_DATABASE_PATH


def _build_session_store(root: Path) -> tuple[SessionStore, object]:
    runtime_storage = build_runtime_storage(root / DEFAULT_DATABASE_PATH, env={})
    return runtime_storage.sessions, runtime_storage


def test_explicit_session_rejects_non_bound_agent(tmp_path: Path) -> None:
    store, runtime_storage = _build_session_store(tmp_path)
    try:
        session = store.resolve_session(
            agent_id="hello-agent",
            channel="console",
            target="cli-chat",
            session_id="shared-chat",
        )
        with pytest.raises(ValueError) as exc_info:
            store.resolve_session(
                agent_id="planner-safe",
                channel="console",
                target="cli-chat",
                session_id=session.id,
            )
        participants = store.list_participants(session.id)
    finally:
        runtime_storage.close()

    message = str(exc_info.value)
    assert "shared-chat" in message
    assert "does not include agent" in message
    assert "planner-safe" in message
    assert [(item.participant_type, item.participant_id) for item in participants] == [
        ("agent", "hello-agent")
    ]


def test_explicit_session_accepts_invited_agent_participant(tmp_path: Path) -> None:
    store, runtime_storage = _build_session_store(tmp_path)
    try:
        session = store.resolve_session(
            agent_id="hello-agent",
            channel="console",
            target="cli-chat",
            session_id="shared-chat",
        )
        store.add_participant(
            session_id=session.id,
            participant_type="agent",
            participant_id="planner-safe",
            channel="console",
            role="participant",
            display_name="Planner",
        )
        resolved = store.resolve_session(
            agent_id="planner-safe",
            channel="console",
            target="cli-chat",
            session_id=session.id,
        )
    finally:
        runtime_storage.close()

    assert resolved.id == session.id


def test_room_session_accepts_agent_participant(tmp_path: Path) -> None:
    store, runtime_storage = _build_session_store(tmp_path)
    try:
        session = store.create_room(
            channel="cli",
            target="room",
            session_id="shared-room",
        )
        store.add_participant(
            session_id=session.id,
            participant_type="agent",
            participant_id="hello-agent",
            channel="cli",
            role="owner",
            display_name="hello-agent",
        )
        store.add_participant(
            session_id=session.id,
            participant_type="agent",
            participant_id="planner-safe",
            channel="cli",
            role="participant",
            display_name="planner-safe",
        )
        resolved = store.resolve_session(
            agent_id="planner-safe",
            channel="cli",
            target="room",
            session_id=session.id,
        )
    finally:
        runtime_storage.close()

    assert resolved.id == session.id


def test_room_active_agent_switch_requires_participant(tmp_path: Path) -> None:
    store, runtime_storage = _build_session_store(tmp_path)
    try:
        session = store.create_room(
            channel="cli",
            target="room",
            session_id="shared-room",
        )
        store.add_participant(
            session_id=session.id,
            participant_type="agent",
            participant_id="hello-agent",
            channel="cli",
            role="owner",
            display_name="hello-agent",
        )
        store.add_participant(
            session_id=session.id,
            participant_type="agent",
            participant_id="planner-safe",
            channel="cli",
            role="participant",
            display_name="planner-safe",
        )
        updated = store.set_active_agent(
            session_id=session.id,
            agent_id="planner-safe",
        )
        with pytest.raises(ValueError):
            store.set_active_agent(
                session_id=session.id,
                agent_id="non-participant",
            )
    finally:
        runtime_storage.close()

    assert updated.active_agent_id == "planner-safe"
