from __future__ import annotations

import json

from tests.e2e.runners import run_cli_focus_e2e

import pytest

pytestmark = pytest.mark.e2e


def test_focus_runner_writes_summary_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    summary_path = tmp_path / "focus-summary.json"

    def fake_run(paths, *, env, extra_args=()):
        assert paths == ("tests/e2e/cli/focus/test_local.py",)
        assert extra_args == ()
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        return 0

    monkeypatch.setenv(
        "OPENMINION_CLI_FOCUS_E2E_SUMMARY_OUTPUT",
        str(summary_path),
    )
    monkeypatch.setattr(run_cli_focus_e2e, "_run", fake_run)

    assert run_cli_focus_e2e.main(["local"]) == 0

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["mode"] == "local"
    assert summary["paths"] == ["tests/e2e/cli/focus/test_local.py"]
    assert summary["exit_code"] == 0
    assert summary["live"] is False
    assert summary["complex"] is False
    assert summary["elapsed_seconds"] >= 0
