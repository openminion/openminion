from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.bootstrap.route_catalog import get_route_descriptor
from openminion.modules.brain.loop.tools import build_loop_thinking_metadata
from openminion.modules.brain.runtime.context import build_context
from openminion.modules.brain.schemas import OutcomeAttributionConfig
from openminion.modules.brain.runtime.reasoning import (
    ModeThinkingPolicy,
    ThinkingRequest,
    ThinkingResolutionInput,
    resolve_mode_aware_thinking,
)


class _Logger:
    def emit(self, event_type: str, payload: dict, **kwargs) -> None:
        del event_type, payload, kwargs


class _ContextAPI:
    def __init__(self) -> None:
        self.last_kwargs = {}

    def build(self, **kwargs):
        self.last_kwargs = dict(kwargs)
        return {
            "llm_call_id": "llm-1",
            "pack_version": "pack-1",
            "context_manifest": {
                "memory": [],
                "facts": [],
                "procedures": [],
                "segment_ids": [],
            },
            "budget_report": {},
        }


class _Runner:
    def __init__(self, *, thinking: str) -> None:
        self.context_api = _ContextAPI()
        self.profile = SimpleNamespace(
            thinking=thinking,
            llm_profiles=SimpleNamespace(
                act_model="MiniMax-M2.5",
                reflect_model="MiniMax-M2.5",
            ),
        )
        self.llm_api = SimpleNamespace(name="openai")
        self.options = SimpleNamespace(
            outcome_attribution_config=OutcomeAttributionConfig(
                max_memory_refs_per_command=3
            )
        )

    def _validate_call_order(self, llm_call_id: str, stage: str) -> dict[str, object]:
        del llm_call_id, stage
        return {"valid": True, "reason": ""}

    def _emit_brain_operation(self, **kwargs) -> bool:
        del kwargs
        return True


def _state():
    return SimpleNamespace(
        unresolved_clarify_items=[],
        clarify_responses={},
        session_id="sess-1",
        agent_id="agent-1",
        trace_id=None,
        decision_memory_refs=[],
        decision_context_pack_version=None,
        decision_context_recorded_at=None,
        active_mode_name="",
    )


def test_mode_policies_are_explicit_for_core_modes() -> None:
    respond = get_route_descriptor("respond")
    act = get_route_descriptor("act")

    assert respond is not None and respond.thinking_policy is not None
    assert act is not None and act.thinking_policy is not None
    assert respond.thinking_policy.default_reasoning_profile == "off"
    assert act.thinking_policy.allow_request_override is True


def test_mode_aware_resolver_blocks_and_clamps_request_override() -> None:
    resolved = resolve_mode_aware_thinking(
        request=ThinkingRequest(
            purpose="decide",
            requested_profile="detailed",
            provider="openai",
            model="MiniMax-M2.5",
        ),
        layers=ThinkingResolutionInput(
            code_default_profile="minimal",
            agent_profile="minimal",
        ),
        mode_policy=ModeThinkingPolicy(
            default_reasoning_profile="minimal",
            allowed_reasoning_profiles=("off", "minimal"),
            allow_request_override=True,
        ),
        mode_name="respond",
    )

    assert resolved.reasoning_profile == "minimal"
    assert resolved.source_layer == "mode_policy"
    assert "mode_policy_clamp" in resolved.degraded_reasons


def test_raw_and_structured_paths_share_mode_aware_thinking_result() -> None:
    runner = _Runner(thinking="detailed")
    logger = _Logger()
    state = _state()

    build_context(
        runner,
        state=state,
        purpose="act",
        budget={"tokens": 100},
        hints={"user_input": "inspect the repo", "mode_name": "act"},
        logger=logger,
    )

    structured_hints = runner.context_api.last_kwargs["hints"]
    raw_ctx = SimpleNamespace(
        options=SimpleNamespace(profile=runner.profile),
        decision=SimpleNamespace(mode="act"),
        state=SimpleNamespace(active_mode_name="act"),
        llm_adapter=SimpleNamespace(name="openai"),
    )
    raw_metadata = build_loop_thinking_metadata(raw_ctx, purpose="act")

    assert structured_hints["thinking_effective_profile"] == "detailed"
    assert raw_metadata["thinking_reasoning_profile"] == "detailed"
    assert raw_metadata["thinking_source_layer"] == "agent_runtime"
    assert raw_metadata["thinking_mode_name"] == "act"
