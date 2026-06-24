from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from openminion.cli.commands import chat as chat_command
from openminion.modules.storage.runtime.context import build_runtime_storage
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import DEFAULT_DATABASE_PATH


def _build_session_store(root: Path) -> tuple[SessionStore, object]:
    runtime_storage = build_runtime_storage(root / DEFAULT_DATABASE_PATH, env={})
    return runtime_storage.sessions, runtime_storage


def test_latest_session_agent_id_prefers_selected_profile_event(tmp_path: Path) -> None:
    store, runtime_storage = _build_session_store(tmp_path)
    try:
        store.resolve_session(
            agent_id="hello-agent",
            channel="console",
            target="cli-chat",
            session_id="shared-chat",
        )
        store.append_message(
            session_id="shared-chat",
            role="outbound",
            body="hello",
            metadata={"agent": "hello-agent"},
        )
        store.append_event(
            session_id="shared-chat",
            event_type="client.attach",
            payload={"selected_profile_id": "planner-safe"},
        )
    finally:
        runtime_storage.close()

    with mock.patch(
        "openminion.cli.commands.chat.resolve_cli_roots",
        return_value=SimpleNamespace(data_root=tmp_path, env={}),
    ):
        agent_id = chat_command._latest_session_agent_id(
            session_id="shared-chat",
            config_path="ignored.json",
        )

    assert agent_id == "planner-safe"


def test_latest_session_agent_id_falls_back_to_outbound_message_metadata(
    tmp_path: Path,
) -> None:
    store, runtime_storage = _build_session_store(tmp_path)
    try:
        store.resolve_session(
            agent_id="hello-agent",
            channel="console",
            target="cli-chat",
            session_id="shared-chat",
        )
        store.append_message(
            session_id="shared-chat",
            role="outbound",
            body="hello",
            metadata={"agent_id": "planner-safe"},
        )
    finally:
        runtime_storage.close()

    with mock.patch(
        "openminion.cli.commands.chat.resolve_cli_roots",
        return_value=SimpleNamespace(data_root=tmp_path, env={}),
    ):
        agent_id = chat_command._latest_session_agent_id(
            session_id="shared-chat",
            config_path="ignored.json",
        )

    assert agent_id == "planner-safe"


def test_session_profile_mismatch_message_includes_reset_guidance() -> None:
    message = chat_command._session_profile_mismatch_message(
        session_id="shared-chat",
        agent_id="hello-agent",
        agent_resolution={
            "source": "explicit",
            "session_agent_id": "planner-safe",
            "default_agent_id": "hello-agent",
        },
        reset_requested=False,
    )

    assert "shared-chat" in message
    assert "planner-safe" in message
    assert "hello-agent" in message
    assert "--reset-session" in message


def test_session_profile_mismatch_skips_room_participant_agent(tmp_path: Path) -> None:
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
    finally:
        runtime_storage.close()

    with mock.patch(
        "openminion.cli.commands.chat.resolve_cli_roots",
        return_value=SimpleNamespace(data_root=tmp_path, env={}),
    ):
        message = chat_command._session_profile_mismatch_message(
            session_id="shared-room",
            agent_id="planner-safe",
            agent_resolution={
                "source": "explicit",
                "session_agent_id": "hello-agent",
                "default_agent_id": "hello-agent",
            },
            reset_requested=False,
            config_path="ignored.json",
        )

    assert message == ""
