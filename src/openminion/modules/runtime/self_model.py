"""Typed runtime self-awareness snapshot contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SelfModelHealth = Literal["ok", "degraded", "unavailable"]

SELF_MODEL_HEALTH_OK: SelfModelHealth = "ok"
SELF_MODEL_HEALTH_DEGRADED: SelfModelHealth = "degraded"
SELF_MODEL_HEALTH_UNAVAILABLE: SelfModelHealth = "unavailable"

DEGRADED_GENERIC_CANDIDATE_REGISTRY_UNAVAILABLE = (
    "generic_candidate_registry_unavailable"
)
DEGRADED_IDENTITY_UNAVAILABLE = "identity_unavailable"
DEGRADED_RUNTIME_CAPABILITY_REPORT_UNAVAILABLE = "runtime_capability_report_unavailable"
DEGRADED_POLICY_POSTURE_UNAVAILABLE = "policy_posture_unavailable"
DEGRADED_MEMORY_UNAVAILABLE = "memory_unavailable"
DEGRADED_CONTEXT_UNAVAILABLE = "context_unavailable"


class SelfModelSection(BaseModel):
    """One named section inside a runtime self-model snapshot."""

    model_config = ConfigDict(extra="forbid")

    status: SelfModelHealth = SELF_MODEL_HEALTH_OK
    facts: dict[str, Any] = Field(default_factory=dict)
    degraded_reasons: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == SELF_MODEL_HEALTH_OK


class SelfModelSnapshot(BaseModel):
    """Runtime-owned answer to "what am I and what can I do right now?"."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "self_model.v1"
    health: SelfModelHealth = SELF_MODEL_HEALTH_OK
    agent_id: str
    identity: SelfModelSection = Field(default_factory=SelfModelSection)
    capabilities: SelfModelSection = Field(default_factory=SelfModelSection)
    policy: SelfModelSection = Field(default_factory=SelfModelSection)
    memory_state: SelfModelSection = Field(default_factory=SelfModelSection)
    context_state: SelfModelSection = Field(default_factory=SelfModelSection)
    knowledge_state: SelfModelSection = Field(default_factory=SelfModelSection)
    improvement_state: SelfModelSection = Field(default_factory=SelfModelSection)
    degraded_reasons: list[str] = Field(default_factory=list)

    @classmethod
    def compose_health(
        cls, sections: Mapping[str, SelfModelSection]
    ) -> SelfModelHealth:
        statuses = {section.status for section in sections.values()}
        if SELF_MODEL_HEALTH_UNAVAILABLE in statuses:
            return SELF_MODEL_HEALTH_UNAVAILABLE
        if SELF_MODEL_HEALTH_DEGRADED in statuses:
            return SELF_MODEL_HEALTH_DEGRADED
        return SELF_MODEL_HEALTH_OK

    @classmethod
    def from_sections(
        cls,
        *,
        agent_id: str,
        identity: SelfModelSection,
        capabilities: SelfModelSection,
        policy: SelfModelSection,
        memory_state: SelfModelSection,
        context_state: SelfModelSection,
        knowledge_state: SelfModelSection,
        improvement_state: SelfModelSection,
    ) -> "SelfModelSnapshot":
        sections = {
            "identity": identity,
            "capabilities": capabilities,
            "policy": policy,
            "memory_state": memory_state,
            "context_state": context_state,
            "knowledge_state": knowledge_state,
            "improvement_state": improvement_state,
        }
        reasons: list[str] = []
        for section in sections.values():
            for reason in section.degraded_reasons:
                if reason not in reasons:
                    reasons.append(reason)
        return cls(
            agent_id=str(agent_id or "").strip() or "unknown",
            health=cls.compose_health(sections),
            identity=identity,
            capabilities=capabilities,
            policy=policy,
            memory_state=memory_state,
            context_state=context_state,
            knowledge_state=knowledge_state,
            improvement_state=improvement_state,
            degraded_reasons=reasons,
        )


def section_ok(**facts: Any) -> SelfModelSection:
    return SelfModelSection(status=SELF_MODEL_HEALTH_OK, facts=_clean_facts(facts))


def section_degraded(reason: str, **facts: Any) -> SelfModelSection:
    return SelfModelSection(
        status=SELF_MODEL_HEALTH_DEGRADED,
        facts=_clean_facts(facts),
        degraded_reasons=[str(reason or "").strip() or "unknown_degradation"],
    )


def section_unavailable(reason: str, **facts: Any) -> SelfModelSection:
    return SelfModelSection(
        status=SELF_MODEL_HEALTH_UNAVAILABLE,
        facts=_clean_facts(facts),
        degraded_reasons=[str(reason or "").strip() or "unknown_unavailable"],
    )


def render_self_awareness_context_block(snapshot: SelfModelSnapshot) -> str:
    """Render a compact, secrets-safe self-awareness context block."""

    payload = {
        "health": snapshot.health,
        "agent_id": snapshot.agent_id,
        "identity": _section_context(snapshot.identity, ("display_name", "mission")),
        "capabilities": _section_context(
            snapshot.capabilities,
            ("provider", "model", "tool_count", "enabled_tool_count"),
        ),
        "policy": _section_context(
            snapshot.policy,
            ("permission_mode", "sandbox", "destructive_action_posture"),
        ),
        "memory_state": _section_context(
            snapshot.memory_state,
            ("provider", "scopes", "provenance_available", "blocks_loaded"),
        ),
        "context_state": _section_context(
            snapshot.context_state,
            ("budget_total", "pinned_prefix_cache", "compaction_state"),
        ),
        "improvement_state": _section_context(
            snapshot.improvement_state,
            ("policy", "phase", "candidate_count", "promotion_posture"),
        ),
        "degraded_reasons": list(snapshot.degraded_reasons),
    }
    return "[SELF AWARENESS]\n" + json.dumps(
        _redact_secrets(payload),
        sort_keys=True,
        indent=2,
        ensure_ascii=True,
    )


def _section_context(
    section: SelfModelSection,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    facts = {key: section.facts.get(key) for key in keys if key in section.facts}
    if section.degraded_reasons:
        facts["degraded_reasons"] = list(section.degraded_reasons)
    facts["status"] = section.status
    return facts


def _clean_facts(facts: Mapping[str, Any]) -> dict[str, Any]:
    return _redact_secrets(
        {str(key): value for key, value in facts.items() if value is not None}
    )


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_key(key_text):
                cleaned[key_text] = "[redacted]"
            else:
                cleaned[key_text] = _redact_secrets(item)
        return cleaned
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_secrets(item) for item in value]
    return value


def _is_secret_key(key: str) -> bool:
    normalized = key.lower()
    return any(
        marker in normalized
        for marker in ("secret", "token", "password", "credential", "api_key")
    )


__all__ = [
    "DEGRADED_CONTEXT_UNAVAILABLE",
    "DEGRADED_GENERIC_CANDIDATE_REGISTRY_UNAVAILABLE",
    "DEGRADED_IDENTITY_UNAVAILABLE",
    "DEGRADED_MEMORY_UNAVAILABLE",
    "DEGRADED_POLICY_POSTURE_UNAVAILABLE",
    "DEGRADED_RUNTIME_CAPABILITY_REPORT_UNAVAILABLE",
    "SELF_MODEL_HEALTH_DEGRADED",
    "SELF_MODEL_HEALTH_OK",
    "SELF_MODEL_HEALTH_UNAVAILABLE",
    "SelfModelHealth",
    "SelfModelSection",
    "SelfModelSnapshot",
    "render_self_awareness_context_block",
    "section_degraded",
    "section_ok",
    "section_unavailable",
]
