from __future__ import annotations

import io
import json
import shlex
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

from openminion.cli.main import main
from openminion.modules.storage.runtime.sqlite import resolve_database_path
from openminion.modules.task import TaskManager, load_latest_project_checkpoint
from openminion.modules.task.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH


def _root_args(tmp_path: Path) -> list[str]:
    return [
        "--home-root",
        str(tmp_path / "home"),
        "--data-root",
        str(tmp_path / "data"),
        "--no-interactive",
    ]


def _run_cli(args: list[str]) -> dict[str, object]:
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(args) == 0
    return json.loads(output.getvalue())


def test_coding_project_replans_repairs_and_resumes_from_committed_checkpoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "coding-project"
    workspace.mkdir()
    module = workspace / "totals.py"
    tests = workspace / "test_totals.py"
    module.write_text("def total(values):\n    return 0\n", encoding="utf-8")
    tests.write_text(
        "from totals import total\n\n"
        "def test_total():\n    assert total([2, 3]) == 5\n\n"
        "def test_empty():\n    assert total([]) == 0\n",
        encoding="utf-8",
    )
    turns = 0

    def run_turn(*, config_path, payload):  # noqa: ANN001, ARG001
        nonlocal turns
        turns += 1
        if turns == 1:
            module.write_text(
                "def total(values):\n    return values[0] if values else 0\n",
                encoding="utf-8",
            )
        else:
            module.write_text(
                "def total(values):\n    return sum(values)\n",
                encoding="utf-8",
            )
        return {
            "final_text": f"coding cycle {turns}",
            "metadata": {
                "artifact_refs": [f"file:{module.name}"],
                "evidence_kinds": ["artifact"],
                "effect_refs": [f"write:{module.name}:{turns}"],
            },
        }

    monkeypatch.setattr("openminion.cli.commands.autonomy.run_turn", run_turn)
    verify = f"{shlex.quote(sys.executable)} -m pytest -q {tests.name}"
    first = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "Repair the total implementation and prove both cases",
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
            *_root_args(tmp_path),
            "autonomy",
            "resume",
            str(first["run_id"]),
            "--max-iterations",
            "3",
            "--json",
        ]
    )["run"]

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
    proof = json.loads(
        Path(str(completed["proof_packet_ref"])).read_text(encoding="utf-8")
    )

    assert completed["status"] == "completed"
    assert (
        module.read_text(encoding="utf-8")
        == "def total(values):\n    return sum(values)\n"
    )
    assert checkpoint is not None
    assert checkpoint.project_run.committed_cycle_count == 2
    assert len(checkpoint.project_run.effect_refs) == 2
    assert proof["tests_run"][0]["status"] == "passed"
    assert turns == 2


def test_project_checkpoint_resumes_across_real_cli_processes(tmp_path: Path) -> None:
    root_args = _root_args(tmp_path)
    failed_verify = f"{shlex.quote(sys.executable)} -c 'raise SystemExit(1)'"
    passed_verify = f"{shlex.quote(sys.executable)} -c 'raise SystemExit(0)'"
    started = subprocess.run(
        [
            sys.executable,
            "-m",
            "openminion.cli.main",
            *root_args,
            "autonomy",
            "start",
            "--goal",
            "Prove restart recovery",
            "--workspace",
            str(tmp_path),
            "--max-iterations",
            "1",
            "--replay-response",
            "first process cycle",
            "--verify-command",
            failed_verify,
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    first = json.loads(started.stdout)["run"]
    assert first["status"] == "blocked"

    resumed = subprocess.run(
        [
            sys.executable,
            "-m",
            "openminion.cli.main",
            *root_args,
            "autonomy",
            "resume",
            first["run_id"],
            "--max-iterations",
            "2",
            "--replay-response",
            "second process cycle",
            "--verify-command",
            passed_verify,
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    completed = json.loads(resumed.stdout)["run"]

    assert completed["status"] == "completed"
    assert completed["checkpoint_id"].endswith(":cycle:2")
