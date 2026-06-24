from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout

import pytest

from .cli_entrypoint_paths import openminion_examples_modules_root


sys.path.insert(0, str(openminion_examples_modules_root()))

from sample.cli import main


def _run(args: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            code = main(args)
        except SystemExit as exc:  # pragma: no cover - argparse help path
            code = int(exc.code) if exc.code is not None else 0
    return code, buf.getvalue()


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


def test_no_command_prints_help_and_returns_zero() -> None:
    code, out = _run([])
    assert code == 0
    assert "Sample module CLI" in out


def test_list_command_returns_deterministic_json() -> None:
    code, out = _run(["sample", "list"])
    assert code == 0
    payload = json.loads(out)
    assert payload["count"] == len(payload["providers"])


def test_unknown_provider_returns_error_exit_code() -> None:
    code, out = _run(["sample", "test", "--provider", "unknown", "--input", "ping"])
    assert code == 1
    payload = json.loads(out)
    assert payload["success"] is False
    assert payload["error"]
