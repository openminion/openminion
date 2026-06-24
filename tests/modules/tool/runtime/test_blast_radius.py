from __future__ import annotations

import dataclasses
from types import MappingProxyType

import pytest

from openminion.modules.runtime.credentials import (
    CredentialRef,
    resolve_credential_ref,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.blast_radius import (
    COMPOSITION_DECISIONS,
    CompositionBoundaryEvent,
    CompositionPolicy,
    InMemoryCompositionAuditLog,
    SANDBOX_KINDS,
    TOOL_BLAST_RADIUSES,
    ToolBlastRadiusProfile,
    blast_radius_rank,
    build_composition_policy,
    classify_composed_blast_radius,
    classify_tool_blast_radius,
    generic_wrapper_family_fallback,
    min_scope_default_radius,
    record_composition_boundary_event,
    requires_composition_approval,
)


@dataclasses.dataclass
class _FakeSpec:
    name: str
    min_scope: str = "READ_ONLY"
    dangerous: bool = False
    blast_radius: str | None = None
    sandbox_kind: str | None = None


def _profile(
    name: str,
    radius: str,
    *,
    sandbox: str = "runtime_local",
    dangerous: bool = False,
    credential_ref: CredentialRef | None = None,
) -> ToolBlastRadiusProfile:
    return ToolBlastRadiusProfile(
        tool_name=name,
        blast_radius=radius,  # type: ignore[arg-type]
        sandbox_kind=sandbox,  # type: ignore[arg-type]
        credential_ref=credential_ref,
        dangerous=dangerous,
        notes="",
    )


def test_tool_blast_radius_closed_set_exhaustive() -> None:
    assert TOOL_BLAST_RADIUSES == (
        "read_only",
        "local_mutation",
        "remote_mutation",
        "code_execution",
        "process_spawn",
        "network_unbounded",
    )


def test_sandbox_kind_closed_set_exhaustive() -> None:
    assert SANDBOX_KINDS == (
        "none",
        "runtime_local",
        "runtime_bwrap",
        "browser_managed",
        "remote_wrapper",
    )


def test_composition_decision_closed_set_exhaustive() -> None:
    assert COMPOSITION_DECISIONS == ("allowed", "escalation_required", "forbidden")


def test_blast_radius_rank_rejects_unknown() -> None:
    with pytest.raises(ToolRuntimeError):
        blast_radius_rank("unknown")  # type: ignore[arg-type]


def test_blast_radius_rank_strictly_ordered() -> None:
    ranks = [blast_radius_rank(r) for r in TOOL_BLAST_RADIUSES]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)


def test_classify_read_only_tool_default_radius() -> None:
    spec = _FakeSpec(name="file.read", min_scope="READ_ONLY", dangerous=False)
    profile = classify_tool_blast_radius(spec)
    assert profile.tool_name == "file.read"
    assert profile.blast_radius == "read_only"
    assert profile.sandbox_kind == "runtime_local"
    assert profile.dangerous is False
    assert profile.credential_ref is None


def test_classify_dangerous_write_safe_lifts_to_local_mutation() -> None:
    spec = _FakeSpec(name="file.write", min_scope="WRITE_SAFE", dangerous=True)
    profile = classify_tool_blast_radius(spec)
    assert profile.blast_radius == "local_mutation"
    assert profile.dangerous is True


def test_classify_power_user_defaults_to_code_execution() -> None:
    spec = _FakeSpec(name="exec.run", min_scope="POWER_USER", dangerous=True)
    profile = classify_tool_blast_radius(spec)
    assert profile.blast_radius == "code_execution"


def test_classify_explicit_declaration_wins() -> None:
    spec = _FakeSpec(
        name="my.tool",
        min_scope="READ_ONLY",
        dangerous=False,
        blast_radius="process_spawn",
        sandbox_kind="runtime_bwrap",
    )
    profile = classify_tool_blast_radius(spec)
    assert profile.blast_radius == "process_spawn"
    assert profile.sandbox_kind == "runtime_bwrap"


def test_classify_unknown_explicit_radius_rejected() -> None:
    spec = _FakeSpec(name="bad.tool", blast_radius="kaboom")
    with pytest.raises(ToolRuntimeError):
        classify_tool_blast_radius(spec)


def test_classify_unknown_explicit_sandbox_rejected() -> None:
    spec = _FakeSpec(name="bad.tool", sandbox_kind="lava_lamp")
    with pytest.raises(ToolRuntimeError):
        classify_tool_blast_radius(spec)


def test_classify_determinism_same_input_same_output() -> None:
    spec = _FakeSpec(name="file.read")
    p1 = classify_tool_blast_radius(spec)
    p2 = classify_tool_blast_radius(spec)
    assert p1 == p2


def test_classify_rejects_missing_tool_name() -> None:
    spec = _FakeSpec(name="")
    with pytest.raises(ToolRuntimeError):
        classify_tool_blast_radius(spec)


def test_compose_empty_is_read_only() -> None:
    assert classify_composed_blast_radius(()) == "read_only"


def test_compose_all_read_only_stays_read_only() -> None:
    profiles = [_profile(f"r{i}", "read_only") for i in range(3)]
    assert classify_composed_blast_radius(profiles) == "read_only"


def test_compose_one_remote_mutation_lifts_whole_sequence() -> None:
    profiles = [
        _profile("file.list_dir", "read_only"),
        _profile("file.read", "read_only"),
        _profile("github.commit_files", "remote_mutation"),
    ]
    assert classify_composed_blast_radius(profiles) == "remote_mutation"


def test_compose_takes_strict_max_over_ordering() -> None:
    profiles = [
        _profile("a", "local_mutation"),
        _profile("b", "remote_mutation"),
        _profile("c", "code_execution"),
        _profile("d", "read_only"),
    ]
    assert classify_composed_blast_radius(profiles) == "code_execution"


def test_compose_network_unbounded_is_top() -> None:
    profiles = [
        _profile("a", "code_execution"),
        _profile("b", "network_unbounded"),
    ]
    assert classify_composed_blast_radius(profiles) == "network_unbounded"


def test_credential_with_process_scope_does_not_escalate() -> None:
    spec = _FakeSpec(name="file.read", min_scope="READ_ONLY")
    cred = resolve_credential_ref(
        "API_KEY", scope_kind="process", scope_id="p1", env_name="API_KEY"
    )
    profile = classify_tool_blast_radius(spec, credential_ref=cred)
    assert profile.blast_radius == "read_only"
    assert profile.credential_ref is cred


def test_credential_with_profile_scope_escalates_to_remote_mutation() -> None:
    spec = _FakeSpec(name="net.fetch", min_scope="READ_ONLY")
    cred = resolve_credential_ref(
        "PROFILE_TOKEN",
        scope_kind="profile",
        scope_id="profile-a",
        env_name="PROFILE_TOKEN",
    )
    profile = classify_tool_blast_radius(spec, credential_ref=cred)
    assert profile.blast_radius == "remote_mutation"


def test_credential_with_agent_scope_escalates() -> None:
    spec = _FakeSpec(name="some.tool", min_scope="WRITE_SAFE", dangerous=False)
    cred = resolve_credential_ref(
        "AGENT_TOKEN",
        scope_kind="agent",
        scope_id="agent-a",
        env_name="AGENT_TOKEN",
    )
    profile = classify_tool_blast_radius(spec, credential_ref=cred)
    assert profile.blast_radius == "remote_mutation"


def test_credential_does_not_demote_higher_radius() -> None:
    spec = _FakeSpec(name="exec.run", min_scope="POWER_USER", dangerous=True)
    cred = resolve_credential_ref(
        "TOKEN", scope_kind="profile", scope_id="p1", env_name="TOKEN"
    )
    profile = classify_tool_blast_radius(spec, credential_ref=cred)
    assert profile.blast_radius == "code_execution"


def test_build_composition_policy_is_frozen() -> None:
    policy = build_composition_policy(policy_id="test")
    assert policy.frozen is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.policy_id = "new"  # type: ignore[misc]


def test_build_composition_policy_rejects_unknown_radius() -> None:
    with pytest.raises(ToolRuntimeError):
        build_composition_policy(
            policy_id="bad",
            forbidden_transitions=[("read_only", "bogus")],  # type: ignore[list-item]
        )


def test_build_composition_policy_rejects_empty_id() -> None:
    with pytest.raises(ToolRuntimeError):
        build_composition_policy(policy_id="")


def test_composition_policy_with_frozen_false_rejected_by_guard() -> None:
    policy = CompositionPolicy(
        policy_id="test",
        forbidden_transitions=frozenset(),
        escalation_required_transitions=frozenset(),
        max_radius_per_turn=None,
        frozen=False,
    )
    next_p = _profile("a", "read_only")
    with pytest.raises(ToolRuntimeError):
        requires_composition_approval(policy, (), next_p)


def test_requires_composition_approval_allows_baseline() -> None:
    policy = build_composition_policy(policy_id="loop")
    prior = [_profile("file.read", "read_only")]
    next_p = _profile("file.list_dir", "read_only")
    assert requires_composition_approval(policy, prior, next_p) is False


def test_requires_composition_approval_escalation_required() -> None:
    policy = build_composition_policy(
        policy_id="loop",
        escalation_required_transitions=[("local_mutation", "code_execution")],
    )
    prior = [_profile("file.write", "local_mutation")]
    next_p = _profile("exec.run", "code_execution")
    assert requires_composition_approval(policy, prior, next_p) is True


def test_requires_composition_approval_forbidden() -> None:
    policy = build_composition_policy(
        policy_id="loop",
        forbidden_transitions=[("read_only", "network_unbounded")],
    )
    prior = [_profile("file.read", "read_only")]
    next_p = _profile("net.unbounded", "network_unbounded")
    assert requires_composition_approval(policy, prior, next_p) is True


def test_requires_composition_approval_max_radius_cap() -> None:
    policy = build_composition_policy(
        policy_id="loop",
        max_radius_per_turn="local_mutation",
    )
    prior = [_profile("file.write", "local_mutation")]
    next_p = _profile("exec.run", "code_execution")
    assert requires_composition_approval(policy, prior, next_p) is True


def test_record_composition_boundary_event_shape() -> None:
    policy = build_composition_policy(policy_id="loop")
    log = InMemoryCompositionAuditLog()
    prior = [_profile("file.read", "read_only")]
    next_p = _profile("github.commit_files", "remote_mutation")
    event = record_composition_boundary_event(
        policy,
        prior,
        next_p,
        seam_id="loop.tools.parallel",
        audit_log=log,
    )
    assert isinstance(event, CompositionBoundaryEvent)
    assert event.policy_id == "loop"
    assert event.from_radius == "read_only"
    assert event.to_radius == "remote_mutation"
    assert event.tool_names == ("file.read", "github.commit_files")
    assert event.seam_id == "loop.tools.parallel"
    assert event.decision == "allowed"
    assert event.event_id  # uuid emitted
    assert event.recorded_at  # iso timestamp emitted


def test_record_composition_boundary_event_rejects_empty_seam_id() -> None:
    policy = build_composition_policy(policy_id="loop")
    log = InMemoryCompositionAuditLog()
    next_p = _profile("a", "read_only")
    with pytest.raises(ToolRuntimeError):
        record_composition_boundary_event(policy, (), next_p, seam_id="", audit_log=log)


def test_record_event_emitted_for_allowed_transition_too() -> None:
    policy = build_composition_policy(policy_id="loop")
    log = InMemoryCompositionAuditLog()
    prior = [_profile("file.read", "read_only")]
    next_p = _profile("file.list_dir", "read_only")
    record_composition_boundary_event(
        policy, prior, next_p, seam_id="seam.a", audit_log=log
    )
    events = log.boundary_events()
    assert len(events) == 1
    assert events[0].decision == "allowed"


def test_generic_wrapper_family_fallback_is_frozen_mapping() -> None:
    table = generic_wrapper_family_fallback()
    assert isinstance(table, MappingProxyType)
    with pytest.raises(TypeError):
        table["new.tool"] = ("read_only", "none")  # type: ignore[index]


def test_gws_call_resolves_through_family_fallback() -> None:
    spec = _FakeSpec(name="gws.call", min_scope="READ_ONLY", dangerous=False)
    profile = classify_tool_blast_radius(spec)
    assert profile.blast_radius == "remote_mutation"
    assert profile.sandbox_kind == "remote_wrapper"


def test_mcp_prefix_resolves_through_family_fallback() -> None:
    spec = _FakeSpec(name="mcp:tavily.search", min_scope="WRITE_SAFE", dangerous=False)
    profile = classify_tool_blast_radius(spec)
    assert profile.blast_radius == "remote_mutation"
    assert profile.sandbox_kind == "remote_wrapper"


def test_min_scope_default_radius_is_frozen_mapping() -> None:
    table = min_scope_default_radius()
    assert isinstance(table, MappingProxyType)
    with pytest.raises(TypeError):
        table["X"] = "read_only"  # type: ignore[index]


def test_audit_log_preserves_fifo_ordering() -> None:
    policy = build_composition_policy(policy_id="loop")
    log = InMemoryCompositionAuditLog()
    profiles = [
        _profile("file.read", "read_only"),
        _profile("file.write", "local_mutation"),
        _profile("exec.run", "code_execution"),
        _profile("net.fetch", "network_unbounded"),
    ]
    prior: list[ToolBlastRadiusProfile] = []
    seam_ids = ["seam.a", "seam.b", "seam.c", "seam.d"]
    for profile, seam in zip(profiles, seam_ids):
        record_composition_boundary_event(
            policy, prior, profile, seam_id=seam, audit_log=log
        )
        prior.append(profile)
    events = log.boundary_events()
    assert [event.seam_id for event in events] == seam_ids
    assert [event.to_radius for event in events] == [
        "read_only",
        "local_mutation",
        "code_execution",
        "network_unbounded",
    ]


def test_composition_boundary_event_carries_no_argument_payload() -> None:
    event = CompositionBoundaryEvent(
        event_id="e1",
        policy_id="p",
        from_radius="read_only",
        to_radius="remote_mutation",
        tool_names=("a", "b"),
        seam_id="seam.x",
        decision="allowed",
        recorded_at="2026-05-13T00:00:00",
    )
    fields = {f.name for f in dataclasses.fields(event)}
    forbidden = {"args", "arguments", "raw_args", "content", "prose", "scan"}
    assert forbidden.isdisjoint(fields)


def test_tool_blast_radius_profile_carries_no_value_field() -> None:
    profile = _profile("a", "read_only")
    fields = {f.name for f in dataclasses.fields(profile)}
    forbidden = {"value", "secret", "args", "arguments", "raw_args"}
    assert forbidden.isdisjoint(fields)


def test_composition_policy_is_frozen_dataclass() -> None:
    policy = build_composition_policy(policy_id="loop")
    assert dataclasses.is_dataclass(policy)
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.frozen = False  # type: ignore[misc]


def test_compose_then_guard_then_record_seam_chains() -> None:
    policy = build_composition_policy(
        policy_id="loop",
        escalation_required_transitions=[("local_mutation", "code_execution")],
    )
    log = InMemoryCompositionAuditLog()

    file_write = classify_tool_blast_radius(
        _FakeSpec(name="file.write", min_scope="WRITE_SAFE", dangerous=True)
    )
    exec_run = classify_tool_blast_radius(
        _FakeSpec(name="exec.run", min_scope="POWER_USER", dangerous=True)
    )

    composed_before = classify_composed_blast_radius([file_write])
    assert composed_before == "local_mutation"

    needs_approval = requires_composition_approval(policy, [file_write], exec_run)
    assert needs_approval is True

    event = record_composition_boundary_event(
        policy,
        [file_write],
        exec_run,
        seam_id="modules.tool.executor",
        audit_log=log,
    )
    assert event.decision == "escalation_required"
    assert event.from_radius == "local_mutation"
    assert event.to_radius == "code_execution"
    assert event.seam_id == "modules.tool.executor"
