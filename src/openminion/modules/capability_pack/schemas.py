from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PolicyDecision = Literal["allow", "ask", "deny"]
CapabilityPackEventType = Literal[
    "capability_pack.activated",
    "capability_pack.activation_denied",
    "capability_pack.override_applied",
]
ToolRiskLevel = Literal["low", "med", "high"]
ToolSideEffects = Literal["none", "local", "remote", "external_account"]
PolicyVerb = Literal[
    "read",
    "write",
    "destructive",
    "external_send",
    "money_movement",
    "prod_mutation",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PackSkillMetadata(StrictModel):
    skill_id: str = Field(min_length=1)
    teaches: tuple[str, ...] = ()
    requires_tools: tuple[str, ...] = ()
    safe_for_domains: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    evidence_expectations: tuple[str, ...] = ()


class PackToolMetadata(StrictModel):
    tool_id: str = Field(min_length=1)
    capabilities: tuple[str, ...] = ()
    risk_level: ToolRiskLevel
    side_effects: ToolSideEffects
    approval_required_for: tuple[str, ...] = ()
    result_contract: str = Field(min_length=1)
    timeout_policy: str = Field(min_length=1)
    audit_events: tuple[str, ...] = ()


class PackPolicyRule(StrictModel):
    verb: PolicyVerb
    capability_scope: str = Field(default="*", min_length=1)
    decision: PolicyDecision


class PackPolicyProfile(StrictModel):
    profile_id: str = Field(min_length=1)
    default_decision: PolicyDecision = "deny"
    rules: tuple[PackPolicyRule, ...] = ()

    @model_validator(mode="after")
    def unique_rules(self) -> "PackPolicyProfile":
        keys = [(rule.verb, rule.capability_scope) for rule in self.rules]
        if len(keys) != len(set(keys)):
            raise ValueError("policy rules must be unique by verb and capability scope")
        return self


class CapabilityPackManifest(StrictModel):
    pack_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    version: str = Field(min_length=1)
    skills: tuple[PackSkillMetadata, ...]
    tools: tuple[PackToolMetadata, ...]
    policy_profile: PackPolicyProfile
    eval_suite: str = Field(min_length=1)
    audit_profile: str = Field(min_length=1)
    registry_ref: str = Field(min_length=1)
    risk_model: str = Field(min_length=1)
    baseline_tools: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_references(self) -> "CapabilityPackManifest":
        skill_ids = [item.skill_id for item in self.skills]
        tool_ids = [item.tool_id for item in self.tools]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("skill ids must be unique")
        if len(tool_ids) != len(set(tool_ids)):
            raise ValueError("tool ids must be unique")
        declared = set(tool_ids) | set(self.baseline_tools)
        for skill in self.skills:
            missing = set(skill.requires_tools) - declared
            if missing:
                raise ValueError(
                    f"skill {skill.skill_id!r} requires undeclared tools: "
                    f"{sorted(missing)!r}"
                )
        return self


class CapabilityPackAuditEvent(StrictModel):
    event_type: CapabilityPackEventType
    pack_id: str
    session_id: str
    visible_tools: tuple[str, ...] = ()
    visible_skills: tuple[str, ...] = ()
    override_tools: tuple[str, ...] = ()
    reason: str = ""


class ActiveCapabilityPack(StrictModel):
    pack_id: str
    version: str
    session_id: str
    visible_tools: tuple[str, ...]
    visible_skills: tuple[str, ...]
    policy_profile: PackPolicyProfile
    audit_event: CapabilityPackAuditEvent
