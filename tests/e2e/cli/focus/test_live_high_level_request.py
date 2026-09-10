from __future__ import annotations

from unittest.mock import patch

import pytest

from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import ToolAdapter
from openminion.modules.brain.config import RunnerOptions
from openminion.modules.brain.diagnostics.status import (
    format_phase_status_text,
    phase_status_from_request_readiness,
)
from openminion.modules.brain.runner import BrainRunner
from openminion.modules.brain.schemas import (
    RequestReadiness,
    RespondDecision,
    ToolCommand,
)
from tests.brain.runner_test_support import _profile, build_seeded_act_decision

pytestmark = pytest.mark.e2e


def _runner(
    tmp_path, *, request_handoff_enabled: bool, permission_mode: str = "readonly"
) -> BrainRunner:
    sessions = LocalSessionStore(tmp_path / "sessions")
    runner = BrainRunner(
        profile=_profile(),
        session_api=sessions,
        context_api=LocalContextAdapter(session_store=sessions),
        llm_api=None,
        tool_api=ToolAdapter(workspace_root=tmp_path),
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(tmp_path / "memory"),
        policy_api=LocalPolicyAdapter(),
        options=RunnerOptions(
            metactl_enabled=False,
            request_handoff_enabled=request_handoff_enabled,
        ),
    )
    runner._pending_permission_mode = permission_mode
    return runner


def _write_decision(
    path,
    *,
    state: str,
    step: str = "Write the reviewed file",
    requested_outcome: str = "execute",
):
    return build_seeded_act_decision(
        command=ToolCommand(
            title="Write reviewed file",
            tool_name="file.write",
            args={"path": str(path), "content": "changed"},
        ),
        request_readiness={
            "posture": "review_before_act"
            if state == "needs_plan_review"
            else "brief_plan",
            "requested_outcome": requested_outcome,
            "state": state,
        },
        sub_intents=[step],
    )


@pytest.mark.parametrize("request_handoff_enabled", [False, True])
def test_focus_plan_review_persists_and_can_be_interrupted(
    tmp_path, request_handoff_enabled: bool
) -> None:
    session_id = f"focus-plan-review-{request_handoff_enabled}"
    target = tmp_path / "reviewed.txt"
    first = _runner(tmp_path, request_handoff_enabled=request_handoff_enabled)
    with patch.object(
        first,
        "_decide",
        return_value=_write_decision(target, state="needs_plan_review"),
    ):
        waiting = first.step(session_id=session_id, user_input="write the file")
    first.tool_api.close()

    resumed = _runner(tmp_path, request_handoff_enabled=request_handoff_enabled)
    with patch.object(resumed, "_decide", side_effect=AssertionError("no redecision")):
        still_waiting = resumed.step(session_id=session_id, user_input=None)
    cancel = RespondDecision(
        respond_kind="answer",
        answer="Cancelled without changing the file.",
        request_readiness=RequestReadiness(
            posture="direct",
            requested_outcome="answer_only",
            state="ready",
        ),
    )
    with patch.object(resumed, "_decide", return_value=cancel):
        interrupted = resumed.step(session_id=session_id, user_input="cancel")
    resumed.tool_api.close()

    assert waiting.status == "waiting_user"
    assert still_waiting.status == "waiting_user"
    assert "1. Write the reviewed file" in still_waiting.message
    assert interrupted.status == "done"
    assert not target.exists()


def test_focus_plan_approval_preserves_readonly_permission_ceiling(tmp_path) -> None:
    session_id = "focus-plan-review-readonly"
    target = tmp_path / "reviewed.txt"
    first = _runner(tmp_path, request_handoff_enabled=True)
    with patch.object(
        first,
        "_decide",
        return_value=_write_decision(target, state="needs_plan_review"),
    ):
        waiting = first.step(session_id=session_id, user_input="write the file")
    first.tool_api.close()

    resumed = _runner(tmp_path, request_handoff_enabled=True)
    with patch.object(
        resumed,
        "_decide",
        return_value=_write_decision(target, state="ready"),
    ):
        blocked = resumed.step(session_id=session_id, user_input="approve")
    resumed.tool_api.close()

    assert waiting.status == "waiting_user"
    assert blocked.action_result is not None
    assert blocked.action_result.error is not None
    assert blocked.action_result.error.code == "PERMISSION_DENIED_READONLY"
    assert not target.exists()


def test_focus_plan_only_outcome_blocks_write_even_with_bypass(tmp_path) -> None:
    target = tmp_path / "plan-only.txt"
    runner = _runner(
        tmp_path,
        request_handoff_enabled=True,
        permission_mode="bypass",
    )
    with patch.object(
        runner,
        "_decide",
        return_value=_write_decision(
            target,
            state="ready",
            requested_outcome="plan_only",
        ),
    ):
        blocked = runner.step(
            session_id="focus-plan-only",
            user_input="plan this without changing files",
        )
    runner.tool_api.close()

    assert blocked.action_result is not None
    assert blocked.action_result.error is not None
    assert blocked.action_result.error.code == "REQUEST_OUTCOME_EFFECT_BLOCKED"
    assert not target.exists()


def test_hlpe_focus_status_matrix_is_safe_without_live_provider() -> None:
    scenarios = [
        (
            "direct_execute",
            RequestReadiness(
                posture="direct",
                requested_outcome="execute",
                state="ready",
            ),
            "executing",
        ),
        (
            "plan_review",
            RequestReadiness(
                posture="review_before_act",
                requested_outcome="execute",
                state="needs_plan_review",
            ),
            "awaiting_plan_review",
        ),
        (
            "operation_approval",
            RequestReadiness(
                posture="direct",
                requested_outcome="execute",
                state="needs_operation_approval",
            ),
            "awaiting_confirmation",
        ),
        (
            "blocked",
            RequestReadiness(
                posture="brief_plan",
                requested_outcome="execute",
                state="blocked",
            ),
            "blocked",
        ),
    ]

    rendered = {}
    for name, readiness, expected_status in scenarios:
        status = phase_status_from_request_readiness(
            trace_id=f"trace-{name}",
            readiness=readiness,
        )
        rendered[name] = format_phase_status_text(status)
        assert status.status_key == expected_status

    assert "Executing" in rendered["direct_execute"]
    assert "plan review" in rendered["plan_review"].lower()
    assert "confirmation" in rendered["operation_approval"].lower()
    assert "Blocked" in rendered["blocked"]
