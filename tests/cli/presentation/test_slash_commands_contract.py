from __future__ import annotations

from openminion.cli.presentation.slash_commands import (
    SLASH_COMMANDS,
    canonical_slash_command,
    canonical_slash_command_name,
    slash_command_runs_while_busy,
    terminal_slash_commands,
    unknown_slash_command_message,
)


def test_slash_metadata_has_unique_primary_names() -> None:
    names = [command.name for command in SLASH_COMMANDS]
    assert len(names) == len(set(names))


def test_terminal_catalog_preserves_supported_commands_and_aliases() -> None:
    commands = set(terminal_slash_commands())

    expected = {
        name for command in SLASH_COMMANDS for name in (command.name, *command.aliases)
    }
    assert commands == expected

    assert "/animation" not in commands
    assert "/debug" not in commands


def test_advertised_aliases_resolve_to_primary_commands() -> None:
    for command in SLASH_COMMANDS:
        for alias in command.aliases:
            assert canonical_slash_command_name(alias) == command.name

    assert canonical_slash_command("/tool file.read") == "/tools file.read"


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
    assert not slash_command_runs_while_busy("/tasks pause task-1")


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
