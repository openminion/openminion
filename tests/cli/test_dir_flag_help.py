from __future__ import annotations

from openminion.cli.parser.base import build_parser


def _dir_help() -> str:
    for action in build_parser()._actions:
        if "--dir" in action.option_strings:
            return str(action.help or "")
    raise AssertionError("--dir action not found on the root parser")


def test_dir_help_covers_one_shot_runs_and_flag_position() -> None:
    help_text = _dir_help()

    assert "interactive session" in help_text
    assert "one-shot run" in help_text
    assert "before the command" in help_text


def test_dir_help_still_describes_a_working_directory() -> None:
    assert _dir_help().startswith("Working directory")


def test_run_still_rejects_subcommand_position_dir() -> None:
    parser = build_parser()
    try:
        parser.parse_args(["run", "--dir", ".", "hi"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - documents the unchanged ordering rule
        raise AssertionError("run --dir should stay rejected until that is decided")
