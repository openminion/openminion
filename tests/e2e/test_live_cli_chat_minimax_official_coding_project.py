from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest

from tests.helpers.live_cli_chat_alibaba import (
    CLISessionResult,
    artifact_dir,
    extract_assistant_messages,
    extract_debug_payloads,
    framework_root,
    parse_tool_results,
    require_live_flag,
    run_cli_session,
)
from tests.helpers.live_e2e_profiles import resolve_live_config_path

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(1200)]


_OFFICIAL_CONFIG = resolve_live_config_path(
    "per-agent-minimax-official.json",
    framework_root(),
)
_AGENT_ID = "minimax-m2-7"
_TODAY = date.today().isoformat()
_INTERNAL_TRACE_TOOL_NAMES = frozenset({"submit_output"})


def _fresh_workspace(name: str) -> Path:
    workspace = artifact_dir() / "workspaces" / name
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _python_env(*, workspace: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(workspace)
    return env


def _run_local_pytest(workspace: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests"],
        cwd=workspace,
        env=_python_env(workspace=workspace),
        text=True,
        capture_output=True,
        check=False,
    )


def _run_module_cli(
    workspace: Path, *, input_name: str, output_name: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "task_summary.report",
            input_name,
            output_name,
        ],
        cwd=workspace,
        env=_python_env(workspace=workspace),
        text=True,
        capture_output=True,
        check=False,
    )


def _repair_project_after_pytest_failure(
    *,
    run_id: str,
    workspace: Path,
    pytest_result: subprocess.CompletedProcess[str],
) -> CLISessionResult:
    prompt = (
        f"Work only inside this directory: {workspace}. External verification of "
        "the project you generated failed. Fix the existing project files so "
        "`python -m pytest -q tests` passes. Do not touch files outside the "
        "directory. Do not use pip or install anything. Use file.read/file.write "
        "for edits and reserve exec.run only for the exact verification command "
        "`python -m pytest -q tests`. Rerun pytest after any edit before the "
        "final answer. Here is the exact pytest output:\n\n"
        f"STDOUT:\n{pytest_result.stdout[-4000:]}\n\n"
        f"STDERR:\n{pytest_result.stderr[-2000:]}\n\n"
        "Final answer must include the final pytest result."
    )
    return run_cli_session(
        session_id_prefix=f"{run_id}-repair",
        user_input=f"{prompt}\n/debug\n/exit\n",
        agent_id=_AGENT_ID,
        config_path=_OFFICIAL_CONFIG,
        data_root_override=artifact_dir() / "data-roots" / f"{run_id}-repair",
        workspace_root_override=workspace,
        matrix_type="coding_project",
        auto_confirm=True,
    )


def _assistant_body(result_transcript: str, *, session_id: str, agent_id: str) -> str:
    messages = extract_assistant_messages(
        transcript=result_transcript,
        session_id=session_id,
        agent_id=agent_id,
        include_policy_confirmation_prompt=False,
    )
    assert messages, "expected at least one assistant message in the live transcript"
    return messages[-1]


def _assert_tool_backing(result_transcript: str, *, transcript_path: Path) -> None:
    payload = extract_debug_payloads(result_transcript, which="last")
    assert isinstance(payload, dict), f"missing last debug payload\n{transcript_path}"
    last_turn = payload.get("last_turn")
    assert isinstance(last_turn, dict), f"missing last_turn payload\n{transcript_path}"
    metadata = last_turn.get("metadata")
    assert isinstance(metadata, dict), f"missing metadata payload\n{transcript_path}"
    tool_execution_count = int(
        str(
            metadata.get(
                "tool_execution_count_cumulative",
                metadata.get("tool_execution_count", "0"),
            )
        ).strip()
        or "0"
    )
    tool_results = parse_tool_results(
        metadata.get("tool_calls_cumulative", metadata.get("tool_results"))
    )
    trace_tool_names = _trace_user_tool_names(transcript_path)
    assert (
        tool_execution_count >= 2
        or len(tool_results) >= 2
        or len(trace_tool_names) >= 2
    ), (
        "expected at least two tool-backed steps for the scratch-project lane\n"
        f"metadata={json.dumps(metadata, indent=2, sort_keys=True)}\n"
        f"trace_tool_names={trace_tool_names}\n"
        f"transcript={transcript_path}"
    )


def _trace_user_tool_names(transcript_path: Path) -> list[str]:
    trace_root = artifact_dir() / "traces" / transcript_path.stem
    if not trace_root.exists():
        return []
    tool_names: list[str] = []
    for trace_file in sorted(trace_root.rglob("*-structured.json")):
        try:
            payload = json.loads(trace_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        response = payload.get("response")
        if not isinstance(response, dict):
            continue
        calls = response.get("tool_calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict):
                continue
            function_payload = call.get("function")
            function_name = (
                function_payload.get("name")
                if isinstance(function_payload, dict)
                else ""
            )
            name = str(
                call.get("tool_name") or call.get("name") or function_name or ""
            ).strip()
            if name and name not in _INTERNAL_TRACE_TOOL_NAMES:
                tool_names.append(name)
    return tool_names


def _seed_research_project(workspace: Path) -> None:
    (workspace / "task_summary").mkdir(parents=True, exist_ok=True)
    (workspace / "tests").mkdir(parents=True, exist_ok=True)
    (workspace / "sample_tasks.csv").write_text(
        (
            "task,owner,priority,status,due_date\n"
            "Refresh docs,alice,1,open,2026-05-20\n"
            "Wire CLI,bob,2,open,2026-05-25\n"
            "Close tracker,alice,3,done,2026-05-18\n"
        ),
        encoding="utf-8",
    )
    (workspace / "pyproject.toml").write_text(
        "\n".join(
            [
                "[build-system]",
                'requires = ["setuptools>=68"]',
                'build-backend = "setuptools.build_meta"',
                "",
                "[project]",
                'name = "task-summary-scratch"',
                'version = "0.0.1"',
                'description = "Scratch project for live research-to-code validation"',
                'requires-python = ">=3.11"',
                "",
                "[tool.pytest.ini_options]",
                'testpaths = ["tests"]',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (workspace / "README.md").write_text(
        "# Task Summary Scratch\n\nRun tests with `python -m pytest -q tests`.\n",
        encoding="utf-8",
    )
    (workspace / "task_summary" / "__init__.py").write_text(
        '"""Scratch task summary package."""\n',
        encoding="utf-8",
    )
    (workspace / "task_summary" / "report.py").write_text(
        "\n".join(
            [
                '"""Generate a minimal Markdown task summary."""',
                "",
                "from __future__ import annotations",
                "",
                "import csv",
                "import sys",
                "from collections import Counter",
                "from datetime import date",
                "from pathlib import Path",
                "",
                "",
                "def build_summary(input_csv: Path, output_md: Path) -> None:",
                "    rows = list(csv.DictReader(input_csv.read_text(encoding='utf-8').splitlines()))",
                "    owner_counts = Counter(row['owner'] for row in rows)",
                "    overdue = sum(",
                "        1",
                "        for row in rows",
                "        if row['status'] != 'done' and row['due_date'] < date.today().isoformat()",
                "    )",
                "    highest_open = sorted(",
                "        (row for row in rows if row['status'] != 'done'),",
                "        key=lambda row: (int(row['priority']), row['task']),",
                "    )[:3]",
                "    lines = ['# Task Summary', '', '## TOTALS BY OWNER']",
                "    lines.extend(f'- {owner}: {count}' for owner, count in sorted(owner_counts.items()))",
                "    lines.extend(['', '## OVERDUE COUNT', f'- overdue open tasks: {overdue}', '', '## HIGHEST PRIORITY OPEN ITEMS'])",
                "    lines.extend(",
                '        f\'- P{row["priority"]} {row["task"]} ({row["owner"]})\'',
                "        for row in highest_open",
                "    )",
                "    output_md.write_text('\\n'.join(lines) + '\\n', encoding='utf-8')",
                "",
                "",
                "def cli(argv: list[str] | None = None) -> int:",
                "    args = list(sys.argv[1:] if argv is None else argv)",
                "    if len(args) != 2:",
                "        print('usage: python -m task_summary.report <input_csv> <output_md>')",
                "        return 2",
                "    build_summary(Path(args[0]), Path(args[1]))",
                "    return 0",
                "",
                "",
                "if __name__ == '__main__':",
                "    raise SystemExit(cli())",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "test_report.py").write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "",
                "from task_summary.report import build_summary",
                "",
                "",
                "def test_build_summary_writes_expected_sections(tmp_path: Path) -> None:",
                "    input_csv = tmp_path / 'tasks.csv'",
                "    input_csv.write_text(",
                "        'task,owner,priority,status,due_date\\n'",
                "        'A,alice,1,open,2026-05-20\\n'",
                "        'B,bob,2,open,2026-05-25\\n',",
                "        encoding='utf-8',",
                "    )",
                "    output_md = tmp_path / 'report.md'",
                "    build_summary(input_csv, output_md)",
                "    body = output_md.read_text(encoding='utf-8')",
                "    assert 'TOTALS BY OWNER' in body",
                "    assert 'OVERDUE COUNT' in body",
                "    assert 'HIGHEST PRIORITY OPEN ITEMS' in body",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.e2e
def test_live_minimax_m2_7_coding_builds_scratch_project() -> None:
    require_live_flag()
    if not _OFFICIAL_CONFIG.exists():
        pytest.skip(f"missing config file: {_OFFICIAL_CONFIG}")

    run_id = f"coding-project-{int(time.time())}"
    workspace = _fresh_workspace(run_id)
    prompt = (
        f"Work only inside this directory: {workspace}. Create a tiny Python "
        "project with this exact structure: pyproject.toml, README.md, "
        "sample_tasks.csv, task_summary/__init__.py, task_summary/report.py, "
        "and tests/test_report.py. Use only the Python standard library. "
        "task_summary.report must expose "
        "build_summary(input_csv: Path, output_md: Path) and "
        "cli(argv: list[str] | None = None) -> int. "
        "`python -m task_summary.report sample_tasks.csv report.md` must work. "
        "To make module execution work, include exactly the standard guard "
        "`if __name__ == '__main__': raise SystemExit(cli())` in "
        "task_summary/report.py. "
        "The cli(argv) function must also work when called directly as "
        "`cli([str(input_csv), str(output_md)])`; when argv is None, read "
        "command-line paths from sys.argv[1:]. "
        "Use file writes with automatic parent-directory creation for project "
        "scaffolding; do not use shell mkdir or other exec commands just to "
        "create directories. For workspace inspection, use file.list_dir and "
        "file.read instead of exec.run with ls, cat, find, pwd, pipes, or "
        "redirections. Reserve exec.run only for the exact verification command "
        "`python -m pytest -q tests`; do not add pipes, redirections, head, or "
        "shell chaining. Do not run pip or install the project; pytest runs "
        "against the workspace files directly. "
        "The Markdown output must contain sections titled TOTALS BY OWNER, "
        "OVERDUE COUNT, and HIGHEST PRIORITY OPEN ITEMS. In TOTALS BY OWNER, "
        "owner total lines must use plain text exactly like `- alice: 2` with "
        "no Markdown bold and no trailing word such as `tasks`. Add tests and run "
        "pytest until it passes. If pytest fails and you edit code or tests, "
        "rerun the exact command `python -m pytest -q tests` before the final "
        "answer; do not finalize based on a stale failing pytest result. "
        "Do not use the plan tool or decompose; do the "
        "work directly in this turn. Keep the project minimal but production-like. "
        "Do not touch files outside the given directory. In your final answer, "
        "list the exact relative paths pyproject.toml, README.md, "
        "sample_tasks.csv, task_summary/__init__.py, task_summary/report.py, "
        "and tests/test_report.py, plus the final pytest result."
    )

    result = run_cli_session(
        session_id_prefix=run_id,
        user_input=f"{prompt}\n/debug\n/exit\n",
        agent_id=_AGENT_ID,
        config_path=_OFFICIAL_CONFIG,
        data_root_override=artifact_dir() / "data-roots" / run_id,
        workspace_root_override=workspace,
        matrix_type="coding_project",
        auto_confirm=True,
    )

    _assert_tool_backing(result.transcript, transcript_path=result.transcript_path)
    assistant_body = _assistant_body(
        result.transcript,
        session_id=result.session_id,
        agent_id=_AGENT_ID,
    )
    assert assistant_body.strip(), (
        "expected non-empty final answer for generated project\n"
        f"transcript={result.transcript_path}"
    )

    expected_files = (
        workspace / "pyproject.toml",
        workspace / "README.md",
        workspace / "sample_tasks.csv",
        workspace / "task_summary" / "__init__.py",
        workspace / "task_summary" / "report.py",
        workspace / "tests" / "test_report.py",
    )
    missing = [
        str(path.relative_to(workspace)) for path in expected_files if not path.exists()
    ]
    assert not missing, (
        f"scratch coding lane did not create expected files: {missing}\n"
        f"workspace={workspace}\ntranscript={result.transcript_path}"
    )

    pytest_result = _run_local_pytest(workspace)
    repair_transcript: Path | None = None
    if pytest_result.returncode != 0:
        repair_result = _repair_project_after_pytest_failure(
            run_id=run_id,
            workspace=workspace,
            pytest_result=pytest_result,
        )
        repair_transcript = repair_result.transcript_path
        _assert_tool_backing(
            repair_result.transcript, transcript_path=repair_result.transcript_path
        )
        pytest_result = _run_local_pytest(workspace)
    assert pytest_result.returncode == 0, (
        f"local pytest verification failed\nworkspace={workspace}\n"
        f"stdout={pytest_result.stdout}\nstderr={pytest_result.stderr}\n"
        f"transcript={result.transcript_path}\nrepair_transcript={repair_transcript}"
    )

    cli_result = _run_module_cli(
        workspace,
        input_name="sample_tasks.csv",
        output_name="generated_report.md",
    )
    assert cli_result.returncode == 0, (
        f"generated project CLI failed\nworkspace={workspace}\n"
        f"stdout={cli_result.stdout}\nstderr={cli_result.stderr}\n"
        f"transcript={result.transcript_path}"
    )
    generated_report = (workspace / "generated_report.md").read_text(encoding="utf-8")
    for heading in (
        "TOTALS BY OWNER",
        "OVERDUE COUNT",
        "HIGHEST PRIORITY OPEN ITEMS",
    ):
        assert heading in generated_report, (
            f"generated report missing heading {heading}\nworkspace={workspace}"
        )


@pytest.mark.e2e
def test_live_minimax_m2_7_research_updates_scratch_project() -> None:
    require_live_flag()
    if not _OFFICIAL_CONFIG.exists():
        pytest.skip(f"missing config file: {_OFFICIAL_CONFIG}")

    run_id = f"research-project-{int(time.time())}"
    workspace = _fresh_workspace(run_id)
    _seed_research_project(workspace)
    preserved_files = {
        relative_path: (workspace / relative_path).read_text(encoding="utf-8")
        for relative_path in (
            "sample_tasks.csv",
            "task_summary/__init__.py",
            "task_summary/report.py",
            "tests/test_report.py",
        )
    }

    prompt = (
        f"Work only inside this directory: {workspace}.\n"
        "Complete this checklist directly, without plan/decompose/git/pip/tool.list:\n"
        "Your first tool batch must contain exactly three tool calls: one web.fetch "
        "call, one file.write call for pyproject.toml, and one file.write call for "
        "README.md. Do not call file.read before these writes and do not repeat a "
        "successful tool call.\n"
        "1. Fetch this official PyPA Packaging URL with web.fetch: "
        "https://packaging.python.org/en/latest/guides/writing-pyproject-toml/\n"
        "2. Rewrite the complete pyproject.toml with file.write. Preserve the "
        "existing build-system, project metadata, and pytest settings, and add "
        "these two TOML lines: `[project.scripts]` and "
        '`task-summary = "task_summary.report:cli"`.\n'
        "3. Rewrite the complete README.md with file.write so it includes the "
        "existing pytest command plus a usage example containing `task-summary`.\n"
        "Do not modify sample_tasks.csv, task_summary/__init__.py, "
        "task_summary/report.py, tests/test_report.py, or any seeded source/test "
        "file; this task is only a packaging metadata and README update.\n"
        "4. Then read pyproject.toml once and README.md once to verify the "
        "required strings are present.\n"
        "5. Run exactly `python -m pytest -q tests` with exec.run from the workspace; "
        "do not use shell chaining, pipes, redirections, head, curl, wget, ls, cat, "
        "find, or pwd.\n"
        "Use file.list_dir/file.read for workspace inspection. Use file.write, not "
        "file.edit, for the small file rewrites. Do not paste proposed file "
        "contents or pretend a command ran in your final answer; actually call "
        "file.write and exec.run, then answer only from tool results. If a required "
        "tool cannot run, say BLOCKED instead of claiming completion. Do not run "
        "interpreter-discovery commands such as which python/python3 or python "
        "--version before the required pytest command. Return exactly "
        "three titled sections: SOURCES, CHANGES, TESTS. In SOURCES, include the "
        f"PyPA URL and the line `DATE: {_TODAY}`. Do not return a progress note."
    )

    result = run_cli_session(
        session_id_prefix=run_id,
        user_input=f"{prompt}\n/debug\n/exit\n",
        agent_id=_AGENT_ID,
        config_path=_OFFICIAL_CONFIG,
        data_root_override=artifact_dir() / "data-roots" / run_id,
        workspace_root_override=workspace,
        matrix_type="coding_project",
        auto_confirm=True,
    )

    _assert_tool_backing(result.transcript, transcript_path=result.transcript_path)
    assistant_body = _assistant_body(
        result.transcript,
        session_id=result.session_id,
        agent_id=_AGENT_ID,
    )
    for heading in ("SOURCES", "CHANGES", "TESTS"):
        assert heading in assistant_body, (
            f"expected {heading} section in research-to-code answer\n"
            f"transcript={result.transcript_path}"
        )
    assert _TODAY in assistant_body, (
        f"expected today's date citation in research answer\n"
        f"transcript={result.transcript_path}"
    )
    assert (
        "packaging.python.org" in assistant_body or "pypa" in assistant_body.lower()
    ), (
        "expected official packaging citation in research answer\n"
        f"transcript={result.transcript_path}"
    )

    pyproject_body = (workspace / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project.scripts]" in pyproject_body, (
        f"expected project.scripts block after research update\nworkspace={workspace}"
    )
    assert 'task-summary = "task_summary.report:cli"' in pyproject_body, (
        f"expected task-summary console script entry\nworkspace={workspace}"
    )

    readme_body = (workspace / "README.md").read_text(encoding="utf-8")
    assert "task-summary" in readme_body, (
        f"expected README usage update for console script\nworkspace={workspace}"
    )

    for relative_path, original_body in preserved_files.items():
        current_body = (workspace / relative_path).read_text(encoding="utf-8")
        assert current_body == original_body, (
            "research-to-code update modified a seeded source/test/data file; "
            "this live oracle only permits pyproject.toml and README.md rewrites\n"
            f"relative_path={relative_path}\nworkspace={workspace}"
        )

    pytest_result = _run_local_pytest(workspace)
    assert pytest_result.returncode == 0, (
        f"local pytest verification failed after research update\nworkspace={workspace}\n"
        f"stdout={pytest_result.stdout}\nstderr={pytest_result.stderr}\n"
        f"transcript={result.transcript_path}"
    )
