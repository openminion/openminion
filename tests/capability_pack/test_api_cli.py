from __future__ import annotations

import json

from typer.testing import CliRunner

from openminion.modules.capability_pack.api import (
    activate_registered_pack,
    list_packs,
    smoke_pack,
)
from openminion.modules.capability_pack.cli import app
from openminion.modules.capability_pack.evaluation import CapabilityScenario
from openminion.modules.capability_pack.fixtures import business_support_manifest
from openminion.modules.capability_pack.registry import CapabilityPackRegistry


def _registry() -> CapabilityPackRegistry:
    registry = CapabilityPackRegistry()
    registry.register(business_support_manifest())
    return registry


def test_api_activation_and_eval_emit_auditable_bounded_results() -> None:
    registry = _registry()
    manifest = registry.get("business-support-fixture")

    active = activate_registered_pack(
        registry,
        pack_id=manifest.pack_id,
        session_id="support-session",
        available_tools=(
            *(tool.tool_id for tool in manifest.tools),
            *manifest.baseline_tools,
        ),
        available_skills=(skill.skill_id for skill in manifest.skills),
    )
    results = smoke_pack(
        manifest,
        (
            CapabilityScenario(
                scenario_id="refuse-delete",
                verb="destructive",
                capability_scope="support.ticket.delete",
                expected_decision="deny",
            ),
            CapabilityScenario(
                scenario_id="require-send-approval",
                verb="external_send",
                capability_scope="support.reply.send",
                expected_decision="ask",
            ),
            CapabilityScenario(
                scenario_id="no-completion-without-evidence",
                verb="read",
                capability_scope="support.customer.read",
                expected_decision="allow",
                evidence_required=True,
            ),
        ),
    )

    assert active.audit_event.event_type == "capability_pack.activated"
    assert active.audit_event.pack_id == manifest.pack_id
    assert [result.status for result in results] == ["pass", "pass", "fail"]
    assert results[-1].reason == "required evidence was not produced"


def test_cli_list_matches_api_registry_contract() -> None:
    expected = [item.model_dump(mode="json") for item in list_packs(_registry())]

    result = CliRunner().invoke(app, ["list"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == expected
