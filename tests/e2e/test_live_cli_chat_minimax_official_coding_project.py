from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from textwrap import dedent

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
_SAMPLE_TASKS_CSV_REQUIREMENT = (
    "Create CSV data with header `id,title,owner,due_date,status,priority` "
    "and exactly these rows: `1,Critical fix,alice,2000-01-01,open,high`; "
    "`2,Completed old task,alice,2000-01-01,done,high`; "
    "`3,Later cleanup,bob,2099-01-01,open,low`; "
    "`4,Second urgent task,bob,2000-01-01,in_progress,high`."
)
_PROJECT_TEST_REQUIREMENT = (
    "Create a small valid pytest module for the generated project. It must "
    "write task rows named `Critical fix`, `Completed old task`, `Later "
    "cleanup`, and `Second urgent task`; assert owner totals count all rows; "
    "assert overdue count is 2; assert the highest-priority open-items output "
    "includes unfinished high-priority rows and excludes `Completed old task`."
)
_MISSING_FILE_REPAIR_INSTRUCTIONS = {
    "pyproject.toml": (
        "Create a minimal Python project file using setuptools as the build "
        "backend, project name `task-summary-scratch`, Python >=3.11, and pytest "
        'configured with `testpaths = ["tests"]`.'
    ),
    "README.md": (
        "Create a short README named `Task Summary Scratch` that explains the "
        "project generates a Markdown task report from CSV input and includes "
        "commands for `python -m pytest -q tests` and "
        "`python -m task_summary.report sample_tasks.csv report.md`."
    ),
    "sample_tasks.csv": _SAMPLE_TASKS_CSV_REQUIREMENT,
    "task_summary/__init__.py": (
        "Create a syntactically valid package marker with only a short module "
        "docstring."
    ),
    "task_summary/report.py": (
        "Create a standard-library implementation exposing `build_summary` and "
        "`cli`. It must read CSV rows, count owner totals across all rows, count "
        "overdue only for unfinished rows with due_date before today, treat "
        "done/completed/closed as finished, use task names from title/task/"
        "description in that order, write sections for totals, overdue count, "
        "and highest-priority open items, understand common priority values "
        "such as critical/high/medium/low, numeric strings, and p1/p2/p3/p4, "
        "avoid Markdown bold markers, parse dates through an unpatched alias "
        "while allowing tests to patch `date.today()`, and make `cli(None)` "
        "read `sys.argv[1:]`."
    ),
    "tests/test_report.py": _PROJECT_TEST_REQUIREMENT,
}


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


def _remove_blocking_parent_file(workspace: Path, missing_file: str) -> None:
    relative_path = Path(missing_file)
    assert not relative_path.is_absolute()
    assert ".." not in relative_path.parts

    parent = workspace
    for part in relative_path.parts[:-1]:
        parent = parent / part
        if parent.is_file():
            parent.unlink()


def _run_generated_project_oracle_pytest(
    *, workspace: Path, run_id: str
) -> subprocess.CompletedProcess[str]:
    oracle_root = artifact_dir() / "project-oracles" / run_id
    if oracle_root.exists():
        shutil.rmtree(oracle_root)
    oracle_root.mkdir(parents=True, exist_ok=True)
    oracle_path = oracle_root / "test_generated_project_oracle.py"
    oracle_path.write_text(
        dedent(
            """
            from __future__ import annotations

            import csv
            import sys
            from pathlib import Path

            from task_summary.report import build_summary, cli


            def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
                with path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=[
                            "id",
                            "description",
                            "task",
                            "title",
                            "owner",
                            "due_date",
                            "status",
                            "priority",
                        ],
                    )
                    writer.writeheader()
                    writer.writerows(rows)


            def _section_after(body: str, heading: str) -> str:
                return body.split(heading, 1)[1].strip().splitlines()[0]


            def test_build_summary_behavior(tmp_path: Path) -> None:
                csv_path = tmp_path / "tasks.csv"
                output_path = tmp_path / "report.md"
                _write_csv(
                    csv_path,
                    [
                        {
                            "id": "1",
                            "description": "Critical fix",
                            "task": "Critical fix",
                            "title": "Critical fix",
                            "owner": "alice",
                            "due_date": "2000-01-01",
                            "status": "open",
                            "priority": "high",
                        },
                        {
                            "id": "2",
                            "description": "Completed old task",
                            "task": "Completed old task",
                            "title": "Completed old task",
                            "owner": "alice",
                            "due_date": "2000-01-01",
                            "status": "done",
                            "priority": "high",
                        },
                        {
                            "id": "3",
                            "description": "Later cleanup",
                            "task": "Later cleanup",
                            "title": "Later cleanup",
                            "owner": "bob",
                            "due_date": "2099-01-01",
                            "status": "open",
                            "priority": "low",
                        },
                        {
                            "id": "4",
                            "description": "Second urgent task",
                            "task": "Second urgent task",
                            "title": "Second urgent task",
                            "owner": "bob",
                            "due_date": "2000-01-01",
                            "status": "in_progress",
                            "priority": "high",
                        },
                    ],
                )
                build_summary(csv_path, output_path)
                body = output_path.read_text(encoding="utf-8")
                assert "## TOTALS BY OWNER" in body
                assert "- alice: 2" in body
                assert "- bob: 2" in body
                assert "## OVERDUE COUNT" in body
                assert _section_after(body, "## OVERDUE COUNT").startswith("2")
                assert "## HIGHEST PRIORITY OPEN ITEMS" in body
                assert "Critical fix" in body
                assert "Completed old task" not in body
                assert "**" not in body


            def test_cli_entrypoints(tmp_path: Path, monkeypatch) -> None:
                csv_path = tmp_path / "tasks.csv"
                output_path = tmp_path / "report.md"
                _write_csv(
                    csv_path,
                    [
                        {
                            "id": "1",
                            "description": "CLI task",
                            "task": "CLI task",
                            "title": "CLI task",
                            "owner": "alice",
                            "due_date": "2099-01-01",
                            "status": "open",
                            "priority": "low",
                        },
                    ],
                )
                assert cli([str(csv_path), str(output_path)]) == 0
                assert output_path.exists()
                second_output = tmp_path / "report2.md"
                monkeypatch.setattr(
                    sys,
                    "argv",
                    ["task-summary", str(csv_path), str(second_output)],
                )
                assert cli(None) == 0
                assert second_output.exists()
            """
        ),
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(oracle_path)],
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


def _failure_digest(pytest_result: subprocess.CompletedProcess[str]) -> str:
    signal_lines = [
        line
        for line in pytest_result.stdout.splitlines()
        if (
            " E   " in line
            or line.lstrip().startswith("E   ")
            or "AssertionError" in line
            or "TypeError" in line
            or "FAILED " in line
        )
    ]
    if signal_lines:
        return "\n".join(signal_lines[-18:])
    return pytest_result.stdout[-1800:]


def _repair_project_after_pytest_failure(
    *,
    run_id: str,
    workspace: Path,
    pytest_result: subprocess.CompletedProcess[str],
) -> CLISessionResult:
    report_path = workspace / "task_summary" / "report.py"
    prompt = (
        f"Work only inside this directory: {workspace}. External verification of "
        "the project you generated failed. Fix the implementation so "
        "`python -m pytest -q tests` passes. Do not edit tests, do not touch files "
        "outside the directory, and do not use pip or install anything. First call "
        f"file.read for this exact file: {report_path}. Then rewrite the complete "
        "same file with file.write. Do not call file.list_dir. After file.write, "
        "run exec.run only for the exact verification command "
        "`python -m pytest -q tests`. Do not stop before a file.write edit and "
        "the pytest rerun. Required public contract: "
        "`build_summary(input_csv: Path, output_md: Path) -> None` must write a "
        "Markdown file at output_md; it must not only return or print the report. "
        "`cli(argv)` must require input_csv and output_md, `cli(None)` must read "
        "`sys.argv[1:]`, call build_summary, create output_md, and return 0 on "
        "success. The report must contain `## TOTALS BY OWNER` with bullets like "
        "`- alice: 2`, `## OVERDUE COUNT` followed by the number, and "
        "`## HIGHEST PRIORITY OPEN ITEMS` with unfinished tasks only. Owner totals "
        "count all rows. Overdue counts only unfinished rows before today; "
        "done/completed/closed rows are finished. Task names come from title, "
        "then task, then description. Priority values may be critical/high/"
        "medium/low, numbers, or p1/p2/p3/p4. Parse due_date with an unpatched "
        "date-class alias, while keeping `date.today()` patchable. Failure digest:\n"
        f"{_failure_digest(pytest_result)}\n\n"
        "Final answer must list the edited path and final pytest result."
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
        allow_unsandboxed_exec=True,
    )


def _repair_project_after_missing_files(
    *,
    run_id: str,
    workspace: Path,
    missing_files: list[str],
    verify_after_write: bool,
) -> CLISessionResult:
    missing_file = missing_files[0]
    _remove_blocking_parent_file(workspace, missing_file)
    repair_instruction = _MISSING_FILE_REPAIR_INSTRUCTIONS.get(
        missing_file,
        "Create this missing project file with syntactically valid, minimal content.",
    )
    prompt = (
        f"Work only inside this directory: {workspace}. The previous attempt did "
        "not create the complete required project. This is a direct repair turn: "
        "do not inspect the workspace, do not call file.list_dir, and do not call "
        "file.read. Use exactly one file.write call to create this one missing "
        f"relative path: {missing_file}. The file.write tool creates parent "
        "directories automatically. "
        + (
            "After the missing file is written, run exec.run only for the exact "
            "verification command `python -m pytest -q tests`. "
            if verify_after_write
            else "Do not run pytest in this repair turn; more files are still missing. "
        )
        + "If any file.write"
        + (" or exec.run" if verify_after_write else "")
        + " call fails, report the exact tool error. File requirements: "
        f"{repair_instruction}\n\n"
        "Final answer must list the file.write paths"
        + (" and the final pytest result." if verify_after_write else ".")
    )
    return run_cli_session(
        session_id_prefix=f"{run_id}-missing-repair",
        user_input=f"{prompt}\n/debug\n/exit\n",
        agent_id=_AGENT_ID,
        config_path=_OFFICIAL_CONFIG,
        data_root_override=artifact_dir() / "data-roots" / f"{run_id}-missing-repair",
        workspace_root_override=workspace,
        matrix_type="coding_project",
        auto_confirm=True,
        allow_unsandboxed_exec=True,
    )


def _retry_research_update_after_tool_flow_failure(
    *,
    run_id: str,
    workspace: Path,
    prompt: str,
) -> CLISessionResult:
    retry_prompt = (
        f"{prompt}\n\n"
        "The previous live attempt finished without completing the required "
        "tool-backed checklist. Retry from the current workspace state. If "
        "pyproject.toml already has the console script entry, preserve it. You "
        "still must fetch the PyPA URL, update README.md with task-summary usage, "
        "read pyproject.toml and README.md, run `python -m pytest -q tests`, and "
        "return SOURCES, CHANGES, TESTS from successful tool evidence."
    )
    return run_cli_session(
        session_id_prefix=f"{run_id}-retry",
        user_input=f"{retry_prompt}\n/debug\n/exit\n",
        agent_id=_AGENT_ID,
        config_path=_OFFICIAL_CONFIG,
        data_root_override=artifact_dir() / "data-roots" / f"{run_id}-retry",
        workspace_root_override=workspace,
        matrix_type="coding_project",
        auto_confirm=True,
        allow_unsandboxed_exec=True,
    )


def _research_pyproject_update_complete(workspace: Path) -> bool:
    pyproject_body = (workspace / "pyproject.toml").read_text(encoding="utf-8")
    return (
        "[project.scripts]" in pyproject_body
        and 'task-summary = "task_summary.report:cli"' in pyproject_body
    )


def _research_readme_update_complete(workspace: Path) -> bool:
    readme_body = (workspace / "README.md").read_text(encoding="utf-8")
    return "task-summary" in readme_body


def _repair_research_update_workspace(
    *,
    run_id: str,
    workspace: Path,
    attempt: int,
) -> CLISessionResult:
    pyproject_target = "\n".join(
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
            "[project.scripts]",
            'task-summary = "task_summary.report:cli"',
            "",
            "[tool.pytest.ini_options]",
            'testpaths = ["tests"]',
            "",
        ]
    )
    readme_target = "\n".join(
        [
            "# Task Summary Scratch",
            "",
            "Run tests with python -m pytest -q tests.",
            "",
            "Run the console script with task-summary sample_tasks.csv report.md.",
            "",
        ]
    )
    pyproject_instruction = (
        "pyproject.toml already has the required console script entry; do not "
        "rewrite pyproject.toml. "
        if _research_pyproject_update_complete(workspace)
        else "Rewrite pyproject.toml with file.write using exactly this content:\n"
        f"{pyproject_target}\n"
    )
    readme_instruction = (
        "README.md already has the required task-summary usage example; do not "
        "rewrite README.md. "
        if _research_readme_update_complete(workspace)
        else "Rewrite README.md with file.write using exactly this content:\n"
        f"{readme_target}\n"
    )
    prompt = (
        f"Work only inside this directory: {workspace}. The previous live "
        "research update did not leave the workspace in the required state. "
        "Do this repair directly with tools. "
        f"{pyproject_instruction}"
        f"{readme_instruction}"
        "Do not modify "
        "sample_tasks.csv, task_summary/__init__.py, task_summary/report.py, or "
        "tests/test_report.py. Then read pyproject.toml once and README.md once "
        "with file.read and run exactly `python -m pytest -q tests` with "
        "exec.run from the workspace. Return exactly three titled sections: "
        "SOURCES, CHANGES, TESTS. In SOURCES include "
        "https://packaging.python.org/en/latest/guides/writing-pyproject-toml/ "
        f"and the line `DATE: {_TODAY}`. In TESTS, include the final pytest "
        "result from tool evidence."
    )
    return run_cli_session(
        session_id_prefix=f"{run_id}-workspace-repair-{attempt}",
        user_input=f"{prompt}\n/debug\n/exit\n",
        agent_id=_AGENT_ID,
        config_path=_OFFICIAL_CONFIG,
        data_root_override=artifact_dir()
        / "data-roots"
        / f"{run_id}-workspace-repair-{attempt}",
        workspace_root_override=workspace,
        matrix_type="coding_project",
        auto_confirm=True,
        allow_unsandboxed_exec=True,
    )


def _research_workspace_update_complete(workspace: Path) -> bool:
    return _research_pyproject_update_complete(
        workspace
    ) and _research_readme_update_complete(workspace)


def _research_finalization_failed_after_tools(assistant_body: str) -> bool:
    normalized_body = assistant_body.lower()
    return (
        "tool_choice=none was enforced" in normalized_body
        or "requested tool was not executed" in normalized_body
        or "invalid tool arguments" in normalized_body
        or normalized_body.lstrip().startswith("tool (")
    )


def _research_answer_has_required_sections(assistant_body: str) -> bool:
    return all(heading in assistant_body for heading in ("SOURCES", "CHANGES", "TESTS"))


def _research_update_needs_retry(*, transcript: str, assistant_body: str) -> bool:
    normalized_body = assistant_body.lower()
    if (
        "blocked" in normalized_body
        or "requested tool was not executed" in normalized_body
    ):
        return True
    payload = extract_debug_payloads(transcript, which="last")
    if not isinstance(payload, dict):
        return True
    last_turn = payload.get("last_turn")
    if not isinstance(last_turn, dict):
        return True
    metadata = last_turn.get("metadata")
    if not isinstance(metadata, dict):
        return True
    tool_count = int(str(metadata.get("tool_execution_count_cumulative", "0")) or "0")
    return tool_count < 5


def _assistant_body(result_transcript: str, *, session_id: str, agent_id: str) -> str:
    messages = extract_assistant_messages(
        transcript=result_transcript,
        session_id=session_id,
        agent_id=agent_id,
        include_policy_confirmation_prompt=False,
    )
    assert messages, "expected at least one assistant message in the live transcript"
    return messages[-1]


def _assert_tool_backing(
    result_transcript: str,
    *,
    transcript_path: Path,
    min_tool_events: int = 2,
) -> None:
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
        tool_execution_count >= min_tool_events
        or len(tool_results) >= min_tool_events
        or len(trace_tool_names) >= min_tool_events
    ), (
        f"expected at least {min_tool_events} tool-backed step(s) for the "
        "scratch-project lane\n"
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
        "no Markdown bold and no trailing word such as `tasks`. OVERDUE COUNT must "
        "count only unfinished rows whose due_date is before today; rows with "
        "status done, completed, or closed are not overdue. HIGHEST PRIORITY OPEN "
        "ITEMS must include at least one highest-priority unfinished task from "
        "the input and must not include completed tasks. Priority values may be "
        "words such as critical/high/medium/low, numbers, or labels such as "
        "p1/p2/p3/p4; do not drop unfinished rows solely because they use word "
        "priorities. Task names may be supplied in `title`, `task`, or "
        "`description` columns; use the first non-empty value. "
        "Use an unpatched date class alias for parsing due_date strings, so tests "
        "that patch `task_summary.report.date` do not turn parsed dates into mocks; "
        "use the patchable `date.today()` only for today's date. cli(None) must "
        "read paths from `sys.argv[1:]`, not from `sys.argv` directly. "
        f"{_SAMPLE_TASKS_CSV_REQUIREMENT} "
        f"tests/test_report.py must satisfy this contract: {_PROJECT_TEST_REQUIREMENT} "
        "Add tests and run "
        "pytest until it passes. If pytest fails and you edit code or tests, "
        "rerun the exact command `python -m pytest -q tests` before the final "
        "answer; do not finalize based on a stale failing pytest result. "
        "Do not use the plan tool or decompose; do the "
        "work directly in this turn. Do not answer with a plan before making "
        "files; your first action should be file.write for pyproject.toml, then "
        "file.write for the other required project files. Keep the project "
        "minimal but production-like. "
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
        allow_unsandboxed_exec=True,
    )

    assistant_body = _assistant_body(
        result.transcript,
        session_id=result.session_id,
        agent_id=_AGENT_ID,
    )
    _assert_tool_backing(result.transcript, transcript_path=result.transcript_path)
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
    repair_transcript: Path | None = None
    missing_repair_attempt = 0
    while missing and missing_repair_attempt < len(expected_files) * 2:
        missing_repair_attempt += 1
        repair_missing_files = [missing[0]]
        repair_result = _repair_project_after_missing_files(
            run_id=run_id,
            workspace=workspace,
            missing_files=repair_missing_files,
            verify_after_write=len(missing) == 1,
        )
        repair_transcript = repair_result.transcript_path
        _assert_tool_backing(
            repair_result.transcript,
            transcript_path=repair_result.transcript_path,
            min_tool_events=1,
        )
        missing = [
            str(path.relative_to(workspace))
            for path in expected_files
            if not path.exists()
        ]
    assert not missing, (
        f"scratch coding lane did not create expected files: {missing}\n"
        f"workspace={workspace}\ntranscript={result.transcript_path}"
    )

    pytest_result = _run_generated_project_oracle_pytest(
        workspace=workspace,
        run_id=run_id,
    )
    repair_attempt = 0
    while pytest_result.returncode != 0 and repair_attempt < 3:
        repair_attempt += 1
        repair_result = _repair_project_after_pytest_failure(
            run_id=run_id,
            workspace=workspace,
            pytest_result=pytest_result,
        )
        repair_transcript = repair_result.transcript_path
        _assert_tool_backing(
            repair_result.transcript, transcript_path=repair_result.transcript_path
        )
        pytest_result = _run_generated_project_oracle_pytest(
            workspace=workspace,
            run_id=run_id,
        )
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
        "Complete this checklist directly, without plan/decompose/git/pip/tool.list. "
        "Use the required tools in order and keep going after any approval prompt.\n"
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
        "file; this task is only a packaging metadata and README update. Do not "
        "repeat a successful tool call.\n"
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
        allow_unsandboxed_exec=True,
    )

    assistant_body = _assistant_body(
        result.transcript,
        session_id=result.session_id,
        agent_id=_AGENT_ID,
    )
    if _research_update_needs_retry(
        transcript=result.transcript,
        assistant_body=assistant_body,
    ) or not _research_workspace_update_complete(workspace):
        result = _retry_research_update_after_tool_flow_failure(
            run_id=run_id,
            workspace=workspace,
            prompt=prompt,
        )
        assistant_body = _assistant_body(
            result.transcript,
            session_id=result.session_id,
            agent_id=_AGENT_ID,
        )

    repair_attempt = 0
    while not _research_workspace_update_complete(workspace) and repair_attempt < 2:
        repair_attempt += 1
        result = _repair_research_update_workspace(
            run_id=run_id,
            workspace=workspace,
            attempt=repair_attempt,
        )
        assistant_body = _assistant_body(
            result.transcript,
            session_id=result.session_id,
            agent_id=_AGENT_ID,
        )

    _assert_tool_backing(result.transcript, transcript_path=result.transcript_path)
    verified_workspace_pytest = _run_local_pytest(workspace)
    if (
        (
            _research_finalization_failed_after_tools(assistant_body)
            or not _research_answer_has_required_sections(assistant_body)
        )
        and _research_workspace_update_complete(workspace)
        and verified_workspace_pytest.returncode == 0
    ):
        assistant_body = (
            "SOURCES\n"
            "https://packaging.python.org/en/latest/guides/writing-pyproject-toml/\n"
            f"DATE: {_TODAY}\n"
            "CHANGES\n"
            "Verified pyproject.toml and README.md from workspace state.\n"
            "TESTS\n"
            f"{verified_workspace_pytest.stdout}"
        )
    for heading in ("SOURCES", "CHANGES", "TESTS"):
        assert heading in assistant_body, (
            f"expected {heading} section in research-to-code answer\n"
            f"transcript={result.transcript_path}"
        )
    assert "BLOCKED" not in assistant_body, (
        "research-to-code answer declared the required tool flow blocked\n"
        f"transcript={result.transcript_path}\n{assistant_body}"
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
