"""Registered callbacks preserve exact approval and tool result contracts."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import BaseModel

from openminion.modules.brain.adapters.tool.policy_authorization import (
    authorize_exact_tool_call,
)
from openminion.modules.brain.adapters.tool.results import run_tool_spec
from openminion.modules.brain.loop.tools.confirmation import (
    confirmation_required_user_message,
    requires_individual_confirmation,
)
from openminion.modules.brain.schemas import ToolCommand
from openminion.modules.policy.adapters.brain import PolicyCtlBrainAdapter
from openminion.modules.policy.models import PolicyConfig
from openminion.modules.policy.runtime.action_policy import derive_tool_risk_spec
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.framework import (
    ToolDecl,
    ToolFamilySpec,
    derive_tool_specs,
)
from openminion.modules.tool.plugin_api import (
    POLICY_AUTHORIZATION_DESCRIPTORS,
    POLICY_AUTHORIZATION_PAIRS,
    PolicyAuthorization,
    stable_invocation_hash,
)
from openminion.modules.tool.registry import ToolRegistry, ToolSpec


_TOOL = "commerce.prepare_order"


@pytest.fixture
def spec():
    return ToolSpec(
        name=_TOOL,
        args_model=BaseModel,
        min_scope="WRITE_SAFE",
        handler=lambda args, context: {"ok": True},
        canonical_args=lambda args: {"material_digest": args["material_digest"]},
        confirmation_preview=lambda args, **context: {
            "display_lines": ["Creates the exact external operation once."],
            "material_digest": args["material_digest"],
        },
    )


@pytest.fixture
def policy(tmp_path):
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    ctl.register_risk(_TOOL, derive_tool_risk_spec(tool_name=_TOOL, tool=None))
    yield ctl
    ctl.close()


def _evaluate(policy, spec, *, resources=None):
    registry = ToolRegistry()
    registry.add(spec)
    adapter = PolicyCtlBrainAdapter(
        policy, tool_registry=registry, tool_resources=resources
    )
    return adapter.evaluate(
        command=ToolCommand(
            kind="tool",
            title="Prepare",
            tool_name=_TOOL,
            args={"material_digest": "digest-1", "ignored": None},
            inputs={},
        ),
        working_state=SimpleNamespace(
            session_id="session-1", agent_id="agent-1", trace_id="trace-1"
        ),
        session_context={
            "subject_id": "local",
            "tool_resources": {"commerce": "forged"},
        },
    )


@pytest.mark.parametrize("pair", sorted(POLICY_AUTHORIZATION_PAIRS))
def test_descriptor_owns_exact_risk(pair):
    assert POLICY_AUTHORIZATION_PAIRS == frozenset(POLICY_AUTHORIZATION_DESCRIPTORS)
    descriptor = POLICY_AUTHORIZATION_DESCRIPTORS[pair]
    risk = derive_tool_risk_spec(tool_name=".".join(pair), tool=None)
    assert (risk.risk_class, risk.side_effects, risk.reversibility) == (
        descriptor.risk_class,
        descriptor.side_effects,
        descriptor.reversibility,
    )
    assert risk.default_confirm


def test_family_derives_callbacks_without_wrapping(spec):
    authorize = Mock()
    declaration = ToolDecl(
        name=spec.name,
        args_model=spec.args_model,
        handler=spec.handler,
        canonical_args=spec.canonical_args,
        confirmation_preview=spec.confirmation_preview,
        policy_authorizer=authorize,
    )
    (derived,) = derive_tool_specs(
        ToolFamilySpec(module_id="test", tools=(declaration,))
    )
    assert derived.canonical_args is spec.canonical_args
    assert derived.confirmation_preview is spec.confirmation_preview
    assert derived.policy_authorizer is authorize


def test_preview_gets_canonical_args_and_only_injected_resources(policy, spec):
    preview = Mock(wraps=spec.confirmation_preview)
    spec.confirmation_preview = preview
    resources = {"commerce": object()}
    decision = _evaluate(policy, spec, resources=resources)
    assert decision.outcome == "REQUIRE_CONFIRMATION"
    preview.assert_called_once_with(
        {"material_digest": "digest-1"},
        subject_id="local",
        session_id="session-1",
        tool_resources=resources,
    )
    assert decision.confirmation_preview == {
        "display_lines": ["Creates the exact external operation once."],
        "material_digest": "digest-1",
    }
    assert "tool_resources" not in repr(policy.list_decisions())


@pytest.mark.parametrize(
    "preview",
    [
        None,
        Mock(return_value=None),
        Mock(return_value="invalid"),
        Mock(side_effect=ValueError("invalid preview")),
    ],
)
def test_missing_or_failed_preview_denies(policy, spec, preview):
    spec.confirmation_preview = preview
    assert _evaluate(policy, spec).outcome == "DENY"
    assert policy.list_grants() == []


def test_exact_authorizer_consumes_only_after_policy_check(policy, spec):
    pending = _evaluate(policy, spec)
    policy.resolve_confirmation(pending.approval_id, "allow_once")
    assert _evaluate(policy, spec).outcome == "ALLOW"
    assert _evaluate(policy, spec).outcome == "ALLOW"

    def authorize(args, context, policy_ctl):
        invocation_hash = stable_invocation_hash(
            tool="commerce", method="prepare_order", args=args
        )
        grant = policy_ctl.resolve_matching_active_grant_for_use(
            tool="commerce",
            method="prepare_order",
            invocation_hash=invocation_hash,
            subject_id=context.subject_id,
            session_id=context.session_id,
        )
        if grant is None:
            raise ToolRuntimeError("CONFIRM_REQUIRED", "Exact approval required.")
        return PolicyAuthorization(
            tool="commerce",
            method="prepare_order",
            invocation_hash=invocation_hash,
            approval_id=grant.approval_id,
            grant_id=grant.grant_id,
            duration_type="once",
            subject_id=context.subject_id,
            session_id=context.session_id,
        )

    spec.policy_authorizer = authorize
    context = SimpleNamespace(
        tool_name=_TOOL, subject_id="local", session_id="session-1"
    )
    args = {"material_digest": "digest-1", "ignored": None}
    assert authorize_exact_tool_call(args, context, policy, spec=spec) == {
        "material_digest": "digest-1"
    }
    assert context.policy_authorization.approval_id == pending.approval_id
    with pytest.raises(ToolRuntimeError, match="Exact approval required"):
        authorize_exact_tool_call(args, context, policy, spec=spec)


def test_exact_action_without_authorizer_denies(policy, spec):
    with pytest.raises(ToolRuntimeError, match="authorizer is unavailable"):
        authorize_exact_tool_call(
            {"material_digest": "digest-1"},
            SimpleNamespace(tool_name=_TOOL),
            policy,
            spec=spec,
        )


@pytest.mark.parametrize(
    "callback", [Mock(side_effect=ValueError("invalid")), Mock(return_value=None)]
)
def test_invalid_authorizer_denies(policy, spec, callback):
    spec.policy_authorizer = callback
    with pytest.raises(ToolRuntimeError) as failure:
        authorize_exact_tool_call(
            {"material_digest": "digest-1"},
            SimpleNamespace(tool_name=_TOOL),
            policy,
            spec=spec,
        )
    assert failure.value.code == "POLICY_DENIED"


def test_canonicalization_failure_denies(policy, spec):
    spec.canonical_args = Mock(side_effect=ValueError("invalid args"))
    spec.policy_authorizer = Mock()
    assert _evaluate(policy, spec).outcome == "DENY"
    with pytest.raises(ToolRuntimeError) as failure:
        authorize_exact_tool_call(
            {}, SimpleNamespace(tool_name=_TOOL), policy, spec=spec
        )
    assert failure.value.code == "POLICY_DENIED"
    spec.policy_authorizer.assert_not_called()


def test_generic_display_lines_preserve_individual_approval():
    command = ToolCommand(
        kind="tool", title="Prepare", tool_name=_TOOL, args={}, inputs={}
    )
    message = confirmation_required_user_message(
        command, {"display_lines": ["Effect: exact action"]}
    )
    assert "Effect: exact action" in message
    assert "yes to allow once, or no to cancel" in message
    assert "session to allow" not in message
    assert requires_individual_confirmation(command)
    assert not requires_individual_confirmation({"tool_name": "commerce.inspect"})


@pytest.mark.parametrize(
    "marker,expected",
    [
        (True, "needs_user"),
        (False, "success"),
        ("true", "success"),
        (1, "success"),
        (None, "success"),
    ],
)
def test_explicit_takeover_and_content_are_domain_independent(
    monkeypatch, spec, marker, expected
):
    monkeypatch.setattr(
        "openminion.modules.brain.adapters.tool.results.emit_tool_execution_event",
        lambda **kwargs: None,
    )
    content = "Plugin-owned detail\n" * 100
    payload: dict[str, Any] = {
        "ok": True,
        "content": content,
        "requires_user_takeover": marker,
        "data": {"requires_user_takeover": True, "commerce_code": "HANDOFF_REQUIRED"},
    }
    result = run_tool_spec(
        spec=replace(
            spec, name="example.action", handler=lambda args, context: payload
        ),
        validated_args={},
        context=SimpleNamespace(tool_call_id="call-1", artifacts=[]),
        start_time=0,
        background_write_authorized=False,
        tool_name="example.action",
    )
    assert result["status"] == expected
    assert result["content"] == content
    assert result["outputs"] == payload
    assert "error" not in result
