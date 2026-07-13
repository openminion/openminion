from __future__ import annotations

import pytest

from openminion.cli.commands.tui import (
    _DASHBOARD_NOTICE,
    _TUI_NOTICE,
)
from openminion.cli.ux.deprecation import print_deprecation_notice


@pytest.mark.parametrize(
    ("text", "env_name"),
    [
        (_DASHBOARD_NOTICE, "OPENMINION_DASHBOARD_NO_DEPRECATION"),
        (_TUI_NOTICE, "OPENMINION_TUI_NO_DEPRECATION"),
    ],
)
def test_notice_is_visible_and_suppressible(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    text: str,
    env_name: str,
) -> None:
    monkeypatch.delenv(env_name, raising=False)
    assert print_deprecation_notice(text, suppression_env=env_name)
    assert text in capsys.readouterr().err

    monkeypatch.setenv(env_name, "1")
    assert not print_deprecation_notice(text, suppression_env=env_name)
    assert capsys.readouterr().err == ""
