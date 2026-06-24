from __future__ import annotations

from dataclasses import asdict, dataclass

from openminion.modules.brain.adapters.llm.model_profiles import (
    capability_profile_id_for_model_name,
)


class SupportRoster:
    MAJOR = "major"
    MEDIUM = "medium"
    UNSUPPORTED = "unsupported"


class SupportTier:
    PRIMARY = "Primary"
    SECONDARY = "Secondary"
    LIMITED = "Limited"
    UNSUPPORTED = "Unsupported"


class ScenarioCategory:
    CORE_TOOL = "core_tool"
    ADVANCED_RUNTIME = "advanced_runtime"
    REAL_SKILL = "real_skill"


class AccessState:
    ACCESS_READY = "access_ready"
    ACCESS_BLOCKED = "access_blocked"
    QUOTA_BLOCKED = "quota_blocked"
    TRANSPORT_BLOCKED = "transport_blocked"


class ExplicitToolState:
    HEALTHY = "explicit_tool_healthy"
    GAPPED = "explicit_tool_gapped"
    BLOCKED_BY_ACCESS = "explicit_tool_blocked_by_access"


class NLParityState:
    HEALTHY = "nl_parity_healthy"
    GAPPED = "nl_parity_gapped"
    BLOCKED_BY_ACCESS = "nl_parity_blocked_by_access"
    UNVERIFIED = "nl_parity_unverified"


class SkillSmokeState:
    HEALTHY = "skill_smoke_healthy"
    GAPPED = "skill_smoke_gapped"
    BLOCKED_BY_ACCESS = "skill_smoke_blocked_by_access"
    UNVERIFIED = "skill_smoke_unverified"


class ImplicitSkillSelectionState:
    CERTIFIED = "nl_select_certified"
    WARNING = "nl_select_warning"
    GAPPED = "nl_select_gapped"
    BLOCKED = "nl_select_blocked"


SUPPORT_DIMENSIONS: tuple[str, ...] = (
    "transport-compatible",
    "decision-compatible",
    "tool-compatible",
    "multi-step-compatible",
    "skill-smoke-compatible",
)


@dataclass(frozen=True)
class SupportTierDefinition:
    tier: str
    required_dimensions: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class CertificationScenario:
    scenario_id: str
    category: str
    requires_live: bool = True
    requires_skill: bool = False


@dataclass(frozen=True)
class ModelSupportTarget:
    target_id: str
    display_name: str
    provider_name: str
    model_name: str
    model_family: str
    roster: str
    goal_tier: str
    config_filename: str = ""


@dataclass(frozen=True)
class ProviderLaneSupportState:
    lane_id: str
    display_name: str
    provider_name: str
    endpoint_lane: str
    model_name: str
    model_family: str
    capability_profile_id: str
    config_filename: str
    access_state: str
    explicit_tool_state: str
    nl_parity_state: str
    skill_smoke_state: str
    notes: str = ""


@dataclass(frozen=True)
class ImplicitSkillSelectionLaneState:
    lane_id: str
    display_name: str
    provider_name: str
    endpoint_lane: str
    model_name: str
    model_family: str
    capability_profile_id: str
    config_filename: str
    explicit_baseline_pass_count: int
    explicit_baseline_scenario_count: int
    implicit_pass_count: int
    implicit_scenario_count: int
    certification_state: str
    routed_tracker: str = ""
    notes: str = ""


def _provider_lane_state(
    *,
    lane_id: str,
    display_name: str,
    provider_name: str,
    endpoint_lane: str,
    model_name: str,
    model_family: str,
    config_filename: str,
    access_state: str,
    explicit_tool_state: str,
    nl_parity_state: str,
    skill_smoke_state: str,
    notes: str = "",
) -> ProviderLaneSupportState:
    return ProviderLaneSupportState(
        lane_id=lane_id,
        display_name=display_name,
        provider_name=provider_name,
        endpoint_lane=endpoint_lane,
        model_name=model_name,
        model_family=model_family,
        capability_profile_id=capability_profile_id_for_model_name(
            model_name=model_name
        ),
        config_filename=config_filename,
        access_state=access_state,
        explicit_tool_state=explicit_tool_state,
        nl_parity_state=nl_parity_state,
        skill_smoke_state=skill_smoke_state,
        notes=notes,
    )


def _implicit_skill_selection_lane_state(
    *,
    lane_id: str,
    display_name: str,
    provider_name: str,
    endpoint_lane: str,
    model_name: str,
    model_family: str,
    config_filename: str,
    explicit_baseline_pass_count: int,
    explicit_baseline_scenario_count: int,
    implicit_pass_count: int,
    implicit_scenario_count: int,
    certification_state: str,
    routed_tracker: str = "",
    notes: str = "",
) -> ImplicitSkillSelectionLaneState:
    return ImplicitSkillSelectionLaneState(
        lane_id=lane_id,
        display_name=display_name,
        provider_name=provider_name,
        endpoint_lane=endpoint_lane,
        model_name=model_name,
        model_family=model_family,
        capability_profile_id=capability_profile_id_for_model_name(
            model_name=model_name
        ),
        config_filename=config_filename,
        explicit_baseline_pass_count=explicit_baseline_pass_count,
        explicit_baseline_scenario_count=explicit_baseline_scenario_count,
        implicit_pass_count=implicit_pass_count,
        implicit_scenario_count=implicit_scenario_count,
        certification_state=certification_state,
        routed_tracker=routed_tracker,
        notes=notes,
    )


_SUPPORT_TIERS: tuple[SupportTierDefinition, ...] = (
    SupportTierDefinition(
        tier=SupportTier.PRIMARY,
        required_dimensions=(
            "transport-compatible",
            "decision-compatible",
            "tool-compatible",
            "multi-step-compatible",
            "skill-smoke-compatible",
        ),
        summary="All core canaries plus at least one advanced multi-step lane and one real skill smoke.",
    ),
    SupportTierDefinition(
        tier=SupportTier.SECONDARY,
        required_dimensions=(
            "transport-compatible",
            "decision-compatible",
            "tool-compatible",
            "multi-step-compatible",
            "skill-smoke-compatible",
        ),
        summary="Core support with bounded retry/prompt discipline still acceptable for certification.",
    ),
    SupportTierDefinition(
        tier=SupportTier.LIMITED,
        required_dimensions=(
            "transport-compatible",
            "decision-compatible",
            "tool-compatible",
            "skill-smoke-compatible",
        ),
        summary="Simple single-tool and at least one skill-analog or skill smoke pass, but advanced lanes remain untrusted.",
    ),
    SupportTierDefinition(
        tier=SupportTier.UNSUPPORTED,
        required_dimensions=(),
        summary="Provider, infra, or model behavior prevents trustworthy certification on current code/runtime.",
    ),
)


_CERTIFICATION_SCENARIOS: tuple[CertificationScenario, ...] = (
    CertificationScenario("time_now", ScenarioCategory.CORE_TOOL),
    CertificationScenario("weather_now", ScenarioCategory.CORE_TOOL),
    CertificationScenario("fetch_example", ScenarioCategory.CORE_TOOL),
    CertificationScenario("search_news", ScenarioCategory.CORE_TOOL),
    CertificationScenario("search_and_time", ScenarioCategory.ADVANCED_RUNTIME),
    CertificationScenario("weather_two_cities", ScenarioCategory.ADVANCED_RUNTIME),
    CertificationScenario("api_json_fetch", ScenarioCategory.ADVANCED_RUNTIME),
    CertificationScenario("api_multi_fetch", ScenarioCategory.ADVANCED_RUNTIME),
    CertificationScenario(
        "linear_skill_smoke",
        ScenarioCategory.REAL_SKILL,
        requires_skill=True,
    ),
    CertificationScenario(
        "builder_skill_smoke",
        ScenarioCategory.REAL_SKILL,
        requires_skill=True,
    ),
    CertificationScenario(
        "research_skill_smoke",
        ScenarioCategory.REAL_SKILL,
        requires_skill=True,
    ),
)


_TARGETS: tuple[ModelSupportTarget, ...] = (
    ModelSupportTarget(
        target_id="openrouter-claude-haiku-4-5",
        display_name="Claude Haiku 4.5",
        provider_name="openrouter",
        model_name="anthropic/claude-haiku-4.5",
        model_family="claude",
        roster=SupportRoster.MAJOR,
        goal_tier=SupportTier.SECONDARY,
        config_filename="per-agent-openrouter-claude-haiku-4-5.json",
    ),
    ModelSupportTarget(
        target_id="openrouter-claude-3-haiku",
        display_name="Claude 3 Haiku",
        provider_name="openrouter",
        model_name="anthropic/claude-3-haiku",
        model_family="claude",
        roster=SupportRoster.MAJOR,
        goal_tier=SupportTier.SECONDARY,
        config_filename="per-agent-openrouter-claude-haiku-3.json",
    ),
    ModelSupportTarget(
        target_id="openrouter-gpt-4o-mini",
        display_name="GPT-4o mini",
        provider_name="openrouter",
        model_name="openai/gpt-4o-mini",
        model_family="openai",
        roster=SupportRoster.MAJOR,
        goal_tier=SupportTier.SECONDARY,
        config_filename="per-agent-openrouter-gpt-4o-mini.json",
    ),
    ModelSupportTarget(
        target_id="openrouter-gpt-4o",
        display_name="GPT-4o",
        provider_name="openrouter",
        model_name="openai/gpt-4o",
        model_family="openai",
        roster=SupportRoster.MAJOR,
        goal_tier=SupportTier.SECONDARY,
        config_filename="per-agent-openrouter-gpt-4o.json",
    ),
    ModelSupportTarget(
        target_id="openrouter-gemini-2-5-flash",
        display_name="Gemini 2.5 Flash",
        provider_name="openrouter",
        model_name="google/gemini-2.5-flash",
        model_family="gemini",
        roster=SupportRoster.MAJOR,
        goal_tier=SupportTier.SECONDARY,
        config_filename="per-agent-openrouter-gemini-2-5-flash.json",
    ),
    ModelSupportTarget(
        target_id="openrouter-gemini-2-5-pro",
        display_name="Gemini 2.5 Pro",
        provider_name="openrouter",
        model_name="google/gemini-2.5-pro",
        model_family="gemini",
        roster=SupportRoster.MAJOR,
        goal_tier=SupportTier.SECONDARY,
        config_filename="per-agent-openrouter-gemini-2-5-pro.json",
    ),
    ModelSupportTarget(
        target_id="openrouter-glm-5-turbo",
        display_name="GLM-5 Turbo",
        provider_name="openrouter",
        model_name="z-ai/glm-5-turbo",
        model_family="glm",
        roster=SupportRoster.MAJOR,
        goal_tier=SupportTier.SECONDARY,
        config_filename="per-agent-openrouter-glm-5-turbo.json",
    ),
    ModelSupportTarget(
        target_id="openrouter-minimax-m2-7",
        display_name="MiniMax M2.7",
        provider_name="openrouter",
        model_name="minimax/minimax-m2.7",
        model_family="minimax",
        roster=SupportRoster.MAJOR,
        goal_tier=SupportTier.SECONDARY,
        config_filename="per-agent-openrouter-minimax-m2-7.json",
    ),
    ModelSupportTarget(
        target_id="alibaba-minimax-m2-5",
        display_name="Alibaba MiniMax M2.5",
        provider_name="alibaba",
        model_name="MiniMax-M2.5",
        model_family="minimax",
        roster=SupportRoster.MAJOR,
        goal_tier=SupportTier.SECONDARY,
        config_filename="per-agent-alibaba-minimax.json",
    ),
    ModelSupportTarget(
        target_id="openrouter-qwen3-5-35b-a3b",
        display_name="Qwen 3.5 35B A3B",
        provider_name="openrouter",
        model_name="qwen/qwen3.5-35b-a3b",
        model_family="qwen",
        roster=SupportRoster.MEDIUM,
        goal_tier=SupportTier.LIMITED,
        config_filename="per-agent-openrouter-qwen3-5-35b-a3b.json",
    ),
    ModelSupportTarget(
        target_id="openrouter-qwen3-5-9b",
        display_name="Qwen 3.5 9B",
        provider_name="openrouter",
        model_name="qwen/qwen3.5-9b",
        model_family="qwen",
        roster=SupportRoster.MEDIUM,
        goal_tier=SupportTier.LIMITED,
        config_filename="per-agent-openrouter-qwen3-5-9b.json",
    ),
    ModelSupportTarget(
        target_id="alibaba-qwen3-5-plus",
        display_name="Alibaba Qwen 3.5 Plus",
        provider_name="alibaba",
        model_name="qwen3.5-plus",
        model_family="qwen",
        roster=SupportRoster.MEDIUM,
        goal_tier=SupportTier.LIMITED,
        config_filename="per-agent-alibaba-qwen3-5-plus.json",
    ),
    ModelSupportTarget(
        target_id="alibaba-glm-5",
        display_name="Alibaba GLM-5",
        provider_name="alibaba",
        model_name="glm-5",
        model_family="glm",
        roster=SupportRoster.MEDIUM,
        goal_tier=SupportTier.LIMITED,
        config_filename="per-agent-alibaba-glm-5.json",
    ),
    ModelSupportTarget(
        target_id="openrouter-gpt-5-4",
        display_name="GPT-5.4",
        provider_name="openrouter",
        model_name="openai/gpt-5.4",
        model_family="openai",
        roster=SupportRoster.MEDIUM,
        goal_tier=SupportTier.LIMITED,
        config_filename="per-agent-openrouter-gpt-5-4.json",
    ),
    ModelSupportTarget(
        target_id="openrouter-gpt-oss-20b",
        display_name="GPT-OSS 20B",
        provider_name="openrouter",
        model_name="openai/gpt-oss-20b",
        model_family="oss",
        roster=SupportRoster.UNSUPPORTED,
        goal_tier=SupportTier.UNSUPPORTED,
        config_filename="per-agent-openrouter-oss20b.json",
    ),
    ModelSupportTarget(
        target_id="openrouter-gpt-oss-120b",
        display_name="GPT-OSS 120B",
        provider_name="openrouter",
        model_name="openai/gpt-oss-120b",
        model_family="oss",
        roster=SupportRoster.UNSUPPORTED,
        goal_tier=SupportTier.UNSUPPORTED,
        config_filename="per-agent-openrouter-oss120b.json",
    ),
    ModelSupportTarget(
        target_id="openrouter-kimi-k2-5",
        display_name="Kimi K2.5",
        provider_name="openrouter",
        model_name="moonshotai/kimi-k2.5",
        model_family="kimi",
        roster=SupportRoster.UNSUPPORTED,
        goal_tier=SupportTier.UNSUPPORTED,
        config_filename="per-agent-openrouter-kimi-k2-5.json",
    ),
)


_PROVIDER_LANE_STATES: tuple[ProviderLaneSupportState, ...] = (
    _provider_lane_state(
        lane_id="alibaba-minimax-m2-5",
        display_name="Alibaba MiniMax M2.5",
        provider_name="alibaba",
        endpoint_lane="dashscope_coding_intl",
        model_name="MiniMax-M2.5",
        model_family="minimax",
        config_filename="per-agent-alibaba-minimax.json",
        access_state=AccessState.ACCESS_READY,
        explicit_tool_state=ExplicitToolState.HEALTHY,
        nl_parity_state=NLParityState.GAPPED,
        skill_smoke_state=SkillSmokeState.HEALTHY,
        notes=(
            "Explicit tool canaries are healthy on the DashScope coding-intl lane, "
            "and bounded current-news NL parity improved after the decide prompt "
            "contract refresh, but fresh cold-start repeats still remain inconsistent "
            "so the publication state stays gapped."
        ),
    ),
    _provider_lane_state(
        lane_id="alibaba-kimi-k2-5",
        display_name="Alibaba Kimi K2.5",
        provider_name="alibaba",
        endpoint_lane="dashscope_coding_intl",
        model_name="kimi-k2.5",
        model_family="kimi",
        config_filename="per-agent-alibaba-kimi-k2-5.json",
        access_state=AccessState.ACCESS_READY,
        explicit_tool_state=ExplicitToolState.HEALTHY,
        nl_parity_state=NLParityState.HEALTHY,
        skill_smoke_state=SkillSmokeState.UNVERIFIED,
        notes=(
            "Current bounded Alibaba proving-ground probes show the lane can execute "
            "the simple explicit and natural-language canaries, but broader skill "
            "smoke is not yet part of the certification claim."
        ),
    ),
    _provider_lane_state(
        lane_id="alibaba-qwen3-5-plus",
        display_name="Alibaba Qwen 3.5 Plus",
        provider_name="alibaba",
        endpoint_lane="dashscope_coding_intl",
        model_name="qwen3.5-plus",
        model_family="qwen",
        config_filename="per-agent-alibaba-qwen3-5-plus.json",
        access_state=AccessState.ACCESS_READY,
        explicit_tool_state=ExplicitToolState.HEALTHY,
        nl_parity_state=NLParityState.GAPPED,
        skill_smoke_state=SkillSmokeState.UNVERIFIED,
        notes=(
            "This profile now targets the DashScope coding-intl lane because the "
            "compatible-mode lane rejects the configured key. Structured submit_output "
            "phases may still need tool_choice retry-to-auto, and natural-language "
            "search remains gapped pending fresh coding-intl re-certification."
        ),
    ),
    _provider_lane_state(
        lane_id="alibaba-glm-5",
        display_name="Alibaba GLM-5",
        provider_name="alibaba",
        endpoint_lane="dashscope_coding_intl",
        model_name="glm-5",
        model_family="glm",
        config_filename="per-agent-alibaba-glm-5.json",
        access_state=AccessState.ACCESS_READY,
        explicit_tool_state=ExplicitToolState.HEALTHY,
        nl_parity_state=NLParityState.GAPPED,
        skill_smoke_state=SkillSmokeState.UNVERIFIED,
        notes=(
            "This profile now targets the DashScope coding-intl lane with the shared "
            "Alibaba path. Access is no longer blocked by the old compatible-mode "
            "auth mismatch, but model-led natural-language parity remains gapped while "
            "structured decide behavior is being tuned on the coding-intl lane."
        ),
    ),
)


_IMPLICIT_SKILL_SELECTION_LANE_STATES: tuple[ImplicitSkillSelectionLaneState, ...] = (
    _implicit_skill_selection_lane_state(
        lane_id="official-minimax-m2-7",
        display_name="Official MiniMax M2.7",
        provider_name="official-minimax",
        endpoint_lane="api_minimax_global",
        model_name="MiniMax-M2.7",
        model_family="minimax",
        config_filename="per-agent-minimax-official-skill-e2e.json",
        explicit_baseline_pass_count=20,
        explicit_baseline_scenario_count=20,
        implicit_pass_count=20,
        implicit_scenario_count=20,
        certification_state=ImplicitSkillSelectionState.CERTIFIED,
        routed_tracker="skill-runtime-support-saturation-tracker",
        notes=(
            "Official MiniMax M2.7 now re-certifies on the implicit no-magic "
            "dense-catalog slice at 20/20 after the bounded SRSS follow-on. The "
            "recovered path stays anti-LLM-clean: improved retrieval text plus a "
            "full-catalog LLM retry when retrieval-select returns empty. Current "
            "no-magic passes split across retrieval-select and llm-select."
        ),
    ),
    _implicit_skill_selection_lane_state(
        lane_id="official-minimax-m2-5",
        display_name="Official MiniMax M2.5",
        provider_name="official-minimax",
        endpoint_lane="api_minimax_global",
        model_name="MiniMax-M2.5",
        model_family="minimax",
        config_filename="per-agent-minimax-official-skill-e2e.json",
        explicit_baseline_pass_count=20,
        explicit_baseline_scenario_count=20,
        implicit_pass_count=20,
        implicit_scenario_count=20,
        certification_state=ImplicitSkillSelectionState.CERTIFIED,
        routed_tracker="skill-runtime-support-saturation-tracker",
        notes=(
            "Official MiniMax M2.5 now re-certifies on the implicit no-magic "
            "dense-catalog slice at 20/20 after the bounded SRSS follow-on. The "
            "recovered path stays anti-LLM-clean: improved retrieval text plus a "
            "full-catalog LLM retry when retrieval-select returns empty. Current "
            "no-magic passes split across retrieval-select and llm-select."
        ),
    ),
)


def support_dimensions() -> tuple[str, ...]:
    return SUPPORT_DIMENSIONS


def support_tier_definitions() -> tuple[SupportTierDefinition, ...]:
    return _SUPPORT_TIERS


def certification_scenarios(
    category: str | None = None,
) -> tuple[CertificationScenario, ...]:
    if not category:
        return _CERTIFICATION_SCENARIOS
    return tuple(
        scenario
        for scenario in _CERTIFICATION_SCENARIOS
        if scenario.category == category
    )


def support_targets(roster: str | None = None) -> tuple[ModelSupportTarget, ...]:
    if not roster:
        return _TARGETS
    return tuple(target for target in _TARGETS if target.roster == roster)


def support_target_by_id(target_id: str) -> ModelSupportTarget | None:
    normalized = str(target_id or "").strip()
    for target in _TARGETS:
        if target.target_id == normalized:
            return target
    return None


def provider_lane_support_states(
    provider_name: str | None = None,
) -> tuple[ProviderLaneSupportState, ...]:
    if not provider_name:
        return _PROVIDER_LANE_STATES
    normalized = str(provider_name or "").strip().lower()
    return tuple(
        lane for lane in _PROVIDER_LANE_STATES if lane.provider_name == normalized
    )


def provider_lane_support_state_by_id(
    lane_id: str,
) -> ProviderLaneSupportState | None:
    normalized = str(lane_id or "").strip()
    for lane in _PROVIDER_LANE_STATES:
        if lane.lane_id == normalized:
            return lane
    return None


def implicit_skill_selection_states(
    provider_name: str | None = None,
) -> tuple[ImplicitSkillSelectionLaneState, ...]:
    if not provider_name:
        return _IMPLICIT_SKILL_SELECTION_LANE_STATES
    normalized = str(provider_name or "").strip().lower()
    return tuple(
        lane
        for lane in _IMPLICIT_SKILL_SELECTION_LANE_STATES
        if lane.provider_name == normalized
    )


def implicit_skill_selection_state_by_id(
    lane_id: str,
) -> ImplicitSkillSelectionLaneState | None:
    normalized = str(lane_id or "").strip()
    for lane in _IMPLICIT_SKILL_SELECTION_LANE_STATES:
        if lane.lane_id == normalized:
            return lane
    return None


def registry_snapshot() -> dict[str, object]:
    return {
        "support_dimensions": list(SUPPORT_DIMENSIONS),
        "tiers": [asdict(entry) for entry in _SUPPORT_TIERS],
        "scenarios": [asdict(entry) for entry in _CERTIFICATION_SCENARIOS],
        "targets": [asdict(entry) for entry in _TARGETS],
        "provider_lane_states": [asdict(entry) for entry in _PROVIDER_LANE_STATES],
        "implicit_skill_selection_states": [
            asdict(entry) for entry in _IMPLICIT_SKILL_SELECTION_LANE_STATES
        ],
    }


__all__ = [
    "AccessState",
    "CertificationScenario",
    "ExplicitToolState",
    "ImplicitSkillSelectionLaneState",
    "ImplicitSkillSelectionState",
    "ModelSupportTarget",
    "NLParityState",
    "ProviderLaneSupportState",
    "ScenarioCategory",
    "SkillSmokeState",
    "SupportRoster",
    "SupportTier",
    "SupportTierDefinition",
    "certification_scenarios",
    "provider_lane_support_state_by_id",
    "provider_lane_support_states",
    "implicit_skill_selection_state_by_id",
    "implicit_skill_selection_states",
    "registry_snapshot",
    "support_dimensions",
    "support_target_by_id",
    "support_targets",
    "support_tier_definitions",
]
