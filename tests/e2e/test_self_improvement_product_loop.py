from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.modules.brain.loop.adaptive.modes import ActLoopMode
from openminion.modules.brain.loop.constants import WORKFLOW_OBSERVATION_ENABLED_KEY
from openminion.modules.brain.loop.tools import (
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopState,
)
from openminion.modules.brain.schemas import BudgetCounters, StepOutput, WorkingState
from openminion.modules.brain.schemas.closure import ClosureJudgment
from openminion.modules.skill.cli import main as skill_cli_main
from openminion.modules.skill.runtime.skill import Skill

pytestmark = pytest.mark.e2e


def _config(tmp_path: Path) -> dict[str, object]:
    data_root = tmp_path / ".openminion"
    return {
        "skill": {
            "sqlite_path": str(data_root / "skill.db"),
            "blob_root": str(data_root / "blob"),
            "fallback_root": str(data_root / "fallback"),
            "wal": False,
            "runtime_workflow_observation_enabled": True,
        }
    }


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "skill.json"
    path.write_text(json.dumps(_config(tmp_path)), encoding="utf-8")
    return path


def _run_cli(config_path: Path, command: str) -> dict[str, object]:
    output = io.StringIO()
    with redirect_stdout(output):
        assert (
            skill_cli_main(
                [
                    "--config",
                    str(config_path),
                    command,
                    "--agent-id",
                    "agent-a",
                ]
            )
            == 0
        )
    return json.loads(output.getvalue())


def _outcome(*, intent_category: str = "modify") -> AdaptiveToolLoopOutcome:
    return AdaptiveToolLoopOutcome(
        profile_name="general_adaptive_v1",
        mode_name="act_adaptive",
        termination_reason="final_text",
        state=AdaptiveToolLoopState(
            scratchpad={
                WORKFLOW_OBSERVATION_ENABLED_KEY: True,
                "adaptive.tool_results": [
                    {"tool_name": "file.write", "ok": True},
                    {"tool_name": "exec.run", "ok": True},
                ],
            }
        ),
        allowed_tools=frozenset({"file.write", "exec.run"}),
        workflow_learning={
            "intent_category": intent_category,
            "capability_category": "code",
        },
        finalization_status={
            "status": "final_answer",
            "reasoning": "workflow completed",
        },
        final_text="Completed the requested update.",
    )


def _finalize(
    skill: Skill,
    *,
    agent_id: str,
    trace_id: str,
    outcome: AdaptiveToolLoopOutcome,
) -> dict[str, object]:
    state = WorkingState(
        session_id=f"session-{agent_id}",
        agent_id=agent_id,
        trace_id=trace_id,
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=10,
            a2a_calls=0,
            tokens=1_000,
            time_ms=60_000,
        ),
    )

    def respond(**kwargs: object) -> StepOutput:
        return StepOutput(
            session_id=state.session_id,
            status=str(kwargs["status"]),
            message=str(kwargs["message"]),
            working_state=state,
            action_result=kwargs.get("action_result"),
        )

    ctx = SimpleNamespace(
        state=state,
        decision=SimpleNamespace(route="act_adaptive"),
        user_input="Complete the requested update.",
        options=SimpleNamespace(profile=None),
        llm_adapter=SimpleNamespace(client=None),
        _services=SimpleNamespace(
            runner=SimpleNamespace(
                skill_api=skill,
                session_api=None,
                memory_api=None,
            )
        ),
        evaluate_turn_closure=lambda **_kwargs: ClosureJudgment(
            satisfied=True,
            next_action="close",
        ),
        apply_closure_judgment=lambda **_kwargs: "close",
        emit_status=lambda **_kwargs: None,
        respond=respond,
    )
    result = ActLoopMode()._finalize_success(ctx, loop_outcome=outcome)
    assert result.action_result is not None
    return dict(result.action_result.outputs)


def test_opt_in_closed_runs_derive_read_only_authoring_readiness(
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path)
    skill = Skill(config_path)
    try:
        statuses: list[str] = []
        for trace_id in ("trace-1", "trace-2", "trace-3"):
            telemetry = _finalize(
                skill,
                agent_id="agent-a",
                trace_id=trace_id,
                outcome=_outcome(),
            )
            statuses.append(str(telemetry["workflow_learning.status"]))

        duplicate_telemetry = _finalize(
            skill,
            agent_id="agent-a",
            trace_id="trace-1",
            outcome=_outcome(),
        )

        conflict_telemetry = _finalize(
            skill,
            agent_id="agent-a",
            trace_id="trace-1",
            outcome=_outcome(intent_category="verify"),
        )

        other_agent_telemetry = _finalize(
            skill,
            agent_id="agent-b",
            trace_id="trace-1",
            outcome=_outcome(),
        )

        observations = skill.list_workflow_observations(agent_id="agent-a")
        shapes = skill.list_workflow_shapes(agent_id="agent-a")

        assert statuses == ["observed", "authoring_ready", "authoring_ready"]
        assert duplicate_telemetry["workflow_learning.status"] == "duplicate"
        assert conflict_telemetry["workflow_learning.status"] == "conflict"
        assert other_agent_telemetry["workflow_learning.status"] == "observed"
        assert len(observations) == 3
        assert len(shapes) == 1
        assert shapes[0].success_count == 3
        assert len(skill.list_workflow_observations(agent_id="agent-b")) == 1
        assert skill.list_skills({}) == []
        assert skill.store.list_proposals(queue_state=None, limit=50) == []
        assert (
            skill.store._record_store.query_dicts("SELECT * FROM skill_versions") == []
        )
        assert (
            skill.store._record_store.query_dicts(
                "SELECT * FROM skill_version_admissions"
            )
            == []
        )
    finally:
        skill.close()

    reopened = Skill(config_path)
    try:
        assert len(reopened.list_workflow_observations(agent_id="agent-a")) == 3
        assert reopened.list_workflow_shapes(agent_id="agent-a")[0].success_count == 3
    finally:
        reopened.close()

    observations_output = _run_cli(config_path, "learning-observation-list")
    shapes_output = _run_cli(config_path, "learning-shape-list")
    assert len(observations_output["observations"]) == 3
    assert shapes_output["shapes"][0]["state"] == "authoring_ready"
    assert shapes_output["shapes"][0]["success_count"] == 3
