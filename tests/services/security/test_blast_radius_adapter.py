from __future__ import annotations

import dataclasses

import pytest

from openminion.modules.tool.runtime.blast_radius import (
    CompositionBoundaryEvent,
    InMemoryCompositionAuditLog,
    build_composition_policy,
)
from openminion.services.security.blast_radius.adapter import (
    CompositionBoundaryAdapter,
    build_composition_boundary_adapter,
)


@dataclasses.dataclass
class _FakeSpec:
    name: str
    min_scope: str = "READ_ONLY"
    dangerous: bool = False
    blast_radius: str | None = None
    sandbox_kind: str | None = None


def test_adapter_requires_frozen_policy() -> None:
    from openminion.modules.tool.runtime.blast_radius import CompositionPolicy

    unfrozen = CompositionPolicy(
        policy_id="x",
        forbidden_transitions=frozenset(),
        escalation_required_transitions=frozenset(),
        max_radius_per_turn=None,
        frozen=False,
    )
    with pytest.raises(ValueError):
        CompositionBoundaryAdapter(
            policy=unfrozen,
            audit_log=InMemoryCompositionAuditLog(),
            seam_id="seam.x",
        )


def test_adapter_rejects_empty_seam_id() -> None:
    with pytest.raises(ValueError):
        build_composition_boundary_adapter(
            policy=build_composition_policy(policy_id="p"),
            audit_log=InMemoryCompositionAuditLog(),
            seam_id="",
        )


def test_adapter_step_returns_typed_tuple() -> None:
    adapter = build_composition_boundary_adapter(
        policy=build_composition_policy(policy_id="loop"),
        audit_log=InMemoryCompositionAuditLog(),
        seam_id="services.security.tool_execution",
    )
    profile, needs_approval, event = adapter.step(
        _FakeSpec(name="file.read", min_scope="READ_ONLY")
    )
    assert profile.tool_name == "file.read"
    assert profile.blast_radius == "read_only"
    assert needs_approval is False
    assert isinstance(event, CompositionBoundaryEvent)
    assert event.seam_id == "services.security.tool_execution"
    assert event.decision == "allowed"


def test_adapter_records_event_per_step_and_preserves_fifo() -> None:
    log = InMemoryCompositionAuditLog()
    adapter = build_composition_boundary_adapter(
        policy=build_composition_policy(policy_id="loop"),
        audit_log=log,
        seam_id="modules.brain.loop.tools.parallel",
    )
    for spec in [
        _FakeSpec(name="file.read", min_scope="READ_ONLY"),
        _FakeSpec(name="file.write", min_scope="WRITE_SAFE", dangerous=True),
        _FakeSpec(name="exec.run", min_scope="POWER_USER", dangerous=True),
    ]:
        adapter.step(spec)
    events = log.boundary_events()
    assert len(events) == 3
    assert [event.to_radius for event in events] == [
        "read_only",
        "local_mutation",
        "code_execution",
    ]
    assert all(event.seam_id == "modules.brain.loop.tools.parallel" for event in events)


def test_adapter_escalation_required_propagates() -> None:
    policy = build_composition_policy(
        policy_id="loop",
        escalation_required_transitions=[("local_mutation", "code_execution")],
    )
    adapter = build_composition_boundary_adapter(
        policy=policy,
        audit_log=InMemoryCompositionAuditLog(),
        seam_id="modules.tool.executor",
    )
    adapter.step(_FakeSpec(name="file.write", min_scope="WRITE_SAFE", dangerous=True))
    _, needs_approval, event = adapter.step(
        _FakeSpec(name="exec.run", min_scope="POWER_USER", dangerous=True)
    )
    assert needs_approval is True
    assert event.decision == "escalation_required"


def test_adapter_composed_radius_reflects_run_so_far() -> None:
    adapter = build_composition_boundary_adapter(
        policy=build_composition_policy(policy_id="loop"),
        audit_log=InMemoryCompositionAuditLog(),
        seam_id="services.runtime.engine",
    )
    assert adapter.composed_radius() == "read_only"
    adapter.step(_FakeSpec(name="file.write", min_scope="WRITE_SAFE", dangerous=True))
    assert adapter.composed_radius() == "local_mutation"
    adapter.step(
        _FakeSpec(
            name="github.commit",
            min_scope="WRITE_SAFE",
            dangerous=True,
            blast_radius="remote_mutation",
        )
    )
    assert adapter.composed_radius() == "remote_mutation"


def test_adapter_reset_clears_prior_profiles() -> None:
    adapter = build_composition_boundary_adapter(
        policy=build_composition_policy(policy_id="loop"),
        audit_log=InMemoryCompositionAuditLog(),
        seam_id="seam.a",
    )
    adapter.step(_FakeSpec(name="file.read", min_scope="READ_ONLY"))
    assert adapter.composed_radius() == "read_only"
    adapter.step(
        _FakeSpec(
            name="exec.run",
            min_scope="POWER_USER",
            dangerous=True,
        )
    )
    assert adapter.composed_radius() == "code_execution"
    adapter.reset()
    assert adapter.composed_radius() == "read_only"
