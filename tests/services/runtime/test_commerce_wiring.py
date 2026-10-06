from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.base.config.runtime.tool_family import CommerceToolRuntimeConfig
from openminion.modules.brain.adapters.tool.execution_context import (
    ToolExecutionContextBuilder,
)
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.commerce.constants import COMMERCE_LOCAL_SUBJECT_ID
from openminion.modules.tool import Policy, ToolRegistry, ToolSpec
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.dependencies import resolve_commerce_runtime
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.services.runtime.bootstrap import (
    build_commerce_runtime,
    resolve_commerce_runtime as resolve_bootstrap_commerce_runtime,
)
from tests.helpers.commerce_runtime import (
    FixtureSecretService,
    build_fixture_commerce_runtime,
)


def _spec(captured: list[tuple[object, str]]) -> ToolSpec:
    def handler(_args, context):
        service = resolve_commerce_runtime(context)
        captured.append((service, context.subject_id))
        return {"ok": True, "service_id": id(service)}

    return ToolSpec(
        name="fixture.commerce_context",
        args_model=dict,
        min_scope="READ_ONLY",
        handler=handler,
    )


def test_tool_execution_paths_share_commerce_runtime_and_trusted_subject(
    tmp_path,
) -> None:
    commerce_runtime, _ = build_fixture_commerce_runtime()
    captured: list[tuple[object, str]] = []
    spec = _spec(captured)
    registry = ToolRegistry()
    registry.add(spec)
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=registry,
        commerce_runtime=commerce_runtime,
        policy={"tools": {"allow_exact": [spec.name]}},
    )

    direct = adapter.execute(
        command={"tool_name": spec.name, "args": {}},
        session_id="session-1",
        trace_id="trace-1",
    )
    builder = ToolExecutionContextBuilder(
        agent_id="agent-1",
        commerce_runtime=commerce_runtime,
        memory_service=None,
        sandbox_runner=None,
        security_lab_runner=None,
        security_lab_config=None,
        identity_security_lab_facts=None,
        exposure_service=registry.exposure_service,
    )
    bridged = execute_tool_spec_call(
        tool=spec,
        arguments={},
        context=builder.build(
            policy=Policy(raw={}),
            session_id="session-1",
            trace_id="trace-1",
            orchestration_metadata=None,
            replay_confirmation_metadata=None,
        ),
    )

    assert direct["status"] == "success"
    assert bridged.ok is True
    assert captured == [
        (commerce_runtime, COMMERCE_LOCAL_SUBJECT_ID),
        (commerce_runtime, COMMERCE_LOCAL_SUBJECT_ID),
    ]


def test_commerce_dependency_resolver_fails_before_handler_or_provider(tmp_path) -> None:
    called = False

    def handler(_args, context):
        nonlocal called
        service = resolve_commerce_runtime(context)
        called = True
        return {"ok": True, "service_id": id(service)}

    spec = ToolSpec(
        name="fixture.commerce_dependency",
        args_model=dict,
        min_scope="READ_ONLY",
        handler=handler,
    )
    registry = ToolRegistry()
    registry.add(spec)
    missing = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=registry,
        policy={"tools": {"allow_exact": [spec.name]}},
    ).execute(
        command={"tool_name": spec.name, "args": {}},
        session_id="session-1",
        trace_id="trace-1",
    )

    assert missing["error"]["code"] == "DEPENDENCY_MISSING"
    assert called is False

    commerce_runtime, _ = build_fixture_commerce_runtime()
    result = execute_tool_spec_call(
        tool=spec,
        arguments={},
        context=ToolExecutionContext(
            channel="console",
            target="session-1",
            session_id="session-1",
            commerce_runtime=commerce_runtime,
        ),
    )
    assert result.data["error_code"] == "POLICY_DENIED"
    assert result.data["details"]["commerce_code"] == "SUBJECT_UNAVAILABLE"
    assert called is False


def test_bootstrap_builds_and_resolves_only_explicit_commerce_runtime() -> None:
    _, provider = build_fixture_commerce_runtime()
    config = CommerceToolRuntimeConfig(
        enabled=True,
        provider="fixture",
        base_url="https://fixture.invalid",
        merchant_id="merchant-fixture",
        provider_secret_key="provider-secret",
        buyer_profile_record_id="buyer-profile",
        payment_token_record_id="payment-token",
    )
    runtime = build_commerce_runtime(
        provider=provider,
        config=config,
        secret_service=FixtureSecretService(),
    )

    assert runtime is not None
    assert resolve_bootstrap_commerce_runtime(
        SimpleNamespace(commerce_runtime=runtime)
    ) is runtime
    assert resolve_bootstrap_commerce_runtime(SimpleNamespace()) is None


def test_subject_resolver_rejects_nonlocal_subject() -> None:
    commerce_runtime, _ = build_fixture_commerce_runtime()
    with pytest.raises(ToolRuntimeError) as exc_info:
        resolve_commerce_runtime(
            SimpleNamespace(commerce_runtime=commerce_runtime, subject_id="remote")
        )
    assert exc_info.value.code == "POLICY_DENIED"
    assert exc_info.value.details["commerce_code"] == "SUBJECT_UNAVAILABLE"
