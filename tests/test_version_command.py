from __future__ import annotations

import json
from argparse import Namespace

import pytest

from openminion import __version__
from openminion.cli.commands.version import run_version
from openminion.cli.main import main


def test_run_version_plain_output(capsys) -> None:
    code = run_version(Namespace(json=False))

    assert code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_run_version_json_output(capsys) -> None:
    code = run_version(Namespace(json=True))

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "package": "openminion",
        "version": __version__,
    }


def test_cli_main_version_plain(capsys) -> None:
    code = main(["version"])

    assert code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_cli_root_version_plain(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_root_value_option_before_lazy_version_subcommand(capsys, tmp_path) -> None:
    code = main(["--dir", str(tmp_path), "version"])

    assert code == 0
    assert capsys.readouterr().out.strip() == __version__
