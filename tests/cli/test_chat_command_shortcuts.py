from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from openminion.cli.chat.commands.base import ChatCommandHandlers, handle_chat_command
from openminion.cli.chat.runtime import ChatRuntimeState
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


def _runtime_state() -> ChatRuntimeState:
    return ChatRuntimeState(
        endpoint=None,
        transport="in-process",
        inproc_runtime=None,
        mode="single-process",
        auto_start=False,
        show_progress=False,
        quiet=False,
    )


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


@pytest.fixture()
def session_store(tmp_path: Path) -> SQLiteSessionStore:
    store = SQLiteSessionStore(tmp_path / "chat-shortcuts.db")
    yield store
    store.close()


def test_nl_tool_inventory_prompt_is_not_short_circuited_locally() -> None:
    handlers = _handlers()
    runtime_state = _runtime_state()

    result = handle_chat_command(
        line="what tools are available",
        args=SimpleNamespace(),
        config=SimpleNamespace(),
        agent_id="agent",
        session_id="session",
        transport="in-process",
        mode="single-process",
        runtime_state=runtime_state,
        last_artifacts=[],
        last_turn_debug={},
        handlers=handlers,
    )

    assert result.handled is False
    handlers.print_tools.assert_not_called()


def test_explicit_tools_command_remains_supported() -> None:
    handlers = _handlers()
    runtime_state = _runtime_state()

    result = handle_chat_command(
        line="/tools",
        args=SimpleNamespace(),
        config=SimpleNamespace(),
        agent_id="agent",
        session_id="session",
        transport="in-process",
        mode="single-process",
        runtime_state=runtime_state,
        last_artifacts=[],
        last_turn_debug={},
        handlers=handlers,
    )

    assert result.handled is True
    handlers.print_tools.assert_called_once()


def test_nl_skill_ingest_prompt_is_not_short_circuited_locally() -> None:
    handlers = _handlers()
    runtime_state = _runtime_state()

    result = handle_chat_command(
        line="read this /tmp/SKILL.md and learn it",
        args=SimpleNamespace(),
        config=SimpleNamespace(),
        agent_id="agent",
        session_id="session",
        transport="in-process",
        mode="single-process",
        runtime_state=runtime_state,
        last_artifacts=[],
        last_turn_debug={},
        handlers=handlers,
    )

    assert result.handled is False


def test_explicit_skill_command_remains_supported() -> None:
    handlers = _handlers()
    runtime_state = _runtime_state()

    result = handle_chat_command(
        line="/skill ingest /tmp/SKILL.md",
        args=SimpleNamespace(),
        config=SimpleNamespace(),
        agent_id="agent",
        session_id="session",
        transport="in-process",
        mode="single-process",
        runtime_state=runtime_state,
        last_artifacts=[],
        last_turn_debug={},
        handlers=handlers,
    )

    assert result.handled is True
    handlers.handle_skill_command.assert_called_once()


def test_new_session_command_is_supported() -> None:
    handlers = _handlers()
    runtime_state = _runtime_state()

    result = handle_chat_command(
        line="/new session",
        args=SimpleNamespace(),
        config=SimpleNamespace(),
        agent_id="agent",
        session_id="session",
        transport="in-process",
        mode="single-process",
        runtime_state=runtime_state,
        last_artifacts=[],
        last_turn_debug={},
        handlers=handlers,
    )

    assert result.handled is True
    assert result.new_session is True


def test_sessions_command_reuses_sessions_cli_surface() -> None:
    handlers = _handlers()
    runtime_state = _runtime_state()

    with patch(
        "openminion.cli.commands.sessions.run_sessions_list"
    ) as run_sessions_list:
        result = handle_chat_command(
            line="/sessions",
            args=SimpleNamespace(config="cfg.json", home_root=None, data_root=None),
            config=SimpleNamespace(),
            agent_id="agent",
            session_id="session",
            transport="in-process",
            mode="single-process",
            runtime_state=runtime_state,
            last_artifacts=[],
            last_turn_debug={},
            handlers=handlers,
        )

    assert result.handled is True
    run_sessions_list.assert_called_once()


def test_stats_command_uses_shared_stats_service(
    session_store: SQLiteSessionStore,
) -> None:
    session_id = session_store.create_session(
        initial_agent_id="agent.main",
        profile_version="v1",
    )
    session_store.append_event(
        session_id=session_id,
        event_type="turn.assistant",
        payload={"text": "done"},
    )
    session_store.append_event(
        session_id=session_id,
        event_type="llm.call.completed",
        payload={"usage": {"prompt_tokens": 8, "completion_tokens": 3}},
    )
    runtime_state = _runtime_state()
    runtime_state.inproc_runtime = SimpleNamespace(sessions=session_store)
    handlers = _handlers()

    with patch("builtins.print") as mocked_print:
        result = handle_chat_command(
            line="/stats",
            args=SimpleNamespace(),
            config=SimpleNamespace(),
            agent_id="agent",
            session_id=session_id,
            transport="in-process",
            mode="single-process",
            runtime_state=runtime_state,
            last_artifacts=[],
            last_turn_debug={},
            handlers=handlers,
        )

    assert result.handled is True
    rendered = "\n".join(str(call.args[0]) for call in mocked_print.call_args_list)
    assert f"session {session_id}" in rendered
    assert "turns 1" in rendered
    assert "totals tokens 8/3" in rendered
