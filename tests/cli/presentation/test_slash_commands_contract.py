from __future__ import annotations

from openminion.cli.presentation.slash_commands import (
    SLASH_COMMANDS,
    rich_slash_command_registry,
    slash_command_runs_while_busy,
    terminal_slash_commands,
    unknown_slash_command_message,
)


def test_terminal_and_rich_share_core_slash_vocabulary() -> None:
    terminal = set(terminal_slash_commands())
    rich = {
        aliases[0] for aliases, _description, _handler in rich_slash_command_registry()
    }
    for command in (
        "/new",
        "/resume",
        "/sessions",
        "/context",
        "/memory",
        "/graph",
        "/tasks",
        "/skills",
        "/statusline",
        "/details",
        "/export",
        "/editor",
    ):
        assert command in terminal
        assert command in rich


def test_slash_metadata_has_unique_primary_names() -> None:
    names = [command.name for command in SLASH_COMMANDS]
    assert len(names) == len(set(names))


def test_rich_metadata_preserves_known_aliases() -> None:
    registry = rich_slash_command_registry()
    aliases_by_primary = {aliases[0]: aliases for aliases, _desc, _handler in registry}
    assert "/quit" in aliases_by_primary["/exit"]
    assert "/tool" in aliases_by_primary["/tools"]
    assert "/session" in aliases_by_primary["/sessions"]
    assert "/task" in aliases_by_primary["/tasks"]


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


def test_overview_is_registered_for_rich_only() -> None:
    terminal = set(terminal_slash_commands())
    rich = {
        aliases[0]: handler
        for aliases, _description, handler in rich_slash_command_registry()
    }

    assert "/overview" not in terminal
    assert rich["/overview"] == "_slash_overview"


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
