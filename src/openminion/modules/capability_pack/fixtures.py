from __future__ import annotations

from .schemas import (
    CapabilityPackManifest,
    PackPolicyProfile,
    PackPolicyRule,
    PackSkillMetadata,
    PackToolMetadata,
    ToolRiskLevel,
    ToolSideEffects,
)


def business_support_manifest() -> CapabilityPackManifest:
    return CapabilityPackManifest(
        pack_id="business-support-fixture",
        domain="customer-support",
        version="1.0.0",
        skills=(
            PackSkillMetadata(
                skill_id="support-response-policy-fixture",
                teaches=("draft support resolutions from fixture evidence",),
                requires_tools=(
                    "customer.lookup.fixture",
                    "ticket.lookup.fixture",
                    "reply.draft.fixture",
                    "refund.preview.fixture",
                ),
                safe_for_domains=("customer-support",),
                forbidden_claims=("never send or refund without approval",),
                evidence_expectations=("source record ids", "draft or preview id"),
            ),
        ),
        tools=(
            _tool("customer.lookup.fixture", "support.customer.read", "CustomerRecord"),
            _tool("ticket.lookup.fixture", "support.ticket.read", "TicketRecord"),
            _tool("reply.draft.fixture", "support.reply.draft", "ReplyDraft"),
            _tool("refund.preview.fixture", "support.refund.preview", "RefundPreview"),
        ),
        policy_profile=PackPolicyProfile(
            profile_id="business-support-default",
            rules=(
                PackPolicyRule(verb="read", decision="allow"),
                PackPolicyRule(verb="write", decision="allow"),
                PackPolicyRule(verb="external_send", decision="ask"),
                PackPolicyRule(verb="money_movement", decision="ask"),
                PackPolicyRule(verb="destructive", decision="deny"),
            ),
        ),
        eval_suite="business-support-contracts-v1",
        audit_profile="business-support-audit-v1",
        registry_ref="business-support-fixture-registry-v1",
        risk_model="business-support-risk-v1",
        baseline_tools=("tool.list",),
    )


def _tool(
    tool_id: str,
    capability: str,
    result_contract: str,
    *,
    risk_level: ToolRiskLevel = "low",
    side_effects: ToolSideEffects = "none",
    approvals: tuple[str, ...] = (),
) -> PackToolMetadata:
    return PackToolMetadata(
        tool_id=tool_id,
        capabilities=(capability,),
        risk_level=risk_level,
        side_effects=side_effects,
        approval_required_for=approvals,
        result_contract=result_contract,
        timeout_policy="bounded-30s",
        audit_events=(f"{capability}.completed",),
    )
