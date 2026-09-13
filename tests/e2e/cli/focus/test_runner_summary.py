from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from tests.e2e.runners import run_cli_focus_e2e

import pytest

pytestmark = pytest.mark.e2e


def test_focus_runner_writes_summary_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    summary_path = tmp_path / "focus-summary.json"

    def fake_run(paths, *, env, extra_args=(), timeout_seconds=None):
        assert paths == ("tests/e2e/cli/focus/test_local.py",)
        assert extra_args == ()
        assert timeout_seconds is None
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


def test_live_minimax_runner_forwards_official_focus_profile(tmp_path: Path) -> None:
    home = tmp_path / "workspace"
    config_path = home / "test-configs" / "per-agent-minimax-official.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}\n", encoding="utf-8")
    capture_path = tmp_path / "captured.json"
    fake_python = tmp_path / "python3.11"
    fake_python.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "from pathlib import Path\n"
        "Path(os.environ['OPENMINION_TEST_CAPTURE']).write_text(json.dumps({\n"
        "    'config': os.environ.get('OPENMINION_CLI_FOCUS_E2E_CONFIG'),\n"
        "    'agent': os.environ.get('OPENMINION_CLI_FOCUS_E2E_AGENT'),\n"
        "}))\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    runner = (
        Path(__file__).resolve().parents[2]
        / "runners"
        / "run_live_minimax_regression_matrix.sh"
    )
    env = os.environ.copy()
    env.update(
        {
            "OPENMINION_HOME": str(home),
            "OPENMINION_PYTHON": str(fake_python),
            "OPENMINION_TEST_CAPTURE": str(capture_path),
        }
    )
    env.pop("OPENMINION_CLI_FOCUS_E2E_CONFIG", None)
    env.pop("OPENMINION_CLI_FOCUS_E2E_AGENT", None)

    result = subprocess.run(
        [str(runner), "gate"],
        cwd=runner.parents[3],
        env=env,
        check=False,
    )

    assert result.returncode == 0
    captured = json.loads(capture_path.read_text(encoding="utf-8"))
    assert captured == {
        "config": str(config_path),
        "agent": "minimax-m2-7",
    }
