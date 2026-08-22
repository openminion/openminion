from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class FocusScenario:
    scenario_id: str
    prompt: str
    expected_markers: tuple[str, ...] = ()
    timeout: int = 240
    requires_approval: bool = False
    max_auto_approvals: int = 5
    approval_reply: str = "yes"
    use_scratch_workspace: bool = False
    include_project_context: bool = True
    min_generated_files: int = 0
    expected_file_patterns: tuple[str, ...] = ()
    forbidden_transcript_markers: tuple[str, ...] = ()
    validation_commands: tuple[tuple[str, ...], ...] = ()
    max_auto_continuations: int = 0


SCRATCH_RELATIVE_PATH_RULE = (
    "Use relative paths under the current directory only; do not use absolute "
    "paths and do not create nested scratch directories. "
)


BASE_LIVE_SCENARIOS: tuple[FocusScenario, ...] = (
    FocusScenario(
        scenario_id="exact_reply",
        prompt="Reply with exactly: CLI Focus live smoke OK",
        expected_markers=("CLI Focus live smoke OK",),
        timeout=180,
    ),
)


TOOL_LIVE_SCENARIOS: tuple[FocusScenario, ...] = (
    FocusScenario(
        scenario_id="time_tool",
        prompt="Use the time tool to tell me the current UTC time in one sentence.",
        expected_markers=("UTC",),
        timeout=240,
    ),
    FocusScenario(
        scenario_id="policy_recovery",
        prompt=(
            "Check whether nasm is installed by using the allowed discovery shape "
            "`command -v nasm`, then summarize the result. Do not install anything."
        ),
        expected_markers=("nasm",),
        timeout=240,
        requires_approval=True,
        max_auto_approvals=4,
        approval_reply="session",
    ),
    FocusScenario(
        scenario_id="browser_tool_status",
        prompt=(
            "Use the browser tool with the daemon.status operation to check "
            "browser/provider readiness, then summarize the browser status in "
            "one sentence."
        ),
        expected_markers=("browser",),
        timeout=360,
        requires_approval=True,
        max_auto_approvals=4,
        approval_reply="session",
    ),
)


RESEARCH_LIVE_SCENARIOS: tuple[FocusScenario, ...] = (
    FocusScenario(
        scenario_id="research_deep_brief",
        prompt=(
            "Do a compact deep-research style pass on current Python packaging "
            "metadata best practices. Use at most three search/fetch calls total, "
            "compare at least three points from available evidence, and end with "
            "a short recommended direction."
        ),
        expected_markers=("recommendation|recommended|recommend",),
        timeout=900,
    ),
    FocusScenario(
        scenario_id="research_complex_tradeoffs",
        prompt=(
            "Research terminal-agent UX patterns for long running tasks. Compare "
            "Codex-style, Claude-style, and OpenCode-style interaction patterns "
            "when evidence is available. Use at most four search/fetch calls total, "
            "then stop searching and produce exact labels `tradeoffs:` and "
            "`recommendation:` with a concise tradeoff matrix plus a practical "
            "recommended direction for OpenMinion."
        ),
        expected_markers=("tradeoffs", "recommendation|recommended|recommend"),
        timeout=1200,
    ),
    FocusScenario(
        scenario_id="research_long_synthesis",
        prompt=(
            "Run a long-form research synthesis on robust CLI agent test harnesses. "
            "Cover PTY testing, transcript artifacts, live-provider gating, "
            "failure classification, and maintainability. End with prioritized "
            "next steps."
        ),
        expected_markers=("next steps",),
        timeout=1500,
        requires_approval=True,
        max_auto_approvals=4,
        approval_reply="session",
        max_auto_continuations=4,
    ),
)


CODING_LIVE_SCENARIOS: tuple[FocusScenario, ...] = (
    FocusScenario(
        scenario_id="coding_deep_scratch_feature",
        prompt=(
            "In the current directory, create `tiny_math.py` with an `add_one` "
            "function and `test_tiny_math.py` with pytest test functions for zero "
            "and a negative input. Run `python -m pytest -q` to verify them. "
            f"{SCRATCH_RELATIVE_PATH_RULE}"
            "Use file tools for files and direct exec.run commands for checks. "
            "Keep it small and finish with the exact label `result:`."
        ),
        expected_markers=("result",),
        timeout=900,
        requires_approval=True,
        max_auto_approvals=8,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
        min_generated_files=2,
        expected_file_patterns=("tiny_math.py", "test_tiny_math.py"),
        validation_commands=(("{python}", "-m", "pytest", "-q"),),
    ),
    FocusScenario(
        scenario_id="coding_complex_debug_loop",
        prompt=(
            "In the current directory, create `math_utils.py` with a `divide(a, b)` "
            "function and `test_math_utils.py` covering a normal division and a "
            "zero-divisor `ValueError`. "
            f"{SCRATCH_RELATIVE_PATH_RULE}"
            "Use file.write for files and direct exec.run commands for checks; "
            "do not only show code snippets. Include one edge case, fix any "
            "issue you find, run a focused check, and finish with the exact "
            "label `result:` plus the bug and fix."
        ),
        expected_markers=("result",),
        timeout=1200,
        requires_approval=True,
        max_auto_approvals=8,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
        min_generated_files=2,
        expected_file_patterns=("math_utils.py", "test_math_utils.py"),
        validation_commands=(("{python}", "-m", "pytest", "-q"),),
    ),
    FocusScenario(
        scenario_id="coding_long_project_slice",
        prompt=(
            "In the current directory, build a tiny Python greeting CLI project "
            "with exactly `greet.py`, `cli.py`, `test_greet.py`, and `README.md`. "
            "`greet.py` must expose `greet(name)` and `python cli.py Codex` must "
            "print exactly `Hello, Codex!`. "
            f"{SCRATCH_RELATIVE_PATH_RULE}"
            "Use file.write for files and direct exec.run commands for checks; "
            "do not only show code snippets. Keep it under five files, run "
            "focused validation, and finish with files changed plus the exact "
            "label `result:`."
        ),
        expected_markers=("result",),
        timeout=1500,
        requires_approval=True,
        max_auto_approvals=10,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
        min_generated_files=4,
        expected_file_patterns=("greet.py", "cli.py", "test_greet.py", "README.md"),
        max_auto_continuations=2,
        validation_commands=(
            ("{python}", "-m", "pytest", "-q"),
            (
                "{python}",
                "-c",
                (
                    "import subprocess, sys; "
                    "result = subprocess.run([sys.executable, 'cli.py', 'Codex'], "
                    "capture_output=True, text=True); "
                    "assert result.returncode == 0, result.stderr; "
                    "assert result.stdout.strip() == 'Hello, Codex!', result.stdout"
                ),
            ),
        ),
    ),
)


SOAK_LIVE_SCENARIOS: tuple[FocusScenario, ...] = (
    FocusScenario(
        scenario_id="goal_long_python_project_loop",
        prompt=(
            "Treat this as a long-running goal-style coding loop in the current "
            "directory. Build a small, self-contained zero-dependency Python CLI "
            "named `loopcalc` in `loopcalc.py` using at most five files. It must "
            "support `python loopcalc.py sum 5` and print `5`. "
            f"{SCRATCH_RELATIVE_PATH_RULE}"
            "Begin by using file.write for `loopcalc.py`; do not inspect the repo "
            "first. Use file.write and file.read, then use direct exec.run to run "
            "`python loopcalc.py sum 5`. Fix the implementation until it exits "
            "successfully and prints exactly `5`, then finish with exact labels "
            "`files:`, `validation:`, and `follow-ups:`."
        ),
        expected_markers=("validation", "files"),
        timeout=2400,
        requires_approval=True,
        max_auto_approvals=12,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
        min_generated_files=1,
        expected_file_patterns=("loopcalc.py",),
        forbidden_transcript_markers=("code.repo_index(",),
        max_auto_continuations=4,
        validation_commands=(
            (
                "{python}",
                "-c",
                (
                    "import subprocess, sys; "
                    "result = subprocess.run("
                    "[sys.executable, 'loopcalc.py', 'sum', '5'], "
                    "capture_output=True, text=True); "
                    "assert result.returncode == 0, result.stderr; "
                    "assert result.stdout.strip() == '5', result.stdout"
                ),
            ),
        ),
    ),
    FocusScenario(
        scenario_id="goal_research_then_code_loop",
        prompt=(
            "Treat this as a long-running self-directed project in the current "
            f"directory. {SCRATCH_RELATIVE_PATH_RULE}"
            "Pick a minimal design for a Python CLI that summarizes "
            "text-file word counts and implement it with file.write/file.read. "
            "Avoid installs. Begin by using file.write for "
            "`word_count_cli.py`; do not inspect the repo first. "
            "Create `sample.txt` containing exactly two words, then use direct "
            "exec.run to run `python word_count_cli.py sample.txt`. Fix the "
            "implementation until it exits successfully and reports `2`. Close "
            "with `design:`, `implementation:`, `validation:`, and `next steps:`."
        ),
        expected_markers=("validation", "next steps"),
        timeout=3000,
        requires_approval=True,
        max_auto_approvals=12,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
        min_generated_files=2,
        expected_file_patterns=("word_count_cli.py", "sample.txt"),
        forbidden_transcript_markers=("code.repo_index(",),
        max_auto_continuations=4,
        validation_commands=(
            (
                "{python}",
                "-c",
                (
                    "from pathlib import Path; "
                    "import subprocess, sys; "
                    "Path('sample.txt').write_text('one two\\n', encoding='utf-8'); "
                    "result = subprocess.run("
                    "[sys.executable, 'word_count_cli.py', 'sample.txt'], "
                    "capture_output=True, text=True); "
                    "assert result.returncode == 0, result.stderr; "
                    "assert '2' in result.stdout"
                ),
            ),
        ),
    ),
    FocusScenario(
        scenario_id="goal_deep_research_analysis_code_loop",
        prompt=(
            "Treat this as a long-running mixed research, analysis, and coding "
            f"goal in the current directory. {SCRATCH_RELATIVE_PATH_RULE}"
            "Compare two minimal designs for a "
            "Python CLI that summarizes Markdown sections, pick the simpler one, "
            "and implement exactly `section_summary.py`, `section_summary_cli.py`, "
            "`test_section_summary.py`, and `README.md` using file.write/file.read. "
            "The module must expose `parse_sections(text)`, and the CLI must accept "
            "one Markdown path and print its section headings. Avoid installs. "
            "Begin by using file.write for `section_summary.py`; do not inspect "
            "the repo first. "
            "Use direct exec.run to run `python -m pytest -q`; inspect failures "
            "and fix the implementation until every test passes. Finish with "
            "`design:`, `files:`, `validation:`, and `follow-ups:`."
        ),
        expected_markers=("design", "validation", "files"),
        timeout=3000,
        requires_approval=True,
        max_auto_approvals=12,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
        min_generated_files=4,
        expected_file_patterns=(
            "section_summary.py",
            "section_summary_cli.py",
            "test_section_summary.py",
            "README.md",
        ),
        forbidden_transcript_markers=("code.repo_index(",),
        max_auto_continuations=4,
        validation_commands=(
            (
                "{python}",
                "-c",
                (
                    "import section_summary; "
                    "result = section_summary.parse_sections('# A\\ntext'); "
                    "assert result"
                ),
            ),
            ("{python}", "-m", "pytest", "-q"),
            (
                "{python}",
                "-c",
                (
                    "from pathlib import Path; import subprocess, sys; "
                    "Path('sample.md').write_text('# A\\ntext', encoding='utf-8'); "
                    "result = subprocess.run("
                    "[sys.executable, 'section_summary_cli.py', 'sample.md'], "
                    "capture_output=True, text=True); "
                    "assert result.returncode == 0, result.stderr; "
                    "assert 'A' in result.stdout, result.stdout"
                ),
            ),
        ),
    ),
)


COMPLEX_LIVE_SCENARIOS: tuple[FocusScenario, ...] = (
    *RESEARCH_LIVE_SCENARIOS,
    *CODING_LIVE_SCENARIOS,
)


def assert_scenario_contract(
    scenario: FocusScenario,
    *,
    scratch_dir: Path,
    transcript: str,
    python_bin: str = "python3",
) -> None:
    generated_files = _generated_scenario_files(scratch_dir)
    if len(generated_files) < scenario.min_generated_files:
        relative_files = sorted(
            str(path.relative_to(scratch_dir)) for path in generated_files
        )
        raise AssertionError(
            f"{scenario.scenario_id} generated {len(generated_files)} file(s), "
            f"expected at least {scenario.min_generated_files}: {relative_files}"
        )
    for pattern in scenario.expected_file_patterns:
        if not any(scratch_dir.glob(pattern)):
            relative_files = sorted(
                str(path.relative_to(scratch_dir)) for path in generated_files
            )
            raise AssertionError(
                f"{scenario.scenario_id} did not create a file matching "
                f"{pattern!r}; generated files: {relative_files}"
            )
    for marker in scenario.forbidden_transcript_markers:
        if marker in transcript:
            raise AssertionError(
                f"{scenario.scenario_id} transcript included forbidden marker "
                f"{marker!r}"
            )
    for command in scenario.validation_commands:
        rendered = tuple(python_bin if item == "{python}" else item for item in command)
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        completed = subprocess.run(
            rendered,
            cwd=scratch_dir,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"{scenario.scenario_id} validation command failed "
                f"with exit {completed.returncode}: {rendered!r}\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )


def _generated_scenario_files(scratch_dir: Path) -> list[Path]:
    return [
        path
        for path in scratch_dir.rglob("*")
        if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts
    ]
