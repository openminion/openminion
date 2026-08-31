from __future__ import annotations

from dataclasses import replace
import json
import sqlite3
import sys

import pytest

from tests.e2e.cli.focus.conftest import require_complex_focus
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.scenarios import (
    COMPLEX_LIVE_SCENARIOS,
    FocusScenario,
    assert_scenario_contract,
)

pytestmark = [pytest.mark.e2e]

_SECURITY_READER_VALIDATION = (
    "{python}",
    "-c",
    (
        "from safe_reader import read_document; "
        "assert read_document('allowed.txt') == 'allowed\\n'; "
        "\ntry: read_document('../secret.txt')\n"
        "except ValueError: pass\n"
        "else: raise AssertionError('path traversal was not rejected')"
    ),
)


def _scenario_param(scenario):
    timeout = scenario.timeout * (scenario.max_auto_continuations + 1) + 60
    return pytest.param(scenario, marks=pytest.mark.timeout(timeout))


def test_security_validation_does_not_trust_fixture_test(tmp_path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "allowed.txt").write_text("allowed\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret\n", encoding="utf-8")
    (tmp_path / "safe_reader.py").write_text(
        "from pathlib import Path\n\n"
        "DATA_ROOT = Path(__file__).parent / 'data'\n\n"
        "def read_document(name: str) -> str:\n"
        "    return (DATA_ROOT / name).read_text(encoding='utf-8')\n",
        encoding="utf-8",
    )
    (tmp_path / "test_safe_reader.py").write_text(
        "def test_false_positive() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    scenario = FocusScenario(
        scenario_id="security-verifier",
        prompt="",
        validation_commands=(
            ("{python}", "-m", "pytest", "-q"),
            _SECURITY_READER_VALIDATION,
        ),
    )

    with pytest.raises(AssertionError, match="path traversal was not rejected"):
        assert_scenario_contract(
            scenario,
            scratch_dir=tmp_path,
            transcript="",
            python_bin=sys.executable,
        )


@pytest.mark.parametrize(
    "scenario",
    [_scenario_param(scenario) for scenario in COMPLEX_LIVE_SCENARIOS],
    ids=[scenario.scenario_id for scenario in COMPLEX_LIVE_SCENARIOS],
)
def test_live_focus_complex_scenarios(
    focus_probe: FocusProbe,
    scenario,
    tmp_path,
) -> None:
    require_complex_focus()
    root = artifact_root(tmp_path)
    scratch_dir = root / "scratch" / scenario.scenario_id
    scratch_dir.mkdir(parents=True, exist_ok=True)
    scenario = replace(
        scenario,
        prompt=scenario.prompt.format(scratch_dir=scratch_dir),
    )
    active_probe = (
        focus_probe.for_workdir(
            scratch_dir,
            include_project_context=scenario.include_project_context,
        )
        if scenario.use_scratch_workspace
        else focus_probe
    )
    with active_probe.session(rows=50, cols=160) as session:
        active_probe.wait_ready(session)
        try:
            transcript = active_probe.run_turn(session, scenario)
        except BaseException:
            write_transcript(root, scenario.scenario_id, session.transcript)
            raise
        write_transcript(root, scenario.scenario_id, transcript)
    if scenario.use_scratch_workspace:
        assert_scenario_contract(
            scenario,
            scratch_dir=scratch_dir,
            transcript=transcript,
            python_bin=focus_probe.python_bin,
        )


@pytest.mark.parametrize("run_number", (1, 2))
@pytest.mark.timeout(3060)
def test_live_focus_security_terminal_recovery(
    focus_probe: FocusProbe,
    tmp_path,
    run_number: int,
) -> None:
    require_complex_focus()
    root = artifact_root(tmp_path)
    scratch_dir = root / "scratch" / f"security-terminal-recovery-{run_number}"
    data_dir = scratch_dir / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "allowed.txt").write_text("allowed\n", encoding="utf-8")
    (scratch_dir / "secret.txt").write_text("secret\n", encoding="utf-8")
    (scratch_dir / "safe_reader.py").write_text(
        "from pathlib import Path\n\n"
        "DATA_ROOT = Path(__file__).parent / 'data'\n\n"
        "def read_document(name: str) -> str:\n"
        "    return (DATA_ROOT / name).read_text(encoding='utf-8')\n",
        encoding="utf-8",
    )
    (scratch_dir / "test_safe_reader.py").write_text(
        "import pytest\n\n"
        "from safe_reader import read_document\n\n"
        "def test_valid_document() -> None:\n"
        "    assert read_document('allowed.txt') == 'allowed\\n'\n\n"
        "def test_path_traversal_is_rejected() -> None:\n"
        "    with pytest.raises(ValueError):\n"
        "        read_document('../secret.txt')\n",
        encoding="utf-8",
    )
    scenario = FocusScenario(
        scenario_id=f"security-terminal-recovery-{run_number}",
        prompt=(
            "Audit only the supplied local fixture for path traversal. Do not scan "
            "or probe any external target. You may use at most four authoritative "
            "documentation search or fetch calls total. Use file tools to inspect and "
            "make the smallest fix that rejects traversal while preserving valid "
            "reads. You must then use exec.run to run `python -m pytest -q`. Do not "
            "stop after editing and do not emit a final answer before that command "
            "passes. After it passes, your entire final answer must be exactly "
            "`SECURITY_VALIDATION_OK`."
        ),
        expected_markers=("SECURITY_VALIDATION_OK",),
        timeout=1500,
        requires_approval=True,
        max_auto_approvals=10,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
        expected_file_patterns=("safe_reader.py", "test_safe_reader.py"),
        validation_commands=(
            ("{python}", "-m", "pytest", "-q"),
            _SECURITY_READER_VALIDATION,
        ),
        max_auto_continuations=2,
    )
    active_probe = focus_probe.for_workdir(
        scratch_dir,
        include_project_context=False,
    )

    with active_probe.session(rows=50, cols=160) as session:
        active_probe.wait_ready(session)
        try:
            transcript = active_probe.run_turn(session, scenario)
        except BaseException:
            write_transcript(root, scenario.scenario_id, session.transcript)
            raise
        write_transcript(root, scenario.scenario_id, transcript)

    assert_scenario_contract(
        scenario,
        scratch_dir=scratch_dir,
        transcript=transcript,
        python_bin=focus_probe.python_bin,
    )


@pytest.mark.timeout(4260)
def test_live_focus_complex_workflow_closeout(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    require_complex_focus()
    root = artifact_root(tmp_path)
    scratch_dir = root / "scratch" / "complex-workflow-closeout"
    scratch_dir.mkdir(parents=True)
    initial_files = {
        "normalize.py": (
            "def normalize(value: str) -> str:\n"
            "    result = value.strip()\n"
            "    return result\n"
        ),
        "labels.py": ('def label(value: str) -> str:\n    return f"[{value}]"\n'),
        "test_helpers.py": (
            "from labels import label\n"
            "from normalize import normalize\n\n"
            "def test_helpers() -> None:\n"
            "    assert normalize(' value ') == 'value'\n"
            "    assert label('ok') == '[ok]'\n"
        ),
    }
    for relative_path, content in initial_files.items():
        (scratch_dir / relative_path).write_text(content, encoding="utf-8")

    scenario = FocusScenario(
        scenario_id="complex-workflow-closeout",
        prompt=(
            "Work only in the current directory. First declare a typed task plan "
            "with exactly five steps: (1) inspect all three source files, "
            "(2) write inventory and ledger, (3) make the one minimal edit, "
            "(4) run both validations, and (5) write the final summary. Use only "
            "file.read, file.write, exec.run, and the plan control tool; do not "
            "request or activate another tool. Review exactly `normalize.py`, "
            "`labels.py`, and `test_helpers.py`. Simplify only real readability "
            "overhead; `normalize.py` has one unnecessary local variable and the "
            "other two files should remain unchanged. Create `inventory.txt` with "
            "exactly those three sorted file names, one per line. Create "
            "`ledger.tsv` with the header `file\\tdisposition` and one row per "
            "inventoried file; use disposition `trim` for normalize.py and `keep` "
            "for the other two. Use file.write to make the smallest source edit, "
            "then use exec.run "
            "to run both `python -m py_compile normalize.py labels.py "
            "test_helpers.py` and `python -m pytest -q`. Do not claim success until "
            "both pass. After they pass, create `summary.md` containing these exact "
            "four nonblank lines: `changed: normalize.py`, `unchanged: labels.py, "
            "test_helpers.py`, `py_compile: pass`, and `pytest: pass`. Explicitly "
            "complete every task-plan step, then complete the plan. Your entire "
            "final answer must be exactly `CWCR_CLOSEOUT_OK`."
        ),
        expected_markers=("CWCR_CLOSEOUT_OK",),
        timeout=1200,
        requires_approval=True,
        max_auto_approvals=12,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
        min_generated_files=6,
        expected_file_patterns=(
            "normalize.py",
            "labels.py",
            "test_helpers.py",
            "inventory.txt",
            "ledger.tsv",
            "summary.md",
        ),
        validation_commands=(
            (
                "{python}",
                "-m",
                "py_compile",
                "normalize.py",
                "labels.py",
                "test_helpers.py",
            ),
            ("{python}", "-m", "pytest", "-q"),
        ),
        max_auto_continuations=4,
    )
    active_probe = focus_probe.for_workdir(
        scratch_dir,
        include_project_context=False,
    )

    with active_probe.session(rows=50, cols=160) as session:
        active_probe.wait_ready(session)
        try:
            transcript = active_probe.run_turn(session, scenario)
        except BaseException:
            write_transcript(root, scenario.scenario_id, session.transcript)
            raise
        write_transcript(root, scenario.scenario_id, transcript)

    assert_scenario_contract(
        scenario,
        scratch_dir=scratch_dir,
        transcript=transcript,
        python_bin=focus_probe.python_bin,
    )
    assert (scratch_dir / "inventory.txt").read_text(encoding="utf-8").splitlines() == [
        "labels.py",
        "normalize.py",
        "test_helpers.py",
    ]
    assert (scratch_dir / "ledger.tsv").read_text(encoding="utf-8").splitlines() == [
        "file\tdisposition",
        "labels.py\tkeep",
        "normalize.py\ttrim",
        "test_helpers.py\tkeep",
    ]
    assert (scratch_dir / "summary.md").read_text(encoding="utf-8").splitlines() == [
        "changed: normalize.py",
        "unchanged: labels.py, test_helpers.py",
        "py_compile: pass",
        "pytest: pass",
    ]
    assert (scratch_dir / "labels.py").read_text(encoding="utf-8") == initial_files[
        "labels.py"
    ]
    assert (scratch_dir / "test_helpers.py").read_text(
        encoding="utf-8"
    ) == initial_files["test_helpers.py"]
    assert "result = value.strip()" not in (scratch_dir / "normalize.py").read_text(
        encoding="utf-8"
    )
    with sqlite3.connect(
        focus_probe.data_root / "state" / "brain" / "sessions.db"
    ) as db:
        plan_events = [
            (event_type, json.loads(payload_json))
            for event_type, payload_json in db.execute(
                "SELECT event_type, payload_json FROM session_events "
                "WHERE event_type LIKE 'task_plan.%' ORDER BY seq"
            )
        ]
    declared_steps = {
        step["step_id"]
        for event_type, payload in plan_events
        if event_type == "task_plan.declared"
        for step in payload["plan"]["steps"]
    }
    completed_steps = {
        payload["step_id"]
        for event_type, payload in plan_events
        if event_type == "task_plan.step_completed"
    }
    assert declared_steps == completed_steps
    assert any(event_type == "task_plan.completed" for event_type, _ in plan_events)
