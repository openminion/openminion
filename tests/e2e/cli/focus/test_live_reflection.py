from __future__ import annotations

import json

import pytest

from tests.e2e.cli.focus.conftest import require_complex_focus
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.assertions import (
    current_turn_events,
    read_focus_evidence,
)
from tests.e2e.cli.focus.harness.scenarios import (
    FocusScenario,
    assert_scenario_contract,
)

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(600)]


def _current_turn_evidence(probe: FocusProbe, previous_ids: set[str]):
    all_events, _, _ = read_focus_evidence(probe.environment(), probe.session_id)
    events, _ = current_turn_events(all_events, previous_ids)
    config = json.loads(probe.config_path.read_text(encoding="utf-8"))
    assert config["runtime"]["session_context_token_budget"] <= 32_000
    phase_timings = [
        event.data
        for event in events
        if event.event_type == "chat.phase_timing"
    ]
    assert len(phase_timings) == 1
    assert int(phase_timings[0]["provider_calls_total"]) <= 24
    input_tokens = int(phase_timings[0]["provider_input_tokens"])
    output_tokens = int(phase_timings[0]["provider_output_tokens"])
    assert input_tokens > 0
    assert output_tokens > 0
    assert input_tokens + output_tokens <= 32_000
    assert sum(event.event_type == "tool.call.requested" for event in events) <= 32
    return events


@pytest.mark.parametrize("run_number", (1, 2))
def test_live_focus_reflection_clean_turn(
    focus_probe: FocusProbe,
    tmp_path,
    run_number: int,
) -> None:
    require_complex_focus()
    root = artifact_root(tmp_path)
    case_id = f"reflection-clean-{run_number}"
    scenario = FocusScenario(
        scenario_id=case_id,
        prompt="What is 2 + 2?",
        expected_markers=("4",),
        timeout=300,
        include_project_context=False,
    )
    probe = focus_probe.for_workdir(tmp_path, include_project_context=False)

    with probe.session(rows=50, cols=160) as session:
        probe.wait_ready(session)
        previous_events, _, _ = read_focus_evidence(
            probe.environment(), probe.session_id
        )
        previous_ids = {event.event_id for event in previous_events}
        try:
            probe.run_turn(session, scenario)
        finally:
            write_transcript(root, case_id, session.visible_transcript)

    events = _current_turn_evidence(probe, previous_ids)
    attempted_reflections = max(
        (
            int(event.data.get("loop.reflection_attempted_calls", 0) or 0)
            for event in events
            if event.event_type == "brain.execution_status"
        ),
        default=0,
    )
    assert attempted_reflections == 0


@pytest.mark.parametrize("failures", (1, 2), ids=("read-failure", "dedicated"))
@pytest.mark.parametrize("run_number", (1, 2))
def test_live_focus_reflection_recovery(
    focus_probe: FocusProbe,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    failures: int,
    run_number: int,
) -> None:
    require_complex_focus()
    root = artifact_root(tmp_path)
    case_id = f"reflection-{failures}-{run_number}"
    scratch_dir = root / "scratch" / case_id
    scratch_dir.mkdir(parents=True)
    (scratch_dir / "input.txt").write_text("reflection-ok\n", encoding="utf-8")
    injection_root = scratch_dir / "injection"
    injection_root.mkdir()
    (injection_root / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        "from openminion.modules.tool.errors import ToolRuntimeError\n"
        "from openminion.tools.file.backends.local import LocalStorageBackend\n\n"
        f"target = Path({str(scratch_dir / 'input.txt')!r}).resolve()\n"
        f"counter = Path({str(scratch_dir / '.read-attempts')!r})\n"
        "original_read = LocalStorageBackend.read\n\n"
        "original_read_range = LocalStorageBackend.read_range\n\n"
        "def fail_transiently(path):\n"
        "    if Path(path).resolve() != target:\n"
        "        return\n"
        "    attempt = int(counter.read_text() if counter.exists() else '0') + 1\n"
        "    counter.write_text(str(attempt))\n"
        f"    if attempt <= {failures}:\n"
        "        raise ToolRuntimeError('TIMEOUT', 'transient test read timeout')\n\n"
        "def read(self, path, *, max_chars=12000, offset=0):\n"
        "    fail_transiently(path)\n"
        "    return original_read(self, path, max_chars=max_chars, offset=offset)\n\n"
        "def read_range(self, path, *, start_line, end_line, max_chars):\n"
        "    fail_transiently(path)\n"
        "    return original_read_range(\n"
        "        self, path, start_line=start_line, end_line=end_line, "
        "max_chars=max_chars\n"
        "    )\n\n"
        "LocalStorageBackend.read = read\n"
        "LocalStorageBackend.read_range = read_range\n",
        encoding="utf-8",
    )
    scenario = FocusScenario(
        scenario_id=case_id,
        prompt=(
            "Read input.txt and report its exact contents."
            if failures == 1
            else (
                "Read input.txt. If the read fails, retry the identical read once "
                "before changing approach. Then report the exact contents."
            )
        ),
        expected_markers=("reflection-ok",),
        timeout=300,
        use_scratch_workspace=True,
        include_project_context=False,
    )
    probe = focus_probe.for_workdir(scratch_dir, include_project_context=False)
    base_environment = probe.environment

    def environment() -> dict[str, str]:
        values = base_environment()
        values["PYTHONPATH"] = f"src:{injection_root}"
        return values

    monkeypatch.setattr(probe, "environment", environment)

    with probe.session(rows=50, cols=160) as session:
        probe.wait_ready(session)
        previous_events, _, _ = read_focus_evidence(
            probe.environment(), probe.session_id
        )
        previous_ids = {event.event_id for event in previous_events}
        try:
            probe.run_turn(session, scenario)
        finally:
            write_transcript(root, case_id, session.visible_transcript)

    attempts = int((scratch_dir / ".read-attempts").read_text(encoding="utf-8"))
    assert attempts == failures + 1
    events = _current_turn_evidence(probe, previous_ids)
    attempted_reflections = max(
        (
            int(event.data.get("loop.reflection_attempted_calls", 0) or 0)
            for event in events
            if event.event_type == "brain.execution_status"
        ),
        default=0,
    )
    assert attempted_reflections == (1 if failures == 2 else 0)


@pytest.mark.parametrize("run_number", (1, 2))
def test_live_focus_reflection_coding_repair(
    focus_probe: FocusProbe,
    tmp_path,
    run_number: int,
) -> None:
    require_complex_focus()
    root = artifact_root(tmp_path)
    case_id = f"reflection-coding-{run_number}"
    scratch_dir = root / "scratch" / case_id
    scratch_dir.mkdir(parents=True)
    (scratch_dir / "slug.py").write_text(
        "def slug(value: str) -> str:\n    return value\n",
        encoding="utf-8",
    )
    test_path = scratch_dir / "test_slug.py"
    original_test = (
        "from pathlib import Path\n\n"
        "from slug import slug\n\n\n"
        "def test_slug_spaces() -> None:\n"
        "    result = slug(\"A B\")\n"
        "    if result != \"a-b\":\n"
        "        Path(\".initial-failure-observed\").write_text(result)\n"
        "    assert result == \"a-b\"\n"
    )
    test_path.write_text(original_test, encoding="utf-8")
    scenario = FocusScenario(
        scenario_id=case_id,
        prompt=(
            'Run the unchanged tests first and retain the failure. Then fix slug.py '
            'so slug("A B") returns "a-b". Rerun the tests, keep tests unchanged, '
            "and report the result."
        ),
        expected_markers=("pass|passed",),
        timeout=300,
        requires_approval=True,
        max_auto_approvals=8,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
        expected_file_patterns=("slug.py", "test_slug.py"),
        validation_commands=(("{python}", "-m", "pytest", "-q"),),
    )
    probe = focus_probe.for_workdir(scratch_dir, include_project_context=False)

    with probe.session(rows=50, cols=160) as session:
        probe.wait_ready(session)
        previous_events, _, _ = read_focus_evidence(
            probe.environment(), probe.session_id
        )
        previous_ids = {event.event_id for event in previous_events}
        try:
            transcript = probe.run_turn(session, scenario)
        finally:
            write_transcript(root, case_id, session.visible_transcript)

    assert (scratch_dir / ".initial-failure-observed").read_text() == "A B"
    assert test_path.read_text(encoding="utf-8") == original_test
    assert_scenario_contract(
        scenario,
        scratch_dir=scratch_dir,
        transcript=transcript,
        python_bin=focus_probe.python_bin,
    )
    _current_turn_evidence(probe, previous_ids)
