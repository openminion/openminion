from __future__ import annotations

from openminion.modules.skill.models import SkillPackage
from openminion.modules.tool.plugin_contract import ToolCapabilities


def test_skill_package_round_trips_capability_pack_metadata() -> None:
    package = SkillPackage.from_dict(
        {
            "skill_id": "ops-diagnostics",
            "name": "Ops diagnostics",
            "teaches": ["inspect hosts"],
            "requires_tools": ["ops.host.snapshot"],
            "safe_for_domains": ["system-operations"],
            "forbidden_claims": ["healthy without evidence"],
            "evidence_expectations": ["operation evidence id"],
        }
    )
    restored = SkillPackage.from_dict(package.to_dict())
    assert restored.requires_tools == ["ops.host.snapshot"]
    assert restored.forbidden_claims == ["healthy without evidence"]


def test_tool_capabilities_accept_pack_policy_metadata() -> None:
    capabilities = ToolCapabilities(
        risk_level="low",
        side_effects="none",
        approval_required_for=("production",),
        result_contract="HostSnapshot",
        timeout_policy="bounded-30s",
        audit_events=("ops.observed",),
    )
    assert capabilities.result_contract == "HostSnapshot"
    assert capabilities.approval_required_for == ("production",)
