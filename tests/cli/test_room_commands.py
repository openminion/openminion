from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from openminion.cli.chat.commands.base import ChatCommandHandlers, handle_chat_command
from openminion.cli.chat.runtime import ChatRuntimeState
from openminion.modules.storage.runtime.session_store import RoomParticipant


def _handlers() -> ChatCommandHandlers:
    return ChatCommandHandlers(
        print_tools=Mock(),
        handle_debug_command=Mock(),
        handle_pair_status=Mock(),
        handle_pair_create=Mock(),
        handle_pair_revoke=Mock(),
        handle_trust_command=Mock(),
        handle_untrust_command=Mock(),
        handle_grants_command=Mock(),
        handle_policy_command=Mock(),
        handle_skill_command=Mock(),
        handle_identity_command=Mock(),
        handle_sidecar_command=Mock(),
    )


def _runtime_with_store(store) -> ChatRuntimeState:
    return ChatRuntimeState(
        endpoint=None,
        transport="in-process",
        inproc_runtime=SimpleNamespace(sessions=store),
        mode="single-process",
        auto_start=False,
        show_progress=False,
        quiet=False,
    )


def test_agent_command_requests_session_rotation_for_new_agent(capsys) -> None:
    result = handle_chat_command(
        line="/agent planner-safe",
        args=SimpleNamespace(config=None, home_root=None, data_root=None),
        config=SimpleNamespace(),
        agent_id="hello-agent",
        session_id="sess-1",
        transport="in-process",
        mode="single-process",
        runtime_state=_runtime_with_store(Mock()),
        last_artifacts=[],
        last_turn_debug={},
        handlers=_handlers(),
    )

    assert result.handled is True
    assert result.agent_id == "planner-safe"
    assert result.rotate_session_on_agent_change is True
    assert capsys.readouterr().out == ""


def test_invite_command_adds_agent_participant(capsys) -> None:
    store = Mock()
    store.list_participants.return_value = [
        RoomParticipant(
            id="p1",
            session_id="sess-1",
            participant_type="agent",
            participant_id="hello-agent",
            channel="cli",
            role="owner",
            display_name="hello-agent",
            joined_at="2026-04-02T00:00:00Z",
            left_at=None,
        ),
        RoomParticipant(
            id="p2",
            session_id="sess-1",
            participant_type="agent",
            participant_id="writer-agent",
            channel="cli",
            role="participant",
            display_name="writer-agent",
            joined_at="2026-04-02T00:00:01Z",
            left_at=None,
        ),
    ]

    result = handle_chat_command(
        line="/invite writer-agent --role participant",
        args=SimpleNamespace(config=None, home_root=None, data_root=None),
        config=SimpleNamespace(),
        agent_id="hello-agent",
        session_id="sess-1",
        transport="cli",
        mode="single-process",
        runtime_state=_runtime_with_store(store),
        last_artifacts=[],
        last_turn_debug={},
        handlers=_handlers(),
    )

    assert result.handled is True
    store.add_participant.assert_called_once()
    assert "invited agent=writer-agent participants=2" in capsys.readouterr().out


def test_activate_command_switches_agent_in_place(capsys) -> None:
    store = Mock()
    store.set_active_agent.return_value = None

    result = handle_chat_command(
        line="/activate writer-agent",
        args=SimpleNamespace(config=None, home_root=None, data_root=None),
        config=SimpleNamespace(),
        agent_id="hello-agent",
        session_id="sess-1",
        transport="cli",
        mode="single-process",
        runtime_state=_runtime_with_store(store),
        last_artifacts=[],
        last_turn_debug={},
        handlers=_handlers(),
    )

    assert result.handled is True
    assert result.agent_id == "writer-agent"
    assert result.rotate_session_on_agent_change is False
    store.set_active_agent.assert_called_once_with(
        session_id="sess-1",
        agent_id="writer-agent",
    )
    assert "active_agent=writer-agent" in capsys.readouterr().out


def test_kick_shorthand_defaults_to_agent_when_unambiguous(capsys) -> None:
    store = Mock()
    store.list_participants.return_value = [
        RoomParticipant(
            id="p1",
            session_id="sess-1",
            participant_type="agent",
            participant_id="writer-agent",
            channel="cli",
            role="participant",
            display_name="writer-agent",
            joined_at="2026-04-02T00:00:00Z",
            left_at=None,
        )
    ]
    store.remove_participant.return_value = True

    result = handle_chat_command(
        line="/kick writer-agent",
        args=SimpleNamespace(config=None, home_root=None, data_root=None),
        config=SimpleNamespace(),
        agent_id="hello-agent",
        session_id="sess-1",
        transport="cli",
        mode="single-process",
        runtime_state=_runtime_with_store(store),
        last_artifacts=[],
        last_turn_debug={},
        handlers=_handlers(),
    )

    assert result.handled is True
    store.remove_participant.assert_called_once_with(
        session_id="sess-1",
        participant_type="agent",
        participant_id="writer-agent",
    )
    assert "removed agent:writer-agent" in capsys.readouterr().out


def test_join_command_adds_human_and_marks_local_participant(capsys) -> None:
    store = Mock()
    store.list_participants.return_value = [
        RoomParticipant(
            id="p1",
            session_id="sess-1",
            participant_type="human",
            participant_id="alice",
            channel="cli",
            role="observer",
            display_name="alice",
            joined_at="2026-04-02T00:00:00Z",
            left_at=None,
        )
    ]

    result = handle_chat_command(
        line="/join alice --role observer",
        args=SimpleNamespace(config=None, home_root=None, data_root=None),
        config=SimpleNamespace(),
        agent_id="hello-agent",
        session_id="sess-1",
        transport="cli",
        mode="single-process",
        runtime_state=_runtime_with_store(store),
        last_artifacts=[],
        last_turn_debug={},
        handlers=_handlers(),
    )

    assert result.handled is True
    store.add_participant.assert_called_once()
    store.update_session_metadata.assert_called_once_with(
        session_id="sess-1",
        patch={"local_human_id": "alice"},
    )
    assert "joined human=alice role=observer participants=1" in capsys.readouterr().out


def test_participants_command_renders_current_participants(capsys) -> None:
    store = Mock()
    store.list_participants.return_value = [
        RoomParticipant(
            id="p1",
            session_id="sess-1",
            participant_type="human",
            participant_id="alice",
            channel="cli",
            role="owner",
            display_name="alice",
            joined_at="2026-04-02T00:00:00Z",
            left_at=None,
        ),
        RoomParticipant(
            id="p2",
            session_id="sess-1",
            participant_type="agent",
            participant_id="writer-agent",
            channel="cli",
            role="participant",
            display_name="writer-agent",
            joined_at="2026-04-02T00:00:01Z",
            left_at=None,
        ),
    ]
    store.get_active_agent.return_value = "writer-agent"

    result = handle_chat_command(
        line="/participants",
        args=SimpleNamespace(config=None, home_root=None, data_root=None),
        config=SimpleNamespace(),
        agent_id="writer-agent",
        session_id="sess-1",
        transport="cli",
        mode="single-process",
        runtime_state=_runtime_with_store(store),
        last_artifacts=[],
        last_turn_debug={},
        handlers=_handlers(),
    )

    assert result.handled is True
    rendered = capsys.readouterr().out
    assert "alice" in rendered
    assert "writer-agent" in rendered
    assert "yes" in rendered
