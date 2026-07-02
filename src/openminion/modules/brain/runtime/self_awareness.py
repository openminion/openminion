"""Snapshot-grounded self-awareness answer helpers."""

from __future__ import annotations

from typing import Any

from openminion.modules.runtime.self_model import (
    SELF_MODEL_HEALTH_OK,
    SelfModelSection,
    SelfModelSnapshot,
)


def answer_self_awareness_question(
    snapshot: SelfModelSnapshot | dict[str, Any],
    *,
    question: str,
) -> str:
    """Answer self/capability questions from a runtime snapshot only."""

    snapshot_obj = (
        snapshot
        if isinstance(snapshot, SelfModelSnapshot)
        else SelfModelSnapshot.model_validate(snapshot)
    )
    normalized = str(question or "").lower()
    if "tool" in normalized:
        return _tools_answer(snapshot_obj)
    if "remember" in normalized or "memory" in normalized:
        return _memory_answer(snapshot_obj)
    if "allowed" in normalized or "not allowed" in normalized or "policy" in normalized:
        return _policy_answer(snapshot_obj)
    if "improve" in normalized:
        return _improvement_answer(snapshot_obj)
    if "disabled" in normalized or "degraded" in normalized or "unavailable" in normalized:
        return _degraded_answer(snapshot_obj)
    if "what can" in normalized or "capab" in normalized:
        return _capabilities_answer(snapshot_obj)
    return _identity_answer(snapshot_obj)


def _identity_answer(snapshot: SelfModelSnapshot) -> str:
    identity = snapshot.identity
    if not identity.ok:
        return _degraded_section_answer("identity", identity)
    display_name = _fact(identity, "display_name", snapshot.agent_id)
    mission = _fact(identity, "mission", "")
    if mission:
        return f"I am {display_name}. My current mission is: {mission}"
    return f"I am {display_name}."


def _capabilities_answer(snapshot: SelfModelSnapshot) -> str:
    capabilities = snapshot.capabilities
    if not capabilities.ok:
        return _degraded_section_answer("capabilities", capabilities)
    provider = _fact(capabilities, "provider", "unknown provider")
    model = _fact(capabilities, "model", "")
    enabled = _fact(capabilities, "enabled_tool_count", 0)
    total = _fact(capabilities, "tool_count", 0)
    model_text = f" using model {model}" if model else ""
    return (
        f"I am running on provider {provider}{model_text}. "
        f"I currently see {enabled} enabled tools out of {total} visible tools."
    )


def _tools_answer(snapshot: SelfModelSnapshot) -> str:
    capabilities = snapshot.capabilities
    if not capabilities.ok:
        return _degraded_section_answer("tools", capabilities)
    enabled = _fact(capabilities, "enabled_tool_count", 0)
    total = _fact(capabilities, "tool_count", 0)
    return f"I currently have {enabled} enabled tools out of {total} visible tools."


def _memory_answer(snapshot: SelfModelSnapshot) -> str:
    memory = snapshot.memory_state
    if not memory.ok:
        return _degraded_section_answer("memory", memory)
    provider = _fact(memory, "provider", "unknown")
    provenance = _fact(memory, "provenance_available", False)
    scopes = _fact(memory, "scopes", [])
    scope_text = ", ".join(str(item) for item in scopes) if scopes else "no scopes listed"
    return (
        f"My memory provider is {provider}. "
        f"Provenance recording is {'available' if provenance else 'not available'}. "
        f"Visible scopes: {scope_text}."
    )


def _policy_answer(snapshot: SelfModelSnapshot) -> str:
    policy = snapshot.policy
    if not policy.ok:
        return _degraded_section_answer("policy", policy)
    permission = _fact(policy, "permission_mode", "configured")
    sandbox = _fact(policy, "sandbox", "unknown")
    destructive = _fact(policy, "destructive_action_posture", "configured")
    return (
        f"My permission mode is {permission}; sandbox posture is {sandbox}; "
        f"destructive-action posture is {destructive}."
    )


def _improvement_answer(snapshot: SelfModelSnapshot) -> str:
    improvement = snapshot.improvement_state
    policy = _fact(improvement, "policy", "never")
    posture = _fact(improvement, "promotion_posture", "unknown")
    answer = f"My self-improvement policy is {policy}; promotion posture is {posture}."
    if improvement.status != SELF_MODEL_HEALTH_OK and improvement.degraded_reasons:
        answer += " Degraded reasons: " + ", ".join(improvement.degraded_reasons) + "."
    return answer


def _degraded_answer(snapshot: SelfModelSnapshot) -> str:
    if not snapshot.degraded_reasons:
        return "No degraded self-model sections are currently reported."
    return "Current degraded self-model reasons: " + ", ".join(snapshot.degraded_reasons) + "."


def _degraded_section_answer(label: str, section: SelfModelSection) -> str:
    reasons = ", ".join(section.degraded_reasons) or "unknown"
    return f"I cannot truthfully answer the {label} section right now. Degraded reasons: {reasons}."


def _fact(section: SelfModelSection, key: str, default: Any) -> Any:
    value = section.facts.get(key, default)
    return default if value is None else value


__all__ = ["answer_self_awareness_question"]
