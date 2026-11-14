from __future__ import annotations

import pytest

from tests.e2e.cli.focus.conftest import require_complex_focus
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.assertions import (
    current_turn_events,
    read_focus_evidence,
)
from tests.e2e.cli.focus.harness.scenarios import FocusScenario

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(600)]


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
        "def read(self, path, *, max_chars=12000, offset=0):\n"
        "    if Path(path).resolve() == target:\n"
        "        attempt = int(counter.read_text() if counter.exists() else '0') + 1\n"
        "        counter.write_text(str(attempt))\n"
        f"        if attempt <= {failures}:\n"
        "            raise ToolRuntimeError('TIMEOUT', 'transient test read timeout')\n"
        "    return original_read(self, path, max_chars=max_chars, offset=offset)\n\n"
        "LocalStorageBackend.read = read\n",
        encoding="utf-8",
    )
    scenario = FocusScenario(
        scenario_id=case_id,
        prompt=(
            "Work only in the current directory. Use file.read with path `input.txt`. "
            "If it returns a transient timeout, retry that identical file.read until "
            "it succeeds, with at most three total attempts. Do not use another tool "
            "or change the path. After success, reply exactly `reflection-ok`."
        ),
        expected_markers=("reflection-ok",),
        timeout=480,
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
    all_events, _, _ = read_focus_evidence(probe.environment(), probe.session_id)
    events, _ = current_turn_events(all_events, previous_ids)
    attempted_reflections = max(
        (
            int(event.data.get("loop.reflection_attempted_calls", 0) or 0)
            for event in events
            if event.event_type == "brain.execution_status"
        ),
        default=0,
    )
    assert attempted_reflections == (1 if failures == 2 else 0)
