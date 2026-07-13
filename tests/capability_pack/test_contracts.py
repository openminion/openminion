from __future__ import annotations

import pytest
from pydantic import ValidationError

from openminion.modules.capability_pack import (
    CapabilityPackManifest,
    CapabilityPackRegistry,
    PackPolicyProfile,
    PackPolicyRule,
    PackSkillMetadata,
    PackToolMetadata,
    activate_pack,
    resolve_policy,
)


def _manifest() -> CapabilityPackManifest:
    return CapabilityPackManifest(
        pack_id="fixture-pack",
        domain="fixture",
        version="1.0.0",
        skills=(
            PackSkillMetadata(
                skill_id="fixture-diagnostics",
                teaches=("inspect fixture state",),
                requires_tools=("fixture.inspect",),
                safe_for_domains=("fixture",),
                forbidden_claims=("never claim success without evidence",),
                evidence_expectations=("fixture evidence id",),
            ),
        ),
        tools=(
            PackToolMetadata(
                tool_id="fixture.inspect",
                capabilities=("fixture.read",),
                risk_level="low",
                side_effects="none",
                result_contract="FixtureInspectResult",
                timeout_policy="bounded-30s",
                audit_events=("fixture.inspected",),
            ),
        ),
        policy_profile=PackPolicyProfile(
            profile_id="fixture-readonly",
            rules=(
                PackPolicyRule(
                    verb="read",
                    capability_scope="fixture.read",
                    decision="allow",
                ),
                PackPolicyRule(
                    verb="write",
                    capability_scope="*",
                    decision="ask",
                ),
            ),
        ),
        eval_suite="fixture-pack-eval",
        audit_profile="fixture-pack-audit",
        registry_ref="fixture-registry",
        risk_model="fixture-risk-v1",
        baseline_tools=("tool.list",),
    )


def test_manifest_rejects_unknown_fields_and_missing_tool_references() -> None:
    raw = _manifest().model_dump()
    raw["unknown"] = True
    with pytest.raises(ValidationError):
        CapabilityPackManifest.model_validate(raw)

    raw = _manifest().model_dump()
    raw["skills"][0]["requires_tools"] = ["fixture.missing"]
    with pytest.raises(ValidationError, match="undeclared tools"):
        CapabilityPackManifest.model_validate(raw)


def test_registry_policy_and_session_activation_are_deterministic() -> None:
    manifest = _manifest()
    registry = CapabilityPackRegistry()
    registry.register(manifest)
    assert registry.get("fixture-pack") == manifest
    assert (
        resolve_policy(
            manifest.policy_profile,
            verb="read",
            capability_scope="fixture.read",
        )
        == "allow"
    )
    assert (
        resolve_policy(
            manifest.policy_profile,
            verb="write",
            capability_scope="fixture.change",
        )
        == "ask"
    )
    assert (
        resolve_policy(
            manifest.policy_profile,
            verb="destructive",
            capability_scope="fixture.delete",
        )
        == "deny"
    )

    events = []
    active = activate_pack(
        manifest,
        session_id="session-1",
        available_tools=("fixture.inspect", "tool.list", "tool.search"),
        available_skills=("fixture-diagnostics", "other-skill"),
        audit_sink=events.append,
    )
    assert active.visible_tools == ("fixture.inspect", "tool.list")
    assert active.visible_skills == ("fixture-diagnostics",)
    assert events == [active.audit_event]


def test_activation_rejects_unavailable_dependencies() -> None:
    events = []
    with pytest.raises(ValueError, match="dependencies unavailable"):
        activate_pack(
            _manifest(),
            session_id="session-1",
            available_tools=("tool.list",),
            available_skills=("fixture-diagnostics",),
            audit_sink=events.append,
        )
    assert [event.event_type for event in events] == [
        "capability_pack.activation_denied"
    ]


def test_activation_audits_explicit_tool_overrides() -> None:
    events = []

    active = activate_pack(
        _manifest(),
        session_id="session-1",
        available_tools=("fixture.inspect", "fixture.override", "tool.list"),
        available_skills=("fixture-diagnostics",),
        override_tools=("fixture.override",),
        audit_sink=events.append,
    )

    assert active.visible_tools == (
        "fixture.inspect",
        "fixture.override",
        "tool.list",
    )
    assert active.audit_event.event_type == "capability_pack.override_applied"
    assert events == [active.audit_event]
