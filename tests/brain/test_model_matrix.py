from __future__ import annotations

import json

from openminion.modules.brain.adapters.llm.model_profiles import (
    capability_profile_id_for_model_name,
)
from openminion.modules.brain.diagnostics.matrix import (
    AccessState,
    ExplicitToolState,
    ImplicitSkillSelectionState,
    NLParityState,
    ScenarioCategory,
    SkillSmokeState,
    SupportRoster,
    SupportTier,
    certification_scenarios,
    implicit_skill_selection_state_by_id,
    implicit_skill_selection_states,
    provider_lane_support_state_by_id,
    provider_lane_support_states,
    registry_snapshot,
    support_dimensions,
    support_target_by_id,
    support_targets,
    support_tier_definitions,
)


def test_support_dimensions_match_broad_support_contract() -> None:
    assert support_dimensions() == (
        "transport-compatible",
        "decision-compatible",
        "tool-compatible",
        "multi-step-compatible",
        "skill-smoke-compatible",
    )


def test_support_tier_definitions_are_unique_and_ordered() -> None:
    tiers = support_tier_definitions()
    assert [entry.tier for entry in tiers] == [
        SupportTier.PRIMARY,
        SupportTier.SECONDARY,
        SupportTier.LIMITED,
        SupportTier.UNSUPPORTED,
    ]


def test_major_roster_matches_spec_targets() -> None:
    major_models = [
        target.model_name for target in support_targets(SupportRoster.MAJOR)
    ]
    assert major_models == [
        "anthropic/claude-haiku-4.5",
        "anthropic/claude-3-haiku",
        "openai/gpt-4o-mini",
        "openai/gpt-4o",
        "google/gemini-2.5-flash",
        "google/gemini-2.5-pro",
        "z-ai/glm-5-turbo",
        "minimax/minimax-m2.7",
        "MiniMax-M2.5",
    ]


def test_medium_roster_matches_spec_targets() -> None:
    medium_models = [
        target.model_name for target in support_targets(SupportRoster.MEDIUM)
    ]
    assert medium_models == [
        "qwen/qwen3.5-35b-a3b",
        "qwen/qwen3.5-9b",
        "qwen3.5-plus",
        "glm-5",
        "openai/gpt-5.4",
    ]


def test_unsupported_roster_remains_explicit() -> None:
    unsupported_models = [
        target.model_name for target in support_targets(SupportRoster.UNSUPPORTED)
    ]
    assert unsupported_models == [
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        "moonshotai/kimi-k2.5",
    ]


def test_targets_use_expected_goal_tier_by_roster() -> None:
    assert {target.goal_tier for target in support_targets(SupportRoster.MAJOR)} == {
        SupportTier.SECONDARY
    }
    assert {target.goal_tier for target in support_targets(SupportRoster.MEDIUM)} == {
        SupportTier.LIMITED
    }
    assert {
        target.goal_tier for target in support_targets(SupportRoster.UNSUPPORTED)
    } == {SupportTier.UNSUPPORTED}


def test_certification_scenarios_are_stable_by_category() -> None:
    assert [entry.scenario_id for entry in certification_scenarios()] == [
        "time_now",
        "weather_now",
        "fetch_example",
        "search_news",
        "search_and_time",
        "weather_two_cities",
        "api_json_fetch",
        "api_multi_fetch",
        "linear_skill_smoke",
        "builder_skill_smoke",
        "research_skill_smoke",
    ]
    assert [
        entry.scenario_id
        for entry in certification_scenarios(ScenarioCategory.CORE_TOOL)
    ] == ["time_now", "weather_now", "fetch_example", "search_news"]
    assert [
        entry.scenario_id
        for entry in certification_scenarios(ScenarioCategory.ADVANCED_RUNTIME)
    ] == [
        "search_and_time",
        "weather_two_cities",
        "api_json_fetch",
        "api_multi_fetch",
    ]
    assert [
        entry.scenario_id
        for entry in certification_scenarios(ScenarioCategory.REAL_SKILL)
    ] == ["linear_skill_smoke", "builder_skill_smoke", "research_skill_smoke"]


def test_support_target_lookup_returns_expected_config_binding() -> None:
    target = support_target_by_id("openrouter-gpt-4o-mini")
    assert target is not None
    assert target.model_name == "openai/gpt-4o-mini"
    assert target.config_filename == "per-agent-openrouter-gpt-4o-mini.json"


def test_provider_lane_support_publication_is_truthful_for_current_alibaba_lanes() -> (
    None
):
    lane_ids = [lane.lane_id for lane in provider_lane_support_states("alibaba")]
    assert lane_ids == [
        "alibaba-minimax-m2-5",
        "alibaba-kimi-k2-5",
        "alibaba-qwen3-5-plus",
        "alibaba-glm-5",
    ]

    minimax = provider_lane_support_state_by_id("alibaba-minimax-m2-5")
    assert minimax is not None
    assert minimax.endpoint_lane == "dashscope_coding_intl"
    assert minimax.access_state == AccessState.ACCESS_READY
    assert minimax.explicit_tool_state == ExplicitToolState.HEALTHY
    assert minimax.nl_parity_state == NLParityState.GAPPED
    assert minimax.skill_smoke_state == SkillSmokeState.HEALTHY

    qwen = provider_lane_support_state_by_id("alibaba-qwen3-5-plus")
    assert qwen is not None
    assert qwen.endpoint_lane == "dashscope_coding_intl"
    assert qwen.access_state == AccessState.ACCESS_READY
    assert qwen.explicit_tool_state == ExplicitToolState.HEALTHY
    assert qwen.nl_parity_state == NLParityState.GAPPED

    glm = provider_lane_support_state_by_id("alibaba-glm-5")
    assert glm is not None
    assert glm.endpoint_lane == "dashscope_coding_intl"
    assert glm.access_state == AccessState.ACCESS_READY
    assert glm.explicit_tool_state == ExplicitToolState.HEALTHY
    assert glm.nl_parity_state == NLParityState.GAPPED
    assert glm.skill_smoke_state == SkillSmokeState.UNVERIFIED

    kimi = provider_lane_support_state_by_id("alibaba-kimi-k2-5")
    assert kimi is not None
    assert kimi.access_state == AccessState.ACCESS_READY
    assert kimi.explicit_tool_state == ExplicitToolState.HEALTHY
    assert kimi.nl_parity_state == NLParityState.HEALTHY
    assert kimi.skill_smoke_state == SkillSmokeState.UNVERIFIED


def test_provider_lane_capability_profile_ids_resolve_from_capability_owner() -> None:
    for lane in provider_lane_support_states("alibaba"):
        assert lane.capability_profile_id == capability_profile_id_for_model_name(
            model_name=lane.model_name
        )


def test_implicit_skill_selection_publication_is_truthful_for_official_minimax() -> (
    None
):
    lane_ids = [
        lane.lane_id for lane in implicit_skill_selection_states("official-minimax")
    ]
    assert lane_ids == [
        "official-minimax-m2-7",
        "official-minimax-m2-5",
    ]

    m27 = implicit_skill_selection_state_by_id("official-minimax-m2-7")
    assert m27 is not None
    assert m27.endpoint_lane == "api_minimax_global"
    assert m27.explicit_baseline_pass_count == 20
    assert m27.explicit_baseline_scenario_count == 20
    assert m27.implicit_pass_count == 20
    assert m27.implicit_scenario_count == 20
    assert m27.certification_state == ImplicitSkillSelectionState.CERTIFIED
    assert m27.routed_tracker == "skill-runtime-support-saturation-tracker"

    m25 = implicit_skill_selection_state_by_id("official-minimax-m2-5")
    assert m25 is not None
    assert m25.explicit_baseline_pass_count == 20
    assert m25.explicit_baseline_scenario_count == 20
    assert m25.implicit_pass_count == 20
    assert m25.implicit_scenario_count == 20
    assert m25.certification_state == ImplicitSkillSelectionState.CERTIFIED


def test_registry_snapshot_is_json_serializable() -> None:
    payload = registry_snapshot()
    encoded = json.dumps(payload, sort_keys=True)
    assert "openrouter-gpt-4o-mini" in encoded
    assert "search_news" in encoded
    assert "transport-compatible" in encoded
    assert "provider_lane_states" in encoded
    assert "alibaba-minimax-m2-5" in encoded
    assert "implicit_skill_selection_states" in encoded
    assert "official-minimax-m2-7" in encoded


def test_registry_snapshot_does_not_embed_generated_artifact_paths() -> None:
    encoded = json.dumps(registry_snapshot(), sort_keys=True)
    assert "evidence_refs" not in encoded
    assert "artifacts/cli-chat-e2e" not in encoded
    assert ".openminion/runtime" not in encoded
