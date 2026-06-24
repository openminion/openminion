from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openminion.cli.commands import room as room_command


def test_run_room_create_adds_agents_and_prints_summary(capsys) -> None:
    runtime = MagicMock()
    runtime.sessions.create_room.return_value = SimpleNamespace(
        id="room-123",
        channel="cli",
    )
    runtime.sessions.list_participants.return_value = [object(), object()]
    runtime.sessions.get_active_agent.return_value = "writer-agent"
    runtime.close.return_value = None

    args = SimpleNamespace(
        name="Spec Review",
        agents=["writer-agent", "review-agent"],
        channel="cli",
        target="room",
        routing_mode="broadcast",
        config=None,
        home_root=None,
        data_root=None,
    )

    with patch.object(room_command, "APIRuntime") as MockRuntime:
        MockRuntime.from_config_path.return_value = runtime
        result = room_command.run_room_create(args)

    assert result == 0
    runtime.sessions.create_room.assert_called_once()
    assert runtime.sessions.add_participant.call_count == 2
    runtime.sessions.set_active_agent.assert_called_once_with(
        session_id="room-123",
        agent_id="writer-agent",
    )
    output = capsys.readouterr().out
    assert "room=room-123" in output
    assert "participants=2" in output
    assert "active_agent=writer-agent" in output


def test_run_room_invite_human_participant(capsys) -> None:
    runtime = MagicMock()
    runtime.sessions.list_participants.return_value = [object()]
    runtime.close.return_value = None
    args = SimpleNamespace(
        session_id="room-123",
        human="alice",
        agent="",
        role="observer",
        config=None,
        home_root=None,
        data_root=None,
    )

    with patch.object(room_command, "APIRuntime") as MockRuntime:
        MockRuntime.from_config_path.return_value = runtime
        result = room_command.run_room_invite(args)

    assert result == 0
    runtime.sessions.add_participant.assert_called_once_with(
        session_id="room-123",
        participant_type="human",
        participant_id="alice",
        channel="cli",
        role="observer",
        display_name="alice",
    )
    assert "invited human=alice" in capsys.readouterr().out
