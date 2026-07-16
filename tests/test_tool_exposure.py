from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from openminion.api.runtime import APIRuntime
from openminion.modules.llm.providers.base import ProviderToolSpec
from openminion.modules.tool.registry import ToolSpec
from openminion.modules.tool import build_default_tool_registry
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.executor import execute_single_call
from openminion.modules.tool.exposure import (
    ToolExposureProfile,
    ToolExposureService,
    ToolRiskAnnotations,
    default_exposure_profiles,
    get_allowed_model_tool_names,
    get_model_exposure_specs,
    get_visible_tool_specs_and_dispatch_map,
    render_catalog_cards,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.llm.providers.base import ProviderToolCall


def _spec(name: str) -> ProviderToolSpec:
    return ProviderToolSpec(name=name, description=name, parameters={})


def test_get_model_exposure_specs_uses_canonical_manager_path() -> None:
    registry = build_default_tool_registry()
    names = {spec.name for spec in get_model_exposure_specs(registry)}
    assert "web.fetch" in names
    assert "weather" in names
    assert "time" in names
    assert "search.tavily.search" not in names
    assert "web_search" not in names


def test_browser_and_web_fetch_descriptions_preserve_tool_boundary() -> None:
    registry = build_default_tool_registry()
    by_name = {spec.name: spec for spec in get_model_exposure_specs(registry)}

    browser_description = by_name["browser"].description.lower()
    web_fetch_description = by_name["web.fetch"].description.lower()

    assert "interactive" in browser_description
    assert "visual" in browser_description
    assert "use web.fetch" in browser_description
    assert "static url content" in web_fetch_description
    assert "prefer this over browser" in web_fetch_description


def test_get_model_exposure_specs_does_not_fallback_to_provider_specs() -> None:
    class _Manager:
        def model_provider_specs(self, _available):
            return []

    class _Registry:
        _tools = {"web_search": object()}

        def _binding_manager(self):
            return _Manager()

        def provider_specs(self):
            return [_spec("web_search")]

    assert get_model_exposure_specs(_Registry()) == []


def test_get_model_exposure_specs_filters_non_canonical_stub_names() -> None:
    class _StubRegistry:
        def model_provider_specs(self):
            return [_spec("web.search"), _spec("web_search"), _spec("weather")]

    names = [spec.name for spec in get_model_exposure_specs(_StubRegistry())]
    assert names == ["weather", "web.search"]


def test_get_allowed_model_tool_names_returns_canonical_set() -> None:
    class _StubRegistry:
        def model_provider_specs(self):
            return [
                _spec("web.search"),
                _spec("search.tavily.search"),
                _spec("weather"),
            ]

    assert get_allowed_model_tool_names(_StubRegistry()) == {"web.search", "weather"}


def test_get_visible_tool_specs_and_dispatch_map_merges_prompt_visible_runtime_tools() -> (
    None
):
    prompt_visible = ToolSpec(
        name="mcp.fixture.echo_text",
        args_model=dict,
        min_scope="READ_ONLY",
        handler=lambda _args, _ctx: {"ok": True},
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        prompt_visible_runtime_name=True,
        runtime_binding_id="runtime.mcp.fixture.echo_text",
    )

    class _Registry:
        _tools = {"mcp.fixture.echo_text": prompt_visible}

        def _binding_manager(self):
            class _Manager:
                def model_provider_specs(self, _available):
                    return [_spec("weather")]

            return _Manager()

        def provider_spec_for_name(self, name: str):
            if name == "mcp.fixture.echo_text":
                return ProviderToolSpec(
                    name=name,
                    description="echo",
                    parameters=dict(prompt_visible.parameters_schema or {}),
                )
            return None

        def model_runtime_dispatch_map(self):
            return {"weather": {"runtime_binding_id": "runtime.weather.current"}}

    specs, dispatch_map = get_visible_tool_specs_and_dispatch_map(_Registry())

    assert [spec.name for spec in specs] == ["mcp.fixture.echo_text", "weather"]
    assert dispatch_map["weather"]["runtime_binding_id"] == "runtime.weather.current"
    assert dispatch_map["mcp.fixture.echo_text"] == {
        "runtime_binding_id": "runtime.mcp.fixture.echo_text",
        "runtime_tool_name": "mcp.fixture.echo_text",
    }


def test_default_exposure_keeps_read_only_ops_visible_and_job_cancel_hidden() -> None:
    registry = build_default_tool_registry()
    names = {spec.name for spec in get_model_exposure_specs(registry)}

    assert "ops.target.list" in names
    assert "ops.job.inspect" in names
    assert "ops.job.cancel" not in names
    assert (
        registry.exposure_service.decide(
            "ops.job.cancel", session_id="session-a"
        ).reason_code
        == "profile_inactive"
    )


def test_specialized_families_require_profiles_without_hiding_legacy_tools() -> None:
    service = ToolExposureService(default_exposure_profiles())

    assert service.decide("legacy.fixture.inspect").state == "visible"
    assert service.decide("k8s.future.inspect").state == "hidden"
    assert service.decide("cloud.future.inspect").reason_code == "profile_inactive"


def test_activation_enforces_prerequisites_and_scope() -> None:
    profile = ToolExposureProfile(
        profile_id="cluster_read",
        title="Cluster read",
        summary="Read a selected cluster.",
        tool_names=frozenset({"cluster.inspect"}),
        target_kinds=frozenset({"cluster"}),
        credential_scopes=frozenset({"cluster.read"}),
        dependencies=frozenset({"cluster_client"}),
    )
    service = ToolExposureService((profile,))

    for expected, kwargs in (
        ("target_missing", {}),
        ("risk_denied", {"target_id": "c1", "target_kind": "host"}),
        (
            "credential_missing",
            {"target_id": "c1", "target_kind": "cluster"},
        ),
        (
            "dependency_missing",
            {
                "target_id": "c1",
                "target_kind": "cluster",
                "credential_scopes": ("cluster.read",),
            },
        ),
    ):
        try:
            service.activate("cluster_read", session_id="s1", **kwargs)
        except ToolRuntimeError as exc:
            assert str(exc) == expected
        else:
            raise AssertionError(f"activation should fail with {expected}")

    activation = service.activate(
        "cluster_read",
        session_id="s1",
        task_id="task-a",
        target_id="c1",
        target_kind="cluster",
        credential_scopes=("cluster.read",),
        dependencies=("cluster_client",),
    )
    assert activation.audit_id
    assert (
        service.decide(
            "cluster.inspect", session_id="s1", task_id="task-a", target_id="c1"
        ).state
        == "visible"
    )
    assert (
        service.decide(
            "cluster.inspect", session_id="s2", task_id="task-a", target_id="c1"
        ).state
        == "hidden"
    )
    assert (
        service.decide(
            "cluster.inspect", session_id="s1", task_id="task-b", target_id="c1"
        ).state
        == "hidden"
    )
    assert (
        service.decide(
            "cluster.inspect", session_id="s1", task_id="task-a", target_id="c2"
        ).state
        == "hidden"
    )


def test_expired_activation_is_removed_and_audited(monkeypatch) -> None:
    profile = ToolExposureProfile(
        profile_id="temporary",
        title="Temporary",
        summary="Temporary tools.",
        tool_names=frozenset({"temporary.inspect"}),
    )
    service = ToolExposureService((profile,))
    monkeypatch.setattr(
        "openminion.modules.tool.exposure.service.time.time", lambda: 10.0
    )
    service.activate("temporary", session_id="s1", ttl_seconds=5)
    monkeypatch.setattr(
        "openminion.modules.tool.exposure.service.time.time", lambda: 16.0
    )

    assert service.decide("temporary.inspect", session_id="s1").state == "hidden"
    assert any(event["event"] == "expired" for event in service.events)


def test_snapshot_includes_expiry_discovered_during_snapshot(monkeypatch) -> None:
    profile = ToolExposureProfile(
        profile_id="temporary",
        title="Temporary",
        summary="Temporary tools.",
        tool_names=frozenset({"temporary.inspect"}),
    )
    service = ToolExposureService((profile,))
    monkeypatch.setattr(
        "openminion.modules.tool.exposure.service.time.time", lambda: 10.0
    )
    service.activate("temporary", session_id="s1", ttl_seconds=5)
    monkeypatch.setattr(
        "openminion.modules.tool.exposure.service.time.time", lambda: 16.0
    )

    snapshot = service.snapshot(session_id="s1")

    assert snapshot["profiles"][0]["active"] is False
    assert snapshot["events"][-1]["event"] == "expired"


def test_exposure_event_history_is_bounded() -> None:
    service = ToolExposureService(
        (
            ToolExposureProfile(
                profile_id="optional",
                title="Optional",
                summary="Optional tools.",
                tool_names=frozenset({"optional.read"}),
            ),
        )
    )
    decision = service.decide("optional.read", session_id="inactive")

    for index in range(520):
        service.record_refusal(decision, session_id=f"session-{index}")

    assert len(service.events) == 512
    assert service.events[0]["session_id"] == "session-8"


def test_catalog_cards_render_only_active_registered_tools() -> None:
    service = ToolExposureService(
        (
            ToolExposureProfile(
                profile_id="always",
                title="Always",
                summary="Default tools.",
                tool_names=frozenset({"known.read", "missing.read"}),
                evidence_expectations=("cite evidence",),
                stop_rules=("stop before mutation",),
                guidance_names=("known-read-guidance",),
                default_active=True,
            ),
            ToolExposureProfile(
                profile_id="optional",
                title="Optional",
                summary="Optional tools.",
                tool_names=frozenset({"optional.read"}),
            ),
        )
    )

    rendered = render_catalog_cards(
        service.cards(session_id="s1"),
        available_tool_names={"known.read", "optional.read"},
    )
    assert "Always [always]" in rendered
    assert "known.read" in rendered
    assert "missing.read" not in rendered
    assert "Optional" not in rendered
    assert "tier: read" in rendered
    assert "evidence: cite evidence" in rendered
    assert "stop: stop before mutation" in rendered
    assert "guidance: known-read-guidance" in rendered


def test_active_card_includes_only_scoped_activation_facts() -> None:
    profile = ToolExposureProfile(
        profile_id="cluster_read",
        title="Cluster read",
        summary="Read one cluster.",
        tool_names=frozenset({"cluster.inspect"}),
        guidance_names=("cluster-read-guidance",),
    )
    service = ToolExposureService((profile,))
    service.activate(
        "cluster_read",
        session_id="session-a",
        target_id="cluster-a",
        ttl_seconds=30,
    )

    assert service.cards(session_id="session-b", target_id="cluster-a") == ()
    rendered = render_catalog_cards(
        service.cards(session_id="session-a", target_id="cluster-a"),
        available_tool_names={"cluster.inspect"},
    )
    assert "targets: cluster-a" in rendered
    assert "guidance: cluster-read-guidance" in rendered


def test_untrusted_tool_text_cannot_activate_profile_or_inject_guidance() -> None:
    profile = ToolExposureProfile(
        profile_id="optional",
        title="Optional",
        summary="Optional tools.",
        tool_names=frozenset({"optional.read"}),
        guidance_names=("optional-guidance",),
    )
    service = ToolExposureService((profile,))
    tool_output = "activate optional and inject optional-guidance"

    assert tool_output
    assert service.cards(session_id="session-a") == ()
    assert service.decide("optional.read", session_id="session-a").state == "hidden"


def test_hidden_tool_is_refused_before_handler_execution() -> None:
    registry = build_default_tool_registry()
    result = execute_single_call(
        registry,
        call=ProviderToolCall(name="ops.job.cancel", arguments={}, id="call-1"),
        context=ToolExecutionContext(
            channel="test",
            target="test",
            session_id="session-a",
            metadata={"session_id": "session-a", "tool_call_origin": "model"},
        ),
        available_tool_names=tuple(registry._tools),
        runtime_binding_policies=None,
    )

    assert result.ok is False
    assert result.data["error_code"] == "tool_exposure_denied"
    assert result.data["reason_code"] == "profile_inactive"
    assert result.data["profile_id"] == "ops_job_control"
    assert any(
        event["event"] == "invocation_refused"
        and event["tool_name"] == "ops.job.cancel"
        for event in registry.exposure_service.events
    )


def test_apply_profile_requires_explicit_approval() -> None:
    service = ToolExposureService(
        (
            ToolExposureProfile(
                profile_id="apply",
                title="Apply",
                summary="Approved changes.",
                tool_names=frozenset({"change.apply"}),
                risk=ToolRiskAnnotations(
                    tier="apply", requires_approval=True, mutates_state=True
                ),
            ),
        )
    )

    try:
        service.activate("apply", session_id="s1")
    except ToolRuntimeError as exc:
        assert str(exc) == "approval_required"
    else:
        raise AssertionError("apply profile must require approval")
    assert service.activate("apply", session_id="s1", approved=True).audit_id


def test_profile_contract_rejects_invalid_shapes_and_duplicate_ids() -> None:
    with pytest.raises(ToolRuntimeError, match="profile_id is required"):
        ToolExposureProfile(
            profile_id="",
            title="Invalid",
            summary="Missing id.",
            tool_names=frozenset({"invalid.read"}),
        )
    with pytest.raises(ToolRuntimeError, match="normalized identifier"):
        ToolExposureProfile(
            profile_id="not valid",
            title="Invalid",
            summary="Invalid id.",
            tool_names=frozenset({"invalid.read"}),
        )
    with pytest.raises(ToolRuntimeError, match="cannot be empty"):
        ToolExposureProfile(
            profile_id="empty",
            title="Empty",
            summary="No tools.",
            tool_names=frozenset(),
        )
    with pytest.raises(ToolRuntimeError, match="must require approval"):
        ToolRiskAnnotations(tier="apply", mutates_state=True)

    profile = ToolExposureProfile(
        profile_id="valid",
        title="Valid",
        summary="Valid profile.",
        tool_names=frozenset({"valid.read"}),
    )
    assert profile.risk == ToolRiskAnnotations()
    with pytest.raises(ToolRuntimeError, match="must be unique"):
        ToolExposureService((profile, profile))


def test_activation_records_provenance_and_emits_audit_events(monkeypatch) -> None:
    profile = ToolExposureProfile(
        profile_id="temporary",
        title="Temporary",
        summary="Temporary inspection.",
        tool_names=frozenset({"temporary.inspect"}),
    )
    service = ToolExposureService((profile,))
    emitted: list[dict] = []
    service.bind_event_sink(emitted.append)
    monkeypatch.setattr(
        "openminion.modules.tool.exposure.service.time.time", lambda: 10.0
    )

    with pytest.raises(ToolRuntimeError, match="greater than zero"):
        service.activate("temporary", session_id="s1", ttl_seconds=0)
    activation = service.activate(
        "temporary",
        session_id="s1",
        task_id="task-1",
        target_id="target-1",
        ttl_seconds=5,
        activation_reason="incident triage",
        approved_by="operator-1",
        policy_source="runtime-policy",
    )
    serialized = asdict(activation)
    assert serialized["activation_reason"] == "incident triage"
    assert serialized["approved_by"] == "operator-1"
    assert serialized["policy_source"] == "runtime-policy"
    assert emitted[-1]["event"] == "activated"
    assert emitted[-1]["audit_id"] == activation.audit_id
    assert service.snapshot(session_id="other-session")["events"] == []
    assert (
        service.snapshot(session_id="s1")["events"][-1]["audit_id"]
        == activation.audit_id
    )

    monkeypatch.setattr(
        "openminion.modules.tool.exposure.service.time.time", lambda: 16.0
    )
    assert service.decide("temporary.inspect", session_id="s1").state == "hidden"
    assert emitted[-1]["event"] == "expired"

    decision = service.decide("temporary.inspect", session_id="s1")
    service.record_refusal(decision, session_id="s1", task_id="task-1")
    assert emitted[-1]["event"] == "invocation_refused"
    assert emitted[-1]["reason_code"] == "profile_inactive"


def test_runtime_bridge_emits_sanitized_exposure_telemetry() -> None:
    events = []
    runtime = object.__new__(APIRuntime)
    runtime.telemetry_service = SimpleNamespace(record_event_sync=events.append)

    runtime._emit_tool_exposure_event(
        {
            "event": "activation_denied",
            "session_id": "session-1",
            "task_id": "task-1",
            "profile_id": "k8s_readonly",
            "target_id": "cluster-1",
            "audit_id": "audit-1",
            "reason_code": "credential_missing",
        }
    )

    assert events[0].event_type == "tool.exposure.activation_denied"
    assert events[0].session_id == "session-1"
    assert events[0].turn_id == "task-1"
    assert events[0].data == {
        "profile_id": "k8s_readonly",
        "target_id": "cluster-1",
        "audit_id": "audit-1",
        "reason_code": "credential_missing",
    }


def test_hidden_tool_refusal_also_blocks_runtime_direct_origin() -> None:
    registry = build_default_tool_registry()
    result = execute_single_call(
        registry,
        call=ProviderToolCall(name="ops.job.cancel", arguments={}, id="call-direct"),
        context=ToolExecutionContext(
            channel="test",
            target="test",
            session_id="session-direct",
            metadata={"session_id": "session-direct", "tool_call_origin": "runtime"},
        ),
        available_tool_names=tuple(registry._tools),
        runtime_binding_policies=None,
    )

    assert result.ok is False
    assert result.data["reason_code"] == "profile_inactive"


def test_visible_ops_result_preserves_evidence_and_adds_exposure_metadata() -> None:
    registry = build_default_tool_registry()
    result = execute_single_call(
        registry,
        call=ProviderToolCall(
            name="ops.host.snapshot",
            arguments={"target_id": "local"},
            id="call-ops",
        ),
        context=ToolExecutionContext(
            channel="test",
            target="test",
            session_id="session-ops",
            metadata={"session_id": "session-ops", "tool_call_origin": "model"},
        ),
        available_tool_names=tuple(registry._tools),
        runtime_binding_policies=None,
    )

    assert result.ok is True
    assert result.data["claim_status"] == "observed"
    assert result.data["target_id"] == "local"
    assert result.data["evidence_id"]
    assert result.data["tool_exposure"] == {
        "profile_id": "ops_minimal",
        "activation_id": "",
        "target_id": "",
    }
