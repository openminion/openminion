from __future__ import annotations

import pytest

from openminion.cli.parser.base import build_parser
from openminion.cli.main import main


@pytest.mark.parametrize(
    ("argv", "expected_agent_attr"),
    [
        (
            [
                "doctor",
                "--profile",
                "planner-safe",
                "--override-provider",
                "anthropic",
                "--override-model",
                "claude-3-5-haiku-latest",
                "--override-system-prompt",
                "Stay concise.",
            ],
            "agent_id",
        ),
        (
            [
                "agent",
                "--profile",
                "planner-safe",
                "--override-provider",
                "anthropic",
                "--override-model",
                "claude-3-5-haiku-latest",
                "--override-system-prompt",
                "Stay concise.",
                "--message",
                "status",
            ],
            "agent_id",
        ),
        (
            [
                "gateway",
                "run",
                "--profile",
                "planner-safe",
                "--override-provider",
                "anthropic",
                "--override-model",
                "claude-3-5-haiku-latest",
                "--override-system-prompt",
                "Stay concise.",
            ],
            "agent_id",
        ),
        (
            [
                "agent-check",
                "--profile",
                "planner-safe",
                "--override-provider",
                "anthropic",
                "--override-model",
                "claude-3-5-haiku-latest",
                "--override-system-prompt",
                "Stay concise.",
            ],
            "agent_id",
        ),
    ],
)
def test_profile_selector_alias_and_runtime_override_flags_parse(
    argv: list[str],
    expected_agent_attr: str,
) -> None:
    args = build_parser().parse_args(argv)

    assert getattr(args, expected_agent_attr) == "planner-safe"
    assert args.override_provider == "anthropic"
    assert args.override_model == "claude-3-5-haiku-latest"
    assert args.override_system_prompt == "Stay concise."


def test_repeated_add_dir_parses_for_bare_interactive(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    args = build_parser().parse_args(
        ["--add-dir", str(first), "--add-dir", str(second)]
    )

    assert args.add_dir == [str(first), str(second)]


def test_add_dir_is_rejected_with_named_subcommand(tmp_path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--add-dir", str(tmp_path), "version"])

    assert exc_info.value.code == 2
