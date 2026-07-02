"""Compose runtime self-awareness snapshots from existing runtime reports."""

from __future__ import annotations

from typing import Any

from openminion.modules.runtime.self_model import (
    DEGRADED_CONTEXT_UNAVAILABLE,
    DEGRADED_GENERIC_CANDIDATE_REGISTRY_UNAVAILABLE,
    DEGRADED_IDENTITY_UNAVAILABLE,
    DEGRADED_MEMORY_UNAVAILABLE,
    DEGRADED_POLICY_POSTURE_UNAVAILABLE,
    DEGRADED_RUNTIME_CAPABILITY_REPORT_UNAVAILABLE,
    SelfModelSection,
    SelfModelSnapshot,
    section_degraded,
    section_ok,
    section_unavailable,
)

from .runtime_reports import build_capability_report, build_runtime_posture_report


def build_runtime_self_model(
    runtime: Any,
    *,
    agent_id: str | None = None,
    overrides: Any = None,
) -> SelfModelSnapshot:
    """Build a truthful partial self-model from existing runtime owners."""

    resolved_agent_id = _resolve_agent_id(
        runtime, agent_id=agent_id, overrides=overrides
    )
    capability_report, capability_section = _capabilities(
        runtime, resolved_agent_id, overrides
    )
    posture_report, policy_section = _policy(runtime, resolved_agent_id, overrides)
    identity_section = _identity(runtime, resolved_agent_id)
    memory_section = _memory(runtime)
    context_section = _context(runtime, posture_report)
    knowledge_section = _knowledge(runtime)
    improvement_section = _improvement(runtime)
    return SelfModelSnapshot.from_sections(
        agent_id=resolved_agent_id,
        identity=identity_section,
        capabilities=capability_section,
        policy=policy_section,
        memory_state=memory_section,
        context_state=context_section,
        knowledge_state=knowledge_section,
        improvement_state=improvement_section,
    )


def _resolve_agent_id(runtime: Any, *, agent_id: str | None, overrides: Any) -> str:
    requested = str(agent_id or "").strip()
    if requested:
        return requested
    resolver = getattr(runtime, "resolve_agent_profile", None)
    if callable(resolver):
        try:
            profile = resolver(None, overrides=overrides)
        except TypeError:
            try:
                profile = resolver(None)
            except Exception:  # noqa: BLE001
                profile = None
        except Exception:  # noqa: BLE001
            profile = None
        name = str(getattr(profile, "name", "") or "").strip()
        if name:
            return name
    config = getattr(runtime, "config", None)
    agents = getattr(config, "agents", None)
    if isinstance(agents, dict) and agents:
        return sorted(str(key) for key in agents)[0]
    return "unknown"


def _capabilities(
    runtime: Any,
    agent_id: str,
    overrides: Any,
) -> tuple[dict[str, Any], SelfModelSection]:
    try:
        report = build_capability_report(
            runtime,
            agent_id=agent_id,
            overrides=overrides,
        )
    except Exception as exc:  # noqa: BLE001
        return {}, section_unavailable(
            DEGRADED_RUNTIME_CAPABILITY_REPORT_UNAVAILABLE,
            error=f"{type(exc).__name__}: {exc}",
        )
    providers = dict(report.get("providers", {}) or {})
    tools = dict(report.get("tools", {}) or {})
    counts = dict(tools.get("counts", {}) or {})
    inventory = [item for item in tools.get("inventory", []) if isinstance(item, dict)]
    selected_provider = str(providers.get("selected", "") or "").strip()
    selected_model = _selected_model(runtime, agent_id)
    return report, section_ok(
        provider=selected_provider or "unknown",
        model=selected_model,
        tool_count=int(counts.get("total", len(inventory)) or 0),
        enabled_tool_count=sum(
            1 for item in inventory if bool(item.get("enabled", True))
        ),
        modes=report.get("modes", {}),
        thinking=report.get("thinking", {}),
        plugins=report.get("plugins", {}),
        mcp=report.get("mcp", {}),
    )


def _policy(
    runtime: Any, agent_id: str, overrides: Any
) -> tuple[dict[str, Any], SelfModelSection]:
    try:
        report = build_runtime_posture_report(
            runtime,
            agent_id=agent_id,
            overrides=overrides,
            canonical_turn_path=("api", "gateway", "agent"),
            canonical_turn_path_ref="runtime.self_model",
            execution_boundary_policy_ref="runtime.security_policy",
            capability_layering_ref="runtime.capability_report",
        )
    except Exception as exc:  # noqa: BLE001
        return {}, section_unavailable(
            DEGRADED_POLICY_POSTURE_UNAVAILABLE,
            error=f"{type(exc).__name__}: {exc}",
        )
    boundary = dict(report.get("execution_boundary_policy", {}) or {})
    return report, section_ok(
        runtime_mode=report.get("runtime_mode", "unknown"),
        brain_bridge_active=bool(report.get("brain_bridge_active", False)),
        fallback_reason=str(report.get("fallback_reason", "") or ""),
        permission_mode=_permission_mode(runtime),
        sandbox=_sandbox_posture(runtime),
        destructive_action_posture=_destructive_action_posture(boundary),
        execution_boundary_policy=boundary,
    )


def _identity(runtime: Any, agent_id: str) -> SelfModelSection:
    profile = None
    resolver = getattr(runtime, "resolve_agent_profile", None)
    if callable(resolver):
        try:
            profile = resolver(agent_id)
        except Exception:  # noqa: BLE001
            profile = None
    if profile is None:
        return section_unavailable(DEGRADED_IDENTITY_UNAVAILABLE, agent_id=agent_id)
    return section_ok(
        agent_id=agent_id,
        profile_name=str(getattr(profile, "name", "") or agent_id),
        display_name=str(
            getattr(profile, "display_name", "")
            or getattr(profile, "name", "")
            or agent_id
        ),
        mission=_profile_mission(profile),
        tone=_profile_tone(profile),
        source="runtime_agent_profile",
    )


def _memory(runtime: Any) -> SelfModelSection:
    memory_owner = _first_attr(runtime, ("memory", "memory_api", "memoryctl", "memctl"))
    if memory_owner is None:
        return section_degraded(DEGRADED_MEMORY_UNAVAILABLE, provider="none")
    return section_ok(
        provider=type(memory_owner).__name__,
        scopes=_safe_call_list(memory_owner, ("list_scopes", "scopes")),
        provenance_available=bool(
            _first_attr(runtime, ("provenance_recorder", "memory_provenance_recorder"))
        ),
        blocks_loaded=0,
    )


def _context(runtime: Any, posture_report: dict[str, Any]) -> SelfModelSection:
    config = getattr(runtime, "config", None)
    if config is None:
        return section_degraded(DEGRADED_CONTEXT_UNAVAILABLE, budget_total=0)
    return section_ok(
        budget_total=_context_budget_total(config),
        pinned_prefix_cache=_pinned_prefix_posture(posture_report),
        compaction_state=_compaction_state(runtime),
    )


def _knowledge(runtime: Any) -> SelfModelSection:
    graph = _first_attr(runtime, ("knowledge", "knowledge_graph", "graph_provider"))
    return section_ok(
        providers=[type(graph).__name__] if graph is not None else [],
        degraded_providers=[],
    )


def _improvement(runtime: Any) -> SelfModelSection:
    policy = _improvement_policy(runtime)
    return section_degraded(
        DEGRADED_GENERIC_CANDIDATE_REGISTRY_UNAVAILABLE,
        phase="phase_a_bsil_only",
        policy=policy,
        candidate_count=0,
        promotion_posture="bsil_only",
    )


def _selected_model(runtime: Any, agent_id: str) -> str:
    profile = None
    resolver = getattr(runtime, "resolve_agent_profile", None)
    if callable(resolver):
        try:
            profile = resolver(agent_id)
        except Exception:  # noqa: BLE001
            profile = None
    for attr in ("model", "model_name", "llm_model"):
        value = str(getattr(profile, attr, "") or "").strip()
        if value:
            return value
    return ""


def _profile_mission(profile: Any) -> str:
    role = getattr(profile, "role", None)
    return str(getattr(role, "mission", "") or getattr(profile, "mission", "") or "")


def _profile_tone(profile: Any) -> str:
    personality = getattr(profile, "personality", None)
    return str(getattr(personality, "tone", "") or getattr(profile, "tone", "") or "")


def _permission_mode(runtime: Any) -> str:
    security = getattr(runtime, "security_policy", None)
    return str(getattr(security, "permission_mode", "") or "configured").strip()


def _sandbox_posture(runtime: Any) -> str:
    config = getattr(runtime, "config", None)
    runtime_config = getattr(config, "runtime", None)
    return str(getattr(runtime_config, "sandbox_mode", "") or "unknown").strip()


def _destructive_action_posture(boundary: dict[str, Any]) -> str:
    scopes = boundary.get("default_required_scopes", [])
    return "approval_required" if scopes else "configured"


def _first_attr(owner: Any, names: tuple[str, ...]) -> Any:
    for name in names:
        value = getattr(owner, name, None)
        if value is not None:
            return value
    return None


def _safe_call_list(owner: Any, names: tuple[str, ...]) -> list[str]:
    for name in names:
        value = getattr(owner, name, None)
        try:
            result = value() if callable(value) else value
        except Exception:  # noqa: BLE001
            continue
        if isinstance(result, (list, tuple, set)):
            return sorted(str(item) for item in result if str(item).strip())
    return []


def _context_budget_total(config: Any) -> int:
    context = getattr(config, "context", None)
    for attr in ("total_max_tokens", "max_tokens", "context_window"):
        value = getattr(context, attr, None)
        if isinstance(value, int) and value > 0:
            return value
    return 0


def _pinned_prefix_posture(posture_report: dict[str, Any]) -> str:
    layering = dict(posture_report.get("capability_layering", {}) or {})
    provider = str(layering.get("provider_selected", "") or "").strip()
    return "available" if provider else "unknown"


def _compaction_state(runtime: Any) -> str:
    sessions = getattr(runtime, "sessions", None)
    return "available" if sessions is not None else "unknown"


def _improvement_policy(runtime: Any) -> str:
    policy = getattr(runtime, "self_improvement_policy", None)
    if policy is None:
        return "never"
    return str(getattr(policy, "policy", policy) or "never")


__all__ = ["build_runtime_self_model"]
