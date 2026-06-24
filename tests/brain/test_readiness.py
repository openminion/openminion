from __future__ import annotations

from openminion.modules.brain.schemas import (
    AgentCommand,
    AskUserCommand,
    ToolCommand,
)
from openminion.modules.brain.schemas.readiness import (
    payload_is_contextually_empty,
    validate_command_readiness,
)


def test_validate_command_readiness_flags_unknown_tool_arg() -> None:
    command = ToolCommand(
        title="Check weather",
        tool_name="weather.current",
        args={"location": "<UNKNOWN>"},
    )

    issue = validate_command_readiness(command, prefix="command")

    assert issue is not None
    assert issue.code == "decision_readiness_unresolved_sentinel"
    assert issue.field_path == "command.args.location"
    assert issue.placeholder_pattern == "<UNKNOWN>"


def test_validate_command_readiness_flags_template_tool_arg() -> None:
    command = ToolCommand(
        title="Check weather",
        tool_name="weather.current",
        args={"location": "{{city}}"},
    )

    issue = validate_command_readiness(command, prefix="command")

    assert issue is not None
    assert issue.code == "decision_readiness_unresolved_template"
    assert issue.field_path == "command.args.location"
    assert issue.placeholder_pattern == "{{city}}"


def test_validate_command_readiness_flags_nested_tool_placeholder() -> None:
    command = ToolCommand(
        title="Fetch config",
        tool_name="config.fetch",
        args={"config": {"host": "<UNKNOWN>"}},
    )

    issue = validate_command_readiness(command, prefix="command")

    assert issue is not None
    assert issue.field_path == "command.args.config.host"


def test_validate_command_readiness_flags_unknown_agent_param() -> None:
    command = AgentCommand(
        title="Delegate search",
        target_agent_id="search-agent",
        method="search",
        params={"query": "<UNKNOWN>"},
    )

    issue = validate_command_readiness(command, prefix="command")

    assert issue is not None
    assert issue.code == "decision_readiness_unresolved_sentinel"
    assert issue.field_path == "command.params.query"


def test_validate_command_readiness_accepts_valid_tool_and_agent_payloads() -> None:
    tool_command = ToolCommand(
        title="Check weather",
        tool_name="weather.current",
        args={"location": "Beijing"},
    )
    agent_command = AgentCommand(
        title="Delegate search",
        target_agent_id="search-agent",
        method="search",
        params={"query": "valid text"},
    )

    assert validate_command_readiness(tool_command, prefix="command") is None
    assert validate_command_readiness(agent_command, prefix="command") is None


def test_validate_command_readiness_skips_non_executable_payload_commands() -> None:
    command = AskUserCommand(
        title="Clarify",
        question="Which city?",
    )

    assert validate_command_readiness(command, prefix="command") is None


def test_validate_command_readiness_allows_content_blob_placeholder_tokens() -> None:
    command = ToolCommand(
        title="Write test fixture",
        tool_name="file.write",
        args={
            "path": "/tmp/test_report.py",
            "content": "assert '[HIGH]' in body\nvalue = '{{ owner }}'\n",
        },
    )

    assert validate_command_readiness(command, prefix="command") is None


def test_payload_is_contextually_empty_recurses_leaf_string_values() -> None:
    assert payload_is_contextually_empty({}) is True
    assert payload_is_contextually_empty({"location": ""}) is True
    assert payload_is_contextually_empty({"wrapper": {"location": "   "}}) is True


def test_payload_is_contextually_empty_allows_non_string_or_non_empty_leaves() -> None:
    assert payload_is_contextually_empty({"location": "Beijing"}) is False
    assert payload_is_contextually_empty({"count": 1}) is False
