from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.runtime.policy import Policy
from openminion.tools.task.reminder_ux import (
    ReminderControlScenario,
    format_reminder_control_summary,
    run_proactive_noop_scenario,
    run_reminder_control_scenario,
)


def _ctx(tmp_path: Path) -> RuntimeContext:
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    return RuntimeContext(
        policy=Policy(
            raw={
                "workspace_root": str(workspace),
                "context_metadata": {"agent_id": "agent-daily-smoke"},
                "paths": {
                    "read_allow": [str(workspace)],
                    "write_allow": [str(workspace)],
                    "deny": [],
                },
                "tools": {"allow_prefix": [""]},
            }
        ),
        workspace=workspace,
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=False,
    )


def test_reminder_control_scenario_records_public_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    result = run_reminder_control_scenario(
        context=_ctx(tmp_path),
        scenario=ReminderControlScenario(
            instruction="remind me to check the release notes",
            name="daily-smoke-reminder",
            schedule={"kind": "every", "every_ms": 60_000},
        ),
    )

    assert result.task_id
    assert result.listed is True
    assert result.shown is True
    assert result.paused is True
    assert result.resumed is True
    assert result.cancelled is True
    assert result.final_state == "cancelled"
    assert result.task_complete_supported is False
    assert result.history_event_id
    assert result.delivery_event_id == f"hermetic-focus-delivery:{result.task_id}"
    assert result.proof_mode == "hermetic_task_lifecycle"


def test_reminder_control_summary_is_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    result = run_reminder_control_scenario(
        context=_ctx(tmp_path),
        scenario=ReminderControlScenario(
            instruction="private reminder text should not be summarized",
            name="daily-smoke-reminder",
            schedule={"kind": "every", "every_ms": 60_000},
        ),
    )
    summary = format_reminder_control_summary(result)

    assert result.task_id in summary
    assert "private reminder text" not in summary
    assert "daily-smoke-reminder" not in summary
    assert "final_state: cancelled" in summary


def test_reminder_control_scenario_rejects_blank_instruction() -> None:
    with pytest.raises(ValueError, match="instruction is required"):
        ReminderControlScenario(
            instruction=" ",
            name="daily-smoke-reminder",
            schedule={"kind": "every", "every_ms": 60_000},
        )


def test_proactive_noop_scenario_records_active_and_suppressed_cases() -> None:
    result = run_proactive_noop_scenario()

    assert result.active_result["scheduled"] is True
    assert result.active_tick_id.startswith("pae.idle_tick:")
    assert result.active_event_ids == ("evt-1",)
    assert result.no_op_result["scheduled"] is False
    assert result.no_op_result["reason"] == "disabled"
    assert result.no_op_event_ids == ("evt-1",)
    assert result.proof_mode == "hermetic_proactive_owner"
