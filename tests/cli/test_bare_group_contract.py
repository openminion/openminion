from __future__ import annotations

import pytest

from openminion.cli.parser.base import build_parser


def _parse(argv: list[str]):
    return build_parser().parse_args(argv)


@pytest.mark.parametrize("group", ["status", "service"])
def test_bare_group_requires_a_subcommand(group: str, capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        _parse([group])

    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert f"{group}_command" in (captured.err + captured.out)
    assert "required" in (captured.err + captured.out).lower()


@pytest.mark.parametrize(
    "argv",
    [
        ["status", "readiness"],
        ["status", "runtime"],
        ["service", "status"],
        ["service", "list"],
    ],
)
def test_valid_subcommands_still_parse(argv: list[str]) -> None:
    args = _parse(argv)

    assert args.command == argv[0]
    assert getattr(args, "handler", None) is not None


@pytest.mark.parametrize("group", ["status", "service"])
def test_bare_group_never_loads_config_or_onboarding(group: str, monkeypatch) -> None:
    from openminion.cli import config as cli_config

    def explode(*_args, **_kwargs):
        raise AssertionError("config must not be loaded for a bare group")

    monkeypatch.setattr(cli_config, "load_cli_config_from_args", explode)
    monkeypatch.setattr(cli_config, "resolve_cli_roots", explode)

    with pytest.raises(SystemExit) as excinfo:
        _parse([group])

    assert excinfo.value.code == 2


def test_bare_interactive_launch_is_unchanged() -> None:
    args = _parse([])

    assert getattr(args, "command", None) is None
    assert getattr(args, "handler", None) is None


def test_other_groups_keep_their_own_contract() -> None:
    args = _parse(["memory", "blocks", "list"])

    assert args.command == "memory"
    assert args.memory_command == "blocks"
    assert args.blocks_command == "list"
