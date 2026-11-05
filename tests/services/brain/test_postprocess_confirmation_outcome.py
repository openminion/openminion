"""A denial recorded on the response reaches the turn metadata once, unparsed."""

from __future__ import annotations

import json
from types import SimpleNamespace

from openminion.services.brain.post_execution import postprocess_response as mod


class _StubService:
    _telemetryctl = None

    def _build_turn_response_metadata(self, **kwargs):
        return {"brain_status": "waiting_user"}

    def _identity_metadata(self):
        return {}

    def _extract_memory_policy_metadata(self, *, response_text: str):
        return {}

    def _build_clarify_request_payload(self, **kwargs):
        return None

    def _attach_clarify_request_metadata(self, **kwargs):
        return None

    def _attach_tool_result_metadata(self, **kwargs):
        return None


def _step_out(*, confirmation_outcome):
    return SimpleNamespace(
        status="waiting_user",
        kind="assistant",
        message="Confirmation denied. The action was not run.",
        action_result=None,
        confirmation_outcome=confirmation_outcome,
        working_state=SimpleNamespace(pending_confirmation_command=None),
    )


def _metadata(step_out):
    return mod._build_postprocess_response_metadata(
        _StubService(),
        runner=SimpleNamespace(),
        step_out=step_out,
        session_id="s",
        request_id="t",
        elapsed_ms=1.0,
        llm_steps=1,
        termination_reason="model_final",
        tool_results_payload=[],
        finalization_payload=None,
        response_text="Confirmation denied. The action was not run.",
        is_pae_idle_tick_noop=False,
    )


def test_denial_fact_is_copied_as_structured_metadata() -> None:
    step_out = _step_out(
        confirmation_outcome={"result": "denied", "command_id": "cmd-3", "cursor": "1"}
    )

    metadata = _metadata(step_out)

    assert json.loads(metadata["confirmation_outcome"]) == {
        "result": "denied",
        "command_id": "cmd-3",
        "cursor": "1",
    }


def test_response_without_a_denial_carries_no_confirmation_field() -> None:
    metadata = _metadata(_step_out(confirmation_outcome=None))

    assert "confirmation_outcome" not in metadata
