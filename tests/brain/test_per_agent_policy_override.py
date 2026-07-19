from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openminion.base.config import ActionPolicyConfig, OpenMinionConfig
from openminion.modules.brain.config import BrainConfig
from openminion.modules.brain.schemas import BudgetCounters, WorkingState
from openminion.modules.brain.schemas.agent import AgentProfile
from openminion.modules.policy.adapters.brain import PolicyCtlBrainAdapter
from openminion.modules.tool import build_default_tool_registry
from openminion.services.brain.service import BrainBridgeService
from openminion.services.runtime.plugins import PluginRegistry


def _working_state(*, session_mode_override: str | None = None) -> WorkingState:
    return WorkingState(
        session_id="sess-1",
        agent_id="agent-1",
        budgets_remaining=BudgetCounters(
            ticks=4,
            tool_calls=4,
            a2a_calls=0,
            tokens=1000,
            time_ms=60000,
        ),
        session_action_policy_mode_override=session_mode_override,
    )


def _tool_command(*, tool_name: str = "file.read", risk_level: str = "low"):
    return SimpleNamespace(
        kind="tool",
        tool_name=tool_name,
        args={"path": "README.md"},
        risk_level=risk_level,
        idempotency_key="idem-1",
    )


def _allow_decision():
    return SimpleNamespace(
        decision="ALLOW",
        reason_code="ALLOW_TEST",
        reason="allowed",
        details={},
        clarification_question=None,
    )


def test_bridge_wiring_passes_resolved_agent_action_policy_to_policy_adapter() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "agents": {
                "openminion": {
                    "name": "openminion",
                    "provider": "echo",
                    "action_policy": {"mode": "bypass"},
                },
            },
            "default_agent": "openminion",
            "action_policy": {"mode": "ask"},
        }
    )
    plugins = PluginRegistry()
    provider = SimpleNamespace()
    logger = logging.getLogger("per-agent-policy-bridge")
    captured_policy_kwargs: dict[str, object] = {}

    class _CaptureRunnerCtor:
        def __init__(self, **kwargs) -> None:
            self.policy_api = kwargs["policy_api"]
            # bootstrap reads `runner.task_manager` to wire the
            # checkpoint manager into the long-running goal runtime.
            self.task_manager = kwargs.get("task_manager")
            self.profile = SimpleNamespace(
                budgets=SimpleNamespace(
                    max_ticks_per_user_turn=40,
                    max_tool_calls=16,
                    max_a2a_calls=5,
                    max_total_llm_tokens=100000,
                    max_elapsed_ms=120000,
                )
            )

    with (
        patch(
            "openminion.services.brain.service.create_session_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_context_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_tool_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_a2a_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_memory_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_policy_api",
            side_effect=lambda **kwargs: (
                captured_policy_kwargs.update(kwargs) or SimpleNamespace()
            ),
        ),
        patch(
            "openminion.services.brain.service.create_safety_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.init_rlm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_compress_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_skill_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.init_retrieve_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_llm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.factory.vector.init_vector_adapter",
            return_value=(None, None),
        ),
        patch(
            "openminion.services.brain.service.BrainBridgeService._validate_adapter_contracts",
            return_value=None,
        ),
        patch(
            "openminion.services.brain.service.BrainBridgeService._validate_runner_contract",
            return_value=None,
        ),
        patch(
            "openminion.services.brain.service.BrainRunner",
            _CaptureRunnerCtor,
        ),
    ):
        service = BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=provider,
            logger=logger,
            tools=build_default_tool_registry(),
            security_policy=None,
            self_improvement=None,
            mode="auto",
            db_path="/tmp/per-agent-policy.db",
        )
        service._get_runner()

    assert captured_policy_kwargs["action_policy_config"].mode == "bypass"


def test_bridge_profile_preserves_brain_config_profile_fields() -> None:

    config = OpenMinionConfig.from_dict(
        {
            "agents": {
                "openminion": {
                    "name": "openminion",
                    "provider": "echo",
                },
            },
            "default_agent": "openminion",
            "brain": {
                "tool_policy": "review-only",
                "memory_read_scopes": ["agent:openminion", "session:test"],
                "memory_write_scopes": {"facts": "agent:openminion"},
                "max_skills_per_session": 3,
                "outcome_attribution": {
                    "enabled": True,
                    "success_feedback_delta": 0.04,
                    "failure_feedback_delta": -0.08,
                    "max_memory_refs_per_command": 9,
                    "include_procedure_refs": False,
                },
                "success_memory": {
                    "enabled": True,
                    "max_items_per_turn": 4,
                    "min_item_confidence": 0.62,
                },
                "auto_fact_extraction": {
                    "enabled": True,
                    "model_tier": "reflect",
                    "max_items_per_turn": 7,
                    "min_user_message_chars": 5,
                    "initial_confidence": 0.44,
                },
                "proactive_autonomous_entrypoint": {
                    "enabled": True,
                    "interval_seconds": 60,
                    "user_activity_grace_seconds": 0,
                    "max_consecutive_noops": 2,
                },
            },
        }
    )
    plugins = PluginRegistry()
    provider = SimpleNamespace()
    logger = logging.getLogger("bridge-profile-config-roundtrip")
    captured_runner_kwargs: dict[str, object] = {}

    class _CaptureRunnerCtor:
        def __init__(self, **kwargs) -> None:
            captured_runner_kwargs.update(kwargs)
            self.profile = kwargs["profile"]
            self.options = kwargs["options"]
            # bootstrap reads `runner.task_manager` to wire the
            # checkpoint manager into the long-running goal runtime.
            self.task_manager = kwargs.get("task_manager")

    with (
        patch(
            "openminion.services.brain.service.create_session_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_context_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_tool_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_a2a_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_memory_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_policy_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_safety_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.init_rlm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_compress_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_skill_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.init_retrieve_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_llm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.factory.vector.init_vector_adapter",
            return_value=(None, None),
        ),
        patch(
            "openminion.services.brain.service.BrainBridgeService._validate_adapter_contracts",
            return_value=None,
        ),
        patch(
            "openminion.services.brain.service.BrainBridgeService._validate_runner_contract",
            return_value=None,
        ),
        patch(
            "openminion.services.brain.service.BrainRunner",
            _CaptureRunnerCtor,
        ),
    ):
        service = BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=provider,
            logger=logger,
            tools=build_default_tool_registry(),
            security_policy=None,
            self_improvement=None,
            mode="auto",
            db_path="/tmp/bridge-profile-config-roundtrip.db",
        )
        expected_brain_config = service._resolve_brain_config()
        service._get_runner()

    assert expected_brain_config is not None
    profile = captured_runner_kwargs["profile"]
    assert isinstance(profile, AgentProfile)
    assert isinstance(expected_brain_config, BrainConfig)

    bridge_derived_fields = {
        "agent_id",
        "role",
        "thinking",
        "llm_profiles",
        "budgets",
        "default_act_profile",
        "skill",
        "skill_catalog",
        "model_capability_overrides",
    }
    roundtrip_fields = (
        set(BrainConfig.model_fields)
        & set(AgentProfile.model_fields) - bridge_derived_fields
    )

    assert roundtrip_fields
    for field_name in sorted(roundtrip_fields):
        expected = getattr(expected_brain_config, field_name)
        actual = getattr(profile, field_name)
        if hasattr(expected, "model_dump"):
            expected = expected.model_dump(mode="python")
        if hasattr(actual, "model_dump"):
            actual = actual.model_dump(mode="python")
        assert actual == expected, field_name


def test_adapter_bypass_short_circuits_without_policyctl_call(caplog) -> None:
    ctl = Mock()
    adapter = PolicyCtlBrainAdapter(
        ctl,
        action_policy_config=ActionPolicyConfig(
            mode="bypass",
            default_action="require_confirm",
            allow_read_only_without_prompt=True,
            rules=[],
            affirmative_tokens=["yes"],
            negative_tokens=["no"],
        ),
    )

    with caplog.at_level(logging.INFO):
        decision = adapter.evaluate(
            command=_tool_command(),
            working_state=_working_state(),
            session_context={},
        )

    assert decision.outcome == "ALLOW"
    ctl.check.assert_not_called()
    assert "policy.adapter.bypass" in caplog.text


def test_adapter_passes_per_agent_policy_overrides_to_policyctl() -> None:
    ctl = Mock()
    ctl.check.return_value = _allow_decision()
    adapter = PolicyCtlBrainAdapter(
        ctl,
        action_policy_config=ActionPolicyConfig(
            mode="ask",
            default_action="allow",
            allow_read_only_without_prompt=False,
            rules=[],
            affirmative_tokens=["ship it"],
            negative_tokens=["skip it"],
        ),
    )

    decision = adapter.evaluate(
        command=_tool_command(),
        working_state=_working_state(),
        session_context={},
    )

    assert decision.outcome == "ALLOW"
    config_overrides = ctl.check.call_args.kwargs["config_overrides"]
    assert config_overrides.mode == "enforce"
    assert config_overrides.default_action == "allow"
    assert config_overrides.allow_read_only_without_prompt is False


def test_adapter_session_override_preserves_non_mode_fields() -> None:
    ctl = Mock()
    ctl.check.return_value = _allow_decision()
    adapter = PolicyCtlBrainAdapter(
        ctl,
        action_policy_config=ActionPolicyConfig(
            mode="ask",
            default_action="allow",
            allow_read_only_without_prompt=False,
            rules=[],
            affirmative_tokens=["yes"],
            negative_tokens=["no"],
        ),
    )

    adapter.evaluate(
        command=_tool_command(),
        working_state=_working_state(session_mode_override="auto"),
        session_context={},
    )

    config_overrides = ctl.check.call_args.kwargs["config_overrides"]
    assert config_overrides.mode == "enforce_safe"
    assert config_overrides.default_action == "allow"
    assert config_overrides.allow_read_only_without_prompt is False


def test_adapter_with_no_override_keeps_existing_behavior() -> None:
    ctl = Mock()
    ctl.check.return_value = _allow_decision()
    adapter = PolicyCtlBrainAdapter(ctl)

    adapter.evaluate(
        command=_tool_command(),
        working_state=_working_state(),
        session_context={},
    )

    assert "config_overrides" not in ctl.check.call_args.kwargs


def test_working_state_exposes_session_action_policy_mode_override_field() -> None:
    state = _working_state()
    assert state.session_action_policy_mode_override is None
