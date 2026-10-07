from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.brain.loop.tools.plan import _validate_workflow_id
from openminion.modules.context.schemas import BuildConstraints, BuildPackRequest
from openminion.modules.context.service import ContextCtlService
from openminion.modules.policy.models import PolicyConfig
from openminion.modules.policy.runtime.action_policy import derive_tool_risk_spec
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.modules.skill.errors import SkillError
from openminion.modules.skill.interfaces import SkillIngestAuthority
from openminion.modules.skill.runtime.parser import build_recipe, parse_markdown
from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.task.plan import TaskPlan
from openminion.tools.commerce import ALL_COMMERCE_TOOLS
from tests.commerce.test_place_order import _bootstrap
from tests.helpers.commerce_runtime import build_fixture_commerce_runtime
from tests.skill.test_skill_preroute_to_context import (
    _ArtifactClient,
    _IdentityClient,
    _MemoryClient,
    _SessionClient,
)


SKILL_PATH = (
    Path(__file__).resolve().parents[2] / "examples/skills/commerce-order-care/SKILL.md"
)
KNOWN_TOOLS = [*ALL_COMMERCE_TOOLS, "task.watch"]


@pytest.fixture
def skill(tmp_path):
    data_root = tmp_path / ".openminion"
    ctl = Skill(
        {
            "skill": {
                "sqlite_path": str(data_root / "skill.db"),
                "blob_root": str(data_root / "blob"),
                "fallback_root": str(data_root / "fallback"),
                "known_tools": KNOWN_TOOLS,
                "wal": False,
            }
        }
    )
    try:
        yield ctl
    finally:
        ctl.close()


def _admit(ctl):
    authority = SkillIngestAuthority.local_operator(
        surface="test.commerce.workflow", principal_id="local:test"
    )
    skill_id, version, warnings = ctl.ingest_file(SKILL_PATH, authority=authority)
    assert "admission.pending" in warnings
    assert not any(warning.startswith("lint.error:") for warning in warnings)
    assert ctl.workflow_catalog().entries == []
    ctl.admit_skill_version(
        skill_id=skill_id,
        version_hash=version,
        expected_active_version_hash=None,
        target_status="verified",
        reason="reviewed commerce fixture workflow",
        authority=authority,
    )
    return ctl.get_workflow(f"workflow.{skill_id}")


def test_skill_admission_catalog_and_model_plan_are_version_bound(skill):
    entry = _admit(skill)
    package = skill.get_skill(entry.skill_id, entry.version_hash)
    assert package.recipe is not None
    assert entry.risk_class == "high"
    assert [step.tool_id for step in entry.workflow.steps] == [
        "commerce.inspect",
        "commerce.prepare_order",
        "commerce.place_order",
        "commerce.inspect",
        "commerce.prepare_order_action",
        "commerce.apply_order_action",
        "commerce.inspect",
        "task.watch",
    ]
    # The existing bridge validates a model-authored plan; it does not run recipes.
    plan = TaskPlan(
        plan_id="care-plan",
        objective="Review and cancel the requested existing order",
        workflow_id=entry.workflow.workflow_id,
        workflow_version_hash=entry.version_hash,
        steps=[
            {"step_id": "inspect-order", "description": "Read the current order"},
            {
                "step_id": "prepare-care",
                "description": "Review exact cancellation terms",
                "depends_on": ["inspect-order"],
            },
            {
                "step_id": "apply-care",
                "description": "Request one-time approval for the prepared action",
                "depends_on": ["prepare-care"],
            },
        ],
    )
    assert {step.step_id for step in plan.steps} <= {
        step.step_id for step in entry.workflow.steps
    }
    restored = TaskPlan.model_validate_json(plan.model_dump_json())
    assert restored == plan and restored.workflow_version_hash == package.version_hash
    context = SimpleNamespace(
        state=SimpleNamespace(agent_id="agent.test"), skill_api=skill
    )
    assert (
        _validate_workflow_id(
            context,
            workflow_id=restored.workflow_id,
            workflow_version_hash=restored.workflow_version_hash,
        )
        is None
    )
    rejected = _validate_workflow_id(
        context, workflow_id=restored.workflow_id, workflow_version_hash="stale-version"
    )
    assert rejected.error.code == "PLAN_WORKFLOW_VERSION_CONFLICT"


def test_runtime_ingest_cannot_self_admit_the_workflow(skill):
    skill_id, version, _ = skill.ingest_file(SKILL_PATH)
    assert skill.get_skill(skill_id, version).recipe is not None
    assert skill.workflow_catalog().entries == []
    with pytest.raises(SkillError, match="SKILL_OPERATOR_AUTH_REQUIRED"):
        skill.admit_skill_version(
            skill_id=skill_id,
            version_hash=version,
            expected_active_version_hash=None,
            target_status="verified",
            reason="runtime cannot approve itself",
            authority=SkillIngestAuthority.runtime(
                surface="test.commerce.runtime", source_kind="local"
            ),
        )
    assert skill.workflow_catalog().entries == []


@pytest.mark.parametrize("purpose", ["plan", "act"])
def test_admitted_skill_renders_pinned_version_into_ordinary_context(skill, purpose):
    entry = _admit(skill)
    service = ContextCtlService(
        identityctl=_IdentityClient(),
        sessctl=_SessionClient(),
        memctl=_MemoryClient(),
        artifactctl=_ArtifactClient(),
        skillctl=skill,
    )
    request = BuildPackRequest(
        session_id="commerce-context",
        agent_id="agent.test",
        purpose=purpose,
        query="Review the requested merchant order before taking action",
        constraints=BuildConstraints(
            skill_refs=[
                {"skill_id": entry.skill_id, "version_hash": entry.version_hash}
            ]
        ),
    )
    with patch.object(skill, "render_snippet", wraps=skill.render_snippet) as render:
        pack = service.build_pack(request)
    render.assert_called_once()
    assert render.call_args.kwargs["skill_id"] == entry.skill_id
    assert render.call_args.kwargs["version_hash"] == entry.version_hash
    assert render.call_args.kwargs["purpose"] == purpose
    snippet, _ = skill.render_snippet(**render.call_args.kwargs)
    assert f"Skill ID: {entry.skill_id}" in snippet
    assert "ordinary policy confirmation" in snippet
    skill_segments = [
        segment for segment in pack.segments if snippet in segment.content
    ]
    assert len(skill_segments) == 1
    assert any(snippet in message.content for message in pack.messages)


def test_recipe_requires_structured_steps_and_known_tool_bindings():
    frontmatter = parse_markdown(SKILL_PATH.read_text())[0]
    recipe, warnings = build_recipe(
        front_matter=frontmatter,
        skill_name="commerce-order-care",
        risk_class="high",
        known_tools=["commerce.inspect"],
    )
    assert warnings == []
    assert recipe is not None
    assert {step.tool_id for step in recipe.steps} == {"commerce.inspect", None}
    del frontmatter["recipe"]
    assert build_recipe(
        front_matter=frontmatter,
        skill_name="commerce-order-care",
        risk_class="high",
        known_tools=KNOWN_TOOLS,
    ) == (None, [])


def _approve_once(policy, runtime, command):
    tool_name = command["tool_name"]
    policy.register_risk(
        tool_name, derive_tool_risk_spec(tool_name=tool_name, tool=None)
    )
    preview = runtime.resolve_confirmation_preview(
        tool_name=tool_name,
        args=command["args"],
        subject_id="local",
        session_id="session-1",
    )
    tool, method = tool_name.split(".")
    pending = policy.check(
        {
            "tool": tool,
            "method": method,
            "args": command["args"],
            "invocation_id": tool_name,
        },
        {"subject_id": "local", "session_id": "session-1", "trace_id": tool_name},
        confirmation_preview=preview.model_dump(mode="json"),
    )
    assert pending.decision == "REQUIRE_CONFIRM"
    return policy.resolve_confirmation(pending.approval_id, "allow_once")


@pytest.mark.parametrize("permission_mode", ["ask", "auto", "bypass"])
def test_admitted_workflow_still_requires_separate_one_time_transaction_approval(
    skill, tmp_path, permission_mode
):
    entry = _admit(skill)
    steps = {step.step_id: step.tool_id for step in entry.workflow.steps}
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    policy = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    bootstrap = _bootstrap(tmp_path, enabled=True, writes_enabled=True)
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=bootstrap.registry,
        tool_resources={"commerce": runtime},
        policy_ctl=policy,
        policy={"tools": {"allow_exact": list(ALL_COMMERCE_TOOLS)}},
    )
    args = {"items": [{"offer_id": "offer-1", "variant_id": "standard", "quantity": 1}]}
    command = {
        "tool_name": steps["prepare-order"],
        "args": args,
        "inputs": {
            "permission_mode": permission_mode,
            "confirmation_source": "policy_replay",
            "confirmation_grant_id": "not-a-grant",
        },
    }
    try:
        denied = adapter.execute(
            command=command, session_id="session-1", trace_id="denied"
        )
        assert provider.ledger == []
        if permission_mode != "ask":
            assert denied["error"]["code"] == "POLICY_DENIED", denied
            assert (
                denied["error"]["details"]["commerce_code"] == "POLICY_MODE_UNSUPPORTED"
            )
            return
        assert denied["error"]["code"] == "CONFIRM_REQUIRED", denied
        command["inputs"]["confirmation_grant_id"] = _approve_once(
            policy, runtime, command
        )
        wrong_session = adapter.execute(
            command=command, session_id="session-2", trace_id="wrong-session"
        )
        assert wrong_session["error"]["code"] == "CONFIRM_REQUIRED"
        assert provider.ledger == []
        prepared = adapter.execute(
            command=command, session_id="session-1", trace_id="allowed"
        )
        assert prepared["status"] == "success", prepared
        replay = adapter.execute(
            command=command, session_id="session-1", trace_id="replay"
        )
        assert replay["error"]["code"] == "CONFIRM_REQUIRED"
        record = runtime.order_store.get_preparation(
            "local", prepared["outputs"]["data"]["preparation_ref"]
        )
        place_command = {
            **command,
            "tool_name": steps["place-order"],
            "args": {
                "preparation_ref": record.preparation_id,
                "preparation": record.prepared.model_dump(mode="json"),
                "preparation_digest": record.prepared.preparation_digest,
            },
        }
        placement = adapter.execute(
            command=place_command,
            session_id="session-1",
            trace_id="separate-approval",
        )
        assert placement["error"]["code"] == "CONFIRM_REQUIRED"
        assert [item.operation for item in provider.ledger] == ["prepare_order"]
        place_command["inputs"]["confirmation_grant_id"] = _approve_once(
            policy, runtime, place_command
        )
        placed = adapter.execute(
            command=place_command, session_id="session-1", trace_id="place-approved"
        )
        assert placed["status"] == "success", placed
        order = runtime.order_store.get_order(
            "local", placed["outputs"]["data"]["order_ref"]
        )
        assert order.placement_attempt_id is not None
        replay = adapter.execute(
            command=place_command, session_id="session-1", trace_id="place-replay"
        )
        assert replay["error"]["code"] == "CONFIRM_REQUIRED"
        assert [item.operation for item in provider.ledger] == [
            "prepare_order",
            "place_order",
        ]
    finally:
        runtime.order_store.close()
        policy.close()


@pytest.mark.parametrize("injected", [False, True])
def test_tool_resource_is_trusted_injection_not_model_input(tmp_path, injected):
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    bootstrap = _bootstrap(tmp_path, enabled=True, writes_enabled=True)
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=bootstrap.registry,
        tool_resources={"commerce": runtime} if injected else {},
        policy={"tools": {"allow_exact": list(ALL_COMMERCE_TOOLS)}},
    )
    try:
        result = adapter.execute(
            command={
                "tool_name": "commerce.inspect",
                "args": {"kind": "product", "product_id": "product-1"},
                "inputs": {
                    "tool_resources": {"commerce": {"merchant_id": "untrusted"}},
                    "subject_id": "other",
                },
            },
            session_id="session-1",
            trace_id="resource-boundary",
        )
        if injected:
            assert result["status"] == "success", result
            assert provider.inspect_calls[0].merchant_id == "merchant-fixture"
        else:
            assert result["error"]["code"] == "DEPENDENCY_MISSING"
            assert provider.inspect_calls == []
        assert provider.ledger == []
    finally:
        runtime.order_store.close()
