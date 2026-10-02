from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.cli.interactive.terminal.composer import TerminalComposer
from openminion.cli.interactive.terminal.shell.session_paths import focus_history_path


def _runtime(data_root: Path) -> SimpleNamespace:
    return SimpleNamespace(api_runtime=SimpleNamespace(data_root=data_root))


def test_focus_history_path_uses_canonical_cli_location(tmp_path: Path) -> None:
    assert focus_history_path(_runtime(tmp_path)) == str(
        tmp_path.resolve() / "cli" / "terminal_history"
    )


def test_focus_history_path_recovers_when_data_root_is_a_file(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_root = tmp_path / "not-a-directory"
    data_root.write_text("occupied", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        history_path = focus_history_path(_runtime(data_root))

    assert history_path is None
    assert "NotADirectoryError" in caplog.text
    assert str(data_root) not in caplog.text
    assert TerminalComposer(history_file=history_path) is not None


@pytest.mark.parametrize("failure", (OSError("denied"), RuntimeError("loop")))
def test_focus_history_path_recovers_without_logging_root(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    def _fail_resolve(_path: Path, *, strict: bool = False) -> Path:
        del strict
        raise failure

    monkeypatch.setattr(Path, "resolve", _fail_resolve)

    with caplog.at_level(logging.WARNING):
        history_path = focus_history_path(_runtime(tmp_path))

    assert history_path is None
    assert type(failure).__name__ in caplog.text
    assert str(tmp_path) not in caplog.text
