from __future__ import annotations

import io
import json
import shlex
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from openminion.cli.main import main
from openminion.modules.storage.runtime.sqlite import resolve_database_path
from openminion.modules.task import TaskManager, load_latest_project_checkpoint
from openminion.modules.task.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.tools.git import register


pytestmark = pytest.mark.e2e
_GIT = shutil.which("git")


def _run_cli(args: list[str]) -> dict[str, object]:
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(args) == 0
    return json.loads(output.getvalue())


def _git(*args: str, cwd: Path) -> str:
    assert _GIT is not None
    result = subprocess.run(
        [_GIT, *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_tool(registry: ToolRegistry, workspace: Path, name: str):
    result = execute_tool_spec_call(
        tool=registry.list()[name],
        arguments={"path": "."} if name == "git.status" else {},
        context=ToolExecutionContext(
            channel="console",
            target="oapc",
            session_id="oapc-session",
            metadata={"workspace_root": str(workspace)},
        ),
    )
    assert result.ok is True
    return result.data["parsed"]


@pytest.mark.skipif(_GIT is None, reason="git binary is required")
def test_project_recovers_verifies_and_proposes_delivery(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    workspace = tmp_path / "fixture"
    workspace.mkdir()
    totals = workspace / "totals.py"
    formatting = workspace / "formatting.py"
    tests = workspace / "test_report.py"
    totals.write_text("def total(values):\n    return 0\n", encoding="utf-8")
    formatting.write_text(
        "def format_total(value):\n    return str(value)\n",
        encoding="utf-8",
    )
    tests.write_text(
        "from formatting import format_total\n"
        "from totals import total\n\n"
        "def test_report():\n"
        "    assert format_total(total([2, 3])) == 'Total: 5'\n",
        encoding="utf-8",
    )
    _git("init", "-q", "-b", "main", cwd=workspace)
    _git("config", "user.email", "oapc@example.invalid", cwd=workspace)
    _git("config", "user.name", "OAPC Fixture", cwd=workspace)
    _git("config", "commit.gpgsign", "false", cwd=workspace)
    _git("add", ".", cwd=workspace)
    _git("commit", "-q", "-m", "seed fixture", cwd=workspace)

    prompts: list[str] = []

    def run_turn(*, config_path, payload):  # noqa: ANN001, ARG001
        prompts.append(str(payload["message"]))
        if len(prompts) == 1:
            totals.write_text(
                "def total(values):\n    return sum(values)\n",
                encoding="utf-8",
            )
            changed = totals.name
        else:
            formatting.write_text(
                "def format_total(value):\n    return f'Total: {value}'\n",
                encoding="utf-8",
            )
            changed = formatting.name
        return {
            "final_text": f"changed {changed}",
            "metadata": {
                "artifact_refs": [f"file:{changed}"],
                "evidence_kinds": ["artifact"],
                "effect_refs": [f"write:{changed}"],
            },
        }

    monkeypatch.setattr("openminion.cli.commands.autonomy.run_turn", run_turn)
    root_args = [
        "--home-root",
        str(tmp_path / "home"),
        "--data-root",
        str(tmp_path / "data"),
        "--no-interactive",
    ]
    verify = f"{shlex.quote(sys.executable)} -m pytest -q {tests.name}"
    first = _run_cli(
        [
            *root_args,
            "autonomy",
            "start",
            "--goal",
            "Repair and verify the report",
            "--workspace",
            str(workspace),
            "--verification-domain",
            "coding",
            "--max-iterations",
            "1",
            "--verify-command",
            verify,
            "--json",
        ]
    )["run"]
    assert first["status"] == "blocked"

    completed = _run_cli(
        [
            *root_args,
            "autonomy",
            "resume",
            str(first["run_id"]),
            "--max-iterations",
            "3",
            "--json",
        ]
    )["run"]
    assert completed["status"] == "completed"
    assert "Prior verifier refs:" in prompts[1]

    env = {
        "OPENMINION_HOME": str(tmp_path / "home"),
        "OPENMINION_DATA_ROOT": str(tmp_path / "data"),
    }
    manager = TaskManager.for_lifecycle_db(
        db_path=resolve_database_path(DEFAULT_INTEGRATED_SQLITE_SUBPATH, env=env)
    )
    checkpoint = load_latest_project_checkpoint(
        manager,
        task_id=str(completed["task_id"]),
    )
    assert checkpoint is not None
    assert checkpoint.project_run.session_id == first["session_id"]
    assert checkpoint.project_run.committed_cycle_count == 2
    assert len(checkpoint.project_run.effect_refs) == 2

    registry = ToolRegistry([])
    register(registry)
    status = _git_tool(registry, workspace, "git.status")
    diff = _git_tool(registry, workspace, "git.diff")
    changed_paths = {item["path"] for item in status["files"]}
    assert changed_paths == {"formatting.py", "totals.py"}
    assert "formatting.py" in diff["diff_text"]
    assert "totals.py" in diff["diff_text"]

    proposal = {
        "commit_message": "fix(report): calculate and format totals",
        "draft_pr": {
            "summary": sorted(changed_paths),
            "validation": [verify],
            "unverified": ["live provider proof"],
        },
    }
    assert proposal["draft_pr"]["summary"] == ["formatting.py", "totals.py"]
    assert _git("rev-list", "--count", "HEAD", cwd=workspace) == "1"
    assert _git("remote", cwd=workspace) == ""
