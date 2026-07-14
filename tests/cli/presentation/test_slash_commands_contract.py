from __future__ import annotations

from openminion.cli.presentation.slash_commands import (
    SLASH_COMMANDS,
    rich_slash_command_registry,
    terminal_slash_commands,
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
        "/tasks",
        "/skills",
        "/statusline",
        "/details",
        "/export",
        "/editor",
        "/dashboard",
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
