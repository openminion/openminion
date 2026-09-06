from __future__ import annotations

from openminion.cli.presentation.slash_commands import (
    SLASH_COMMANDS,
    slash_command_runs_while_busy,
    terminal_slash_commands,
    unknown_slash_command_message,
)


def test_slash_metadata_has_unique_primary_names() -> None:
    names = [command.name for command in SLASH_COMMANDS]
    assert len(names) == len(set(names))


def test_terminal_catalog_preserves_supported_commands_and_aliases() -> None:
    commands = set(terminal_slash_commands())

    for command in (
        "/new",
        "/resume",
        "/sessions",
        "/context-review",
        "/overview",
        "/copy",
        "/memory",
        "/graph",
        "/tasks",
        "/skills",
        "/statusline",
        "/details",
        "/export",
        "/editor",
        "/quit",
    ):
        assert command in commands

    assert "/animation" not in commands
    assert "/debug" not in commands


def test_busy_slash_policy_allows_reads_and_blocks_changes() -> None:
    for command in (
        "/status",
        "/overview",
        "/memory",
        "/graph",
        "/skills",
        "/tasks task-1",
        "/model",
    ):
        assert slash_command_runs_while_busy(command)
    for command in ("/new", "/undo", "/model openai/gpt-5", "/permissions bypass"):
        assert not slash_command_runs_while_busy(command)


def test_unknown_slash_command_message_suggests_nearest_command() -> None:
    assert unknown_slash_command_message(
        "/skill", available_commands=("/skills", "/status")
    ) == (
        "Unknown command: /skill\n"
        "Did you mean /skills?\n"
        "Type / to view available commands."
    )


def test_unknown_slash_command_message_omits_weak_suggestion() -> None:
    assert (
        unknown_slash_command_message(
            "/xyzzy", available_commands=("/skills", "/status")
        )
        == "Unknown command: /xyzzy\nType / to view available commands."
    )
