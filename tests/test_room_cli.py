from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openminion.cli.commands import room as room_command
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database


def test_run_room_create_adds_agents_and_prints_summary(capsys) -> None:
    runtime = MagicMock()
    runtime.sessions.create_room.return_value = SimpleNamespace(
        id="room-123",
        channel="cli",
    )
    runtime.sessions.list_participants.return_value = [object(), object()]
    runtime.sessions.get_active_agent.return_value = "writer-agent"
    runtime.config.agents = {
        "writer-agent": object(),
        "review-agent": object(),
    }
    runtime.close.return_value = None

    args = SimpleNamespace(
        name="Spec Review",
        agents=["writer-agent", "review-agent"],
        humans=[],
        channel="cli",
        target="room",
        routing_mode="broadcast",
        config=None,
        home_root=None,
        data_root=None,
    )

    with patch("openminion.api.runtime.APIRuntime") as MockRuntime:
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


def test_run_room_create_adds_owner_human_and_participant_agents(capsys) -> None:
    runtime = MagicMock()
    runtime.config.agents = {"writer-agent": object(), "review-agent": object()}
    runtime.sessions.create_room.return_value = SimpleNamespace(
        id="room-123",
        channel="cli",
    )
    runtime.sessions.list_participants.return_value = [object(), object(), object()]
    runtime.sessions.get_active_agent.return_value = "writer-agent"
    args = SimpleNamespace(
        name="Spec Review",
        agents=["writer-agent", "review-agent"],
        humans=["  Alice   Smith  "],
        channel="",
        target="",
        routing_mode="sequential",
        config=None,
        home_root=None,
        data_root=None,
    )

    with patch("openminion.api.runtime.APIRuntime") as MockRuntime:
        MockRuntime.from_config_path.return_value = runtime
        result = room_command.run_room_create(args)

    assert result == 0
    runtime.sessions.create_room.assert_called_once_with(
        channel="cli",
        target="Spec Review",
        metadata={
            "name": "Spec Review",
            "room_routing_mode": "sequential",
            "local_human_id": "alice smith",
        },
    )
    participant_calls = runtime.sessions.add_participant.call_args_list
    assert participant_calls[0].kwargs["participant_type"] == "human"
    assert participant_calls[0].kwargs["participant_id"] == "alice smith"
    assert participant_calls[0].kwargs["role"] == "owner"
    assert [call.kwargs["role"] for call in participant_calls[1:]] == [
        "participant",
        "participant",
    ]
    assert "participants=3" in capsys.readouterr().out


def test_run_room_create_rejects_invalid_inputs_before_mutation(capsys) -> None:
    runtime = MagicMock()
    runtime.config.agents = {"writer-agent": object()}
    args = SimpleNamespace(
        name="Spec Review",
        agents=["missing-agent"],
        humans=["   "],
        channel="cli",
        target="room",
        routing_mode="addressed",
        config=None,
        home_root=None,
        data_root=None,
    )

    with patch("openminion.api.runtime.APIRuntime") as MockRuntime:
        MockRuntime.from_config_path.return_value = runtime
        result = room_command.run_room_create(args)

    assert result == 1
    runtime.sessions.create_room.assert_not_called()
    runtime.sessions.add_participant.assert_not_called()
    assert "create failed" in capsys.readouterr().err


def test_run_room_create_rejects_duplicate_participants_before_mutation(
    capsys,
) -> None:
    runtime = MagicMock()
    runtime.config.agents = {"writer-agent": object()}
    runtime.close.return_value = None

    for agents, humans, expected in (
        (["writer-agent"], ["Alice", " alice "], "duplicate human id"),
        (["writer-agent", "writer-agent"], [], "duplicate agent id"),
    ):
        args = SimpleNamespace(
            name="Spec Review",
            agents=agents,
            humans=humans,
            channel="cli",
            target="room",
            routing_mode="addressed",
            config=None,
            home_root=None,
            data_root=None,
        )
        with patch("openminion.api.runtime.APIRuntime") as MockRuntime:
            MockRuntime.from_config_path.return_value = runtime
            assert room_command.run_room_create(args) == 1
        assert expected in capsys.readouterr().err

    runtime.sessions.create_room.assert_not_called()
    runtime.sessions.add_participant.assert_not_called()


def test_run_room_invite_human_participant(capsys) -> None:
    runtime = MagicMock()
    runtime.sessions.get_session.return_value = SimpleNamespace(
        session_key="room:room-123",
        channel="slack",
        metadata={},
    )
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

    with patch("openminion.api.runtime.APIRuntime") as MockRuntime:
        MockRuntime.from_config_path.return_value = runtime
        result = room_command.run_room_invite(args)

    assert result == 0
    runtime.sessions.add_participant.assert_called_once_with(
        session_id="room-123",
        participant_type="human",
        participant_id="alice",
        channel="slack",
        role="observer",
        display_name="alice",
    )
    assert "invited human=alice" in capsys.readouterr().out


def test_run_room_invite_owner_sets_missing_local_human_only_once() -> None:
    runtime = MagicMock()
    runtime.sessions.get_session.return_value = SimpleNamespace(
        session_key="room:room-123",
        channel="cli",
        metadata={},
    )
    runtime.sessions.list_participants.return_value = [object()]
    args = SimpleNamespace(
        session_id="room-123",
        human=" Alice ",
        agent="",
        role="owner",
        config=None,
        home_root=None,
        data_root=None,
    )

    with patch("openminion.api.runtime.APIRuntime") as MockRuntime:
        MockRuntime.from_config_path.return_value = runtime
        assert room_command.run_room_invite(args) == 0

    runtime.sessions.update_session_metadata.assert_called_once_with(
        session_id="room-123",
        patch={"local_human_id": "alice"},
    )

    runtime.reset_mock()
    runtime.sessions.get_session.return_value = SimpleNamespace(
        session_key="room:room-123",
        channel="cli",
        metadata={"local_human_id": "alice"},
    )
    runtime.sessions.list_participants.return_value = [object(), object()]
    args.human = "Bob"
    with patch("openminion.api.runtime.APIRuntime") as MockRuntime:
        MockRuntime.from_config_path.return_value = runtime
        assert room_command.run_room_invite(args) == 0

    runtime.sessions.update_session_metadata.assert_not_called()


def test_run_room_invite_owner_upgrades_humanless_room_with_real_store(
    tmp_path,
) -> None:
    database_path = tmp_path / "state" / "openminion.db"
    migrate_database(database_path)
    connection = connect_database(database_path)
    store = SessionStore(connection)
    room = store.create_room(channel="cli", target="room")
    runtime = SimpleNamespace(
        sessions=store,
        config=SimpleNamespace(agents={}),
        close=lambda: None,
    )
    args = SimpleNamespace(
        session_id=room.id,
        human="Alice",
        agent="",
        role="owner",
        config=None,
        home_root=None,
        data_root=None,
    )

    try:
        with patch.object(room_command, "_open_room_runtime", return_value=runtime):
            assert room_command.run_room_invite(args) == 0

        updated = store.get_session(room.id)
        assert updated is not None
        assert updated.metadata["local_human_id"] == "alice"
        participant = store.get_participant(room.id, "human", "alice")
        assert participant is not None
        assert participant.role == "owner"
    finally:
        connection.close()


def test_run_room_invite_rejects_ambiguous_or_invalid_agent(capsys) -> None:
    ambiguous = SimpleNamespace(session_id="room-123", human="alice", agent="writer")
    assert room_command.run_room_invite(ambiguous) == 2

    runtime = MagicMock()
    runtime.config.agents = {"writer": object()}
    runtime.sessions.get_session.return_value = SimpleNamespace(
        session_key="room:room-123",
        channel="cli",
        metadata={},
    )
    args = SimpleNamespace(
        session_id="room-123",
        human="",
        agent="missing",
        role="participant",
        config=None,
        home_root=None,
        data_root=None,
    )
    with patch("openminion.api.runtime.APIRuntime") as MockRuntime:
        MockRuntime.from_config_path.return_value = runtime
        assert room_command.run_room_invite(args) == 1

    runtime.sessions.add_participant.assert_not_called()
    assert "invite failed" in capsys.readouterr().err
