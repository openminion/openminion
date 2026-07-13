from __future__ import annotations

import pytest

from openminion.modules.capability_pack import (
    CapabilityScenario,
    activate_pack,
    business_support_manifest,
    evaluate_scenario,
)


@pytest.mark.parametrize(
    ("scenario_id", "verb", "scope", "expected"),
    [
        ("read-ticket", "read", "support.ticket.read", "allow"),
        ("draft-reply", "write", "support.reply.draft", "allow"),
        ("send-reply", "external_send", "support.reply.send", "ask"),
        ("execute-refund", "money_movement", "support.refund.execute", "ask"),
        ("delete-ticket", "destructive", "support.ticket.delete", "deny"),
    ],
)
def test_business_support_policy_scenarios(
    scenario_id: str,
    verb: str,
    scope: str,
    expected: str,
) -> None:
    manifest = business_support_manifest()
    result = evaluate_scenario(
        manifest.policy_profile,
        CapabilityScenario(
            scenario_id=scenario_id,
            verb=verb,
            capability_scope=scope,
            expected_decision=expected,
        ),
    )
    assert result.status == "pass"


def test_business_support_evaluation_requires_claim_evidence() -> None:
    manifest = business_support_manifest()
    missing = evaluate_scenario(
        manifest.policy_profile,
        CapabilityScenario(
            scenario_id="customer-lookup-evidence",
            verb="read",
            capability_scope="support.customer.read",
            expected_decision="allow",
            evidence_required=True,
        ),
    )
    assert missing.status == "fail"
    assert missing.reason == "required evidence was not produced"


def test_business_support_activation_exposes_only_pack_dependencies() -> None:
    manifest = business_support_manifest()
    active = activate_pack(
        manifest,
        session_id="support-session",
        available_tools=(
            *(tool.tool_id for tool in manifest.tools),
            "tool.list",
            "unrelated.tool",
        ),
        available_skills=(
            *(skill.skill_id for skill in manifest.skills),
            "unrelated-skill",
        ),
    )
    assert "unrelated.tool" not in active.visible_tools
    assert "unrelated-skill" not in active.visible_skills
