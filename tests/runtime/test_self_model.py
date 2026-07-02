from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.api.queries import self_model as self_model_query
from openminion.api.runtime import APIRuntime
from openminion.modules.runtime.self_model import (
    DEGRADED_GENERIC_CANDIDATE_REGISTRY_UNAVAILABLE,
    DEGRADED_IDENTITY_UNAVAILABLE,
    SELF_MODEL_HEALTH_DEGRADED,
    SELF_MODEL_HEALTH_OK,
    SELF_MODEL_HEALTH_UNAVAILABLE,
    SelfModelSnapshot,
    render_self_awareness_context_block,
    section_ok,
    section_unavailable,
)


def _snapshot(**overrides: object) -> SelfModelSnapshot:
    sections = {
        "identity": section_ok(display_name="Mini", mission="Help the operator."),
        "capabilities": section_ok(provider="echo", tool_count=2, enabled_tool_count=1),
        "policy": section_ok(permission_mode="ask", sandbox="workspace"),
        "memory_state": section_ok(provider="SQLiteMemoryStore"),
        "context_state": section_ok(budget_total=4096),
        "knowledge_state": section_ok(providers=[]),
        "improvement_state": section_ok(policy="never", promotion_posture="bsil_only"),
    }
    sections.update(overrides)
    return SelfModelSnapshot.from_sections(agent_id="mini", **sections)


def test_self_model_snapshot_composes_ok_health() -> None:
    snapshot = _snapshot()

    assert snapshot.health == SELF_MODEL_HEALTH_OK
    assert snapshot.agent_id == "mini"
    assert snapshot.model_dump(mode="json")["identity"]["facts"]["display_name"] == "Mini"


def test_self_model_snapshot_collects_unavailable_reason() -> None:
    snapshot = _snapshot(
        identity=section_unavailable(DEGRADED_IDENTITY_UNAVAILABLE, agent_id="mini")
    )

    assert snapshot.health == SELF_MODEL_HEALTH_UNAVAILABLE
    assert snapshot.degraded_reasons == [DEGRADED_IDENTITY_UNAVAILABLE]


def test_render_self_awareness_context_block_redacts_secret_like_values() -> None:
    snapshot = _snapshot(
        policy=section_ok(
            permission_mode="ask",
            sandbox="workspace",
            api_key="should-not-leak",
            nested={"token": "also-secret"},
        )
    )

    rendered = render_self_awareness_context_block(snapshot)

    assert rendered.startswith("[SELF AWARENESS]")
    assert "should-not-leak" not in rendered
    assert "also-secret" not in rendered
    assert "api_key" not in rendered


def test_runtime_self_model_reports_bsil_only_when_candidate_registry_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        self_model_query,
        "build_capability_report",
        lambda *_args, **_kwargs: {
            "providers": {"selected": "echo"},
            "tools": {"counts": {"total": 2}, "inventory": [{"enabled": True}]},
            "modes": {},
            "thinking": {},
            "plugins": {},
            "mcp": {},
        },
    )
    monkeypatch.setattr(
        self_model_query,
        "build_runtime_posture_report",
        lambda *_args, **_kwargs: {
            "runtime_mode": "brain",
            "brain_bridge_active": True,
            "execution_boundary_policy": {"default_required_scopes": ["exec"]},
            "capability_layering": {"provider_selected": "echo"},
        },
    )
    profile = SimpleNamespace(
        name="mini",
        display_name="Mini",
        role=SimpleNamespace(mission="Help the operator."),
        personality=SimpleNamespace(tone="clear"),
    )
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            context=SimpleNamespace(total_max_tokens=8192),
            runtime=SimpleNamespace(sandbox_mode="workspace-write"),
        ),
        security_policy=SimpleNamespace(permission_mode="ask"),
        memory=SimpleNamespace(list_scopes=lambda: ["agent:mini"]),
        provenance_recorder=object(),
        sessions=object(),
        resolve_agent_profile=lambda *_args, **_kwargs: profile,
    )

    snapshot = self_model_query.build_runtime_self_model(runtime, agent_id="mini")

    assert snapshot.health == SELF_MODEL_HEALTH_DEGRADED
    assert snapshot.capabilities.facts["provider"] == "echo"
    assert snapshot.policy.facts["destructive_action_posture"] == "approval_required"
    assert snapshot.improvement_state.facts["promotion_posture"] == "bsil_only"
    assert (
        DEGRADED_GENERIC_CANDIDATE_REGISTRY_UNAVAILABLE
        in snapshot.improvement_state.degraded_reasons
    )


def test_api_runtime_emits_self_model_snapshot_event_best_effort() -> None:
    records: list[object] = []
    runtime = SimpleNamespace(
        telemetry_service=SimpleNamespace(record_event_sync=records.append)
    )

    APIRuntime._emit_runtime_self_model_snapshot(  # type: ignore[misc]
        runtime,
        _snapshot().model_dump(mode="json"),
    )

    assert len(records) == 1
    assert records[0].event_type == "self_model.snapshot_built"
    assert records[0].data["agent_id"] == "mini"
