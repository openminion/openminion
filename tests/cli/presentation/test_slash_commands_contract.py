from __future__ import annotations

from openminion.cli.presentation.slash_commands import (
    SLASH_COMMANDS,
    canonical_slash_command,
    canonical_slash_command_name,
    format_slash_command_help,
    format_slash_help,
    parse_slash_help_target,
    slash_command_metadata,
    slash_command_runs_while_busy,
    terminal_slash_commands,
    unknown_slash_command_message,
)


def test_slash_metadata_has_unique_primary_names() -> None:
    names = [command.name for command in SLASH_COMMANDS]
    assert len(names) == len(set(names))


def test_every_primary_command_has_explicit_usage() -> None:
    assert len(SLASH_COMMANDS) == 51
    assert all(command.usage for command in SLASH_COMMANDS)
    assert all(
        all(line.strip() for line in command.usage) for command in SLASH_COMMANDS
    )


def test_agents_metadata_describes_list_and_filter_behavior() -> None:
    command = slash_command_metadata("agent")

    assert command is not None
    assert command.name == "/agents"
    assert (
        command.description == "List configured agents or filter by exact ID or label"
    )
    assert command.usage == ("/agents", "/agents <agent-id-or-label>")
    assert command.aliases == ("/agent",)
    assert "--profile" in command.note
    assert "--agent" in command.note


def test_stateful_command_summaries_match_bare_command_effects() -> None:
    summaries = {
        command.name: command.description
        for command in SLASH_COMMANDS
        if command.name in {"/sessions", "/graph", "/details", "/readonly"}
    }

    assert summaries == {
        "/sessions": "List sessions",
        "/graph": "Show commands to query, refresh, or view configured graphs",
        "/details": "Toggle or set tool-block detail",
        "/readonly": "Toggle or set read-only mode",
    }


def test_new_metadata_preserves_multi_token_alternate_form() -> None:
    command = slash_command_metadata("new")

    assert command is not None
    assert command.aliases == ("/new session",)
    assert "/new session" in command.usage
    assert "/new session" in format_slash_help("new")
    assert format_slash_help("new session").startswith("/help —")


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


def test_help_shaped_commands_are_busy_safe_without_relaxing_normal_commands() -> None:
    for command in (
        "/exit --help",
        "/clear ?",
        "/review --help",
        "/unknown ?",
        "/help agents",
    ):
        assert slash_command_runs_while_busy(command)

    assert not slash_command_runs_while_busy("/clear now")
    assert not slash_command_runs_while_busy("/review current")


def test_help_target_parser_reserves_only_exact_help_operands() -> None:
    assert parse_slash_help_target("") is None
    assert not slash_command_runs_while_busy("")
    assert parse_slash_help_target("/") == ""
    assert parse_slash_help_target("/help") == ""
    assert parse_slash_help_target("/help /agents") == "/agents"
    assert parse_slash_help_target("/help --help") == "/help"
    assert parse_slash_help_target("/help ?") == "/help"
    assert parse_slash_help_target("/agents --help") == "/agents"
    assert parse_slash_help_target("/agent ?") == "/agent"
    assert parse_slash_help_target("/help new session") == "new session"
    assert parse_slash_help_target("/graph query source ?") is None
    assert parse_slash_help_target("/agents ? extra") is None


def test_contextual_help_uses_canonical_heading_usage_alias_and_note() -> None:
    command = slash_command_metadata("/agent")
    assert command is not None

    output = format_slash_command_help(command)

    assert output.startswith(
        "/agents — List configured agents or filter by exact ID or label"
    )
    assert "Usage:\n  /agents\n  /agents <agent-id-or-label>" in output
    assert "Alias: /agent" in output
    assert command.note in output


def test_help_wraps_with_hanging_indentation_at_eighty_columns() -> None:
    global_help = format_slash_help(width=80)
    contextual_help = format_slash_help("context-review", width=80)
    wide_help = format_slash_help("context-review", width=120)

    assert max(map(len, global_help.splitlines())) <= 80
    assert max(map(len, contextual_help.splitlines())) <= 80
    assert "\n               /agent)" in global_help
    assert "\n    [artifacts=<dir>]" in contextual_help
    assert "[calibration=<path>] [artifacts=<dir>]" in wide_help


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
