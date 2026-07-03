from __future__ import annotations

import dataclasses
from types import MappingProxyType

import pytest

from openminion.modules.runtime.credentials import (
    CREDENTIAL_ROTATION_POLICIES,
    CREDENTIAL_SCOPE_KINDS,
    CREDENTIAL_SOURCE_KINDS,
    CredentialAccessEvent,
    CredentialRef,
    CredentialRotationEvent,
    CredentialScopeViolation,
    InMemoryCredentialAuditLog,
    assert_credential_scope,
    credential_source_routing,
    record_credential_access_event,
    redacted_credential_ref,
    reload_credential_after_auth_failure,
    resolve_credential_ref,
)


SECRET_VALUE = "ghp_DO_NOT_LEAK_THIS_VALUE_abc123"


def test_scope_kinds_are_closed_set() -> None:
    assert CREDENTIAL_SCOPE_KINDS == (
        "process",
        "profile",
        "agent",
        "tool_family",
    )


def test_source_kinds_are_closed_set() -> None:
    assert CREDENTIAL_SOURCE_KINDS == (
        "env",
        "secret_ref",
        "profile_override",
    )


def test_rotation_policies_are_closed_set() -> None:
    assert CREDENTIAL_ROTATION_POLICIES == (
        "static",
        "reload_on_auth_failure",
    )


def test_resolve_credential_ref_rejects_unknown_source_kind() -> None:
    with pytest.raises(ValueError, match="source_kind"):
        resolve_credential_ref(
            "github_pat",
            scope_kind="agent",
            scope_id="agent-1",
            source_kind="vault",  # type: ignore[arg-type]
            env_name="GITHUB_TOKEN",
        )


def test_resolve_credential_ref_rejects_unknown_scope_kind() -> None:
    with pytest.raises(ValueError, match="scope_kind"):
        resolve_credential_ref(
            "github_pat",
            scope_kind="universe",  # type: ignore[arg-type]
            scope_id="x",
            source_kind="env",
            env_name="GITHUB_TOKEN",
        )


def test_resolve_credential_ref_rejects_unknown_rotation_policy() -> None:
    with pytest.raises(ValueError, match="rotation_policy"):
        resolve_credential_ref(
            "github_pat",
            scope_kind="agent",
            scope_id="agent-1",
            source_kind="env",
            env_name="GITHUB_TOKEN",
            rotation_policy="weekly",  # type: ignore[arg-type]
        )


def test_resolve_credential_ref_is_deterministic() -> None:
    ref_a = resolve_credential_ref(
        "github_pat",
        scope_kind="agent",
        scope_id="agent-1",
        source_kind="env",
        env_name="GITHUB_TOKEN",
        rotation_policy="reload_on_auth_failure",
    )
    ref_b = resolve_credential_ref(
        "github_pat",
        scope_kind="agent",
        scope_id="agent-1",
        source_kind="env",
        env_name="GITHUB_TOKEN",
        rotation_policy="reload_on_auth_failure",
    )
    assert ref_a == ref_b


def test_credential_source_routing_is_frozen_dict() -> None:
    routing = credential_source_routing()
    assert isinstance(routing, MappingProxyType)
    assert set(routing.keys()) == set(CREDENTIAL_SOURCE_KINDS)
    with pytest.raises(TypeError):
        routing["new_source"] = "openminion.x"  # type: ignore[index]


def test_credential_ref_is_frozen_dataclass() -> None:
    ref = resolve_credential_ref(
        "github_pat",
        scope_kind="agent",
        scope_id="agent-1",
        source_kind="env",
        env_name="GITHUB_TOKEN",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.env_name = "MUTATED"  # type: ignore[misc]


def test_scope_process_permits_any_caller() -> None:
    ref = resolve_credential_ref(
        "ci_token", scope_kind="process", scope_id="proc-1", source_kind="env"
    )
    assert_credential_scope(ref, caller_agent_id="a1", caller_profile_id="p1")
    assert_credential_scope(ref, caller_agent_id="a2", caller_profile_id="p2")


def test_scope_agent_blocks_wrong_caller() -> None:
    ref = resolve_credential_ref(
        "agent_token",
        scope_kind="agent",
        scope_id="agent-1",
        source_kind="env",
    )
    assert_credential_scope(ref, caller_agent_id="agent-1", caller_profile_id="p1")
    with pytest.raises(CredentialScopeViolation) as exc:
        assert_credential_scope(ref, caller_agent_id="agent-2", caller_profile_id="p1")
    assert exc.value.credential_id == "agent_token"
    assert exc.value.scope_kind == "agent"
    assert SECRET_VALUE not in str(exc.value)


def test_scope_profile_blocks_wrong_caller() -> None:
    ref = resolve_credential_ref(
        "profile_token",
        scope_kind="profile",
        scope_id="profile-A",
        source_kind="env",
    )
    assert_credential_scope(ref, caller_agent_id="a1", caller_profile_id="profile-A")
    with pytest.raises(CredentialScopeViolation):
        assert_credential_scope(
            ref, caller_agent_id="a1", caller_profile_id="profile-B"
        )


def test_scope_tool_family_uses_profile_id() -> None:
    ref = resolve_credential_ref(
        "gh_family_token",
        scope_kind="tool_family",
        scope_id="profile-gh",
        source_kind="env",
    )
    assert_credential_scope(ref, caller_agent_id="a1", caller_profile_id="profile-gh")
    with pytest.raises(CredentialScopeViolation):
        assert_credential_scope(
            ref, caller_agent_id="a1", caller_profile_id="profile-other"
        )


def test_scope_violation_does_not_log(monkeypatch: pytest.MonkeyPatch) -> None:
    log = InMemoryCredentialAuditLog()
    ref = resolve_credential_ref(
        "ag_token", scope_kind="agent", scope_id="agent-1", source_kind="env"
    )
    with pytest.raises(CredentialScopeViolation):
        assert_credential_scope(ref, caller_agent_id="agent-2", caller_profile_id="p1")
    assert log.events == []


def test_redacted_credential_ref_never_returns_raw_value() -> None:
    ref = resolve_credential_ref(
        "github_pat",
        scope_kind="agent",
        scope_id="agent-1",
        source_kind="env",
        env_name="GITHUB_TOKEN",
        rotation_policy="reload_on_auth_failure",
    )
    rendered = redacted_credential_ref(ref)
    assert SECRET_VALUE not in rendered


def test_redacted_credential_ref_never_returns_raw_env_name() -> None:
    ref = resolve_credential_ref(
        "github_pat",
        scope_kind="agent",
        scope_id="agent-1",
        source_kind="env",
        env_name="GITHUB_TOKEN",
    )
    rendered = redacted_credential_ref(ref)
    assert "GITHUB_TOKEN" not in rendered
    assert "github_pat" in rendered
    assert "agent:agent-1" in rendered
    assert "source=env" in rendered


def test_redaction_does_not_inspect_value_bytes() -> None:
    # The redactor's signature accepts only the typed ref. There is no path
    # for the value to enter the function.
    ref = resolve_credential_ref(
        "k", scope_kind="process", scope_id="proc", source_kind="env"
    )
    assert "value" not in redacted_credential_ref(ref).lower()


def test_record_credential_access_event_emits_typed_event() -> None:
    log = InMemoryCredentialAuditLog()
    ref = resolve_credential_ref(
        "k", scope_kind="process", scope_id="proc", source_kind="env"
    )
    event = record_credential_access_event(
        ref,
        access_site="tools.github.auth.require_github_pat",
        caller_agent_id="a1",
        caller_profile_id="p1",
        decision="allowed",
        audit_log=log,
    )
    assert isinstance(event, CredentialAccessEvent)
    assert event.credential_id == "k"
    assert event.decision == "allowed"
    assert event.access_site == "tools.github.auth.require_github_pat"
    field_names = {f.name for f in dataclasses.fields(event)}
    assert "value" not in field_names
    assert "secret" not in field_names
    assert "token" not in field_names


def test_record_credential_access_event_requires_caller_declared_site() -> None:
    log = InMemoryCredentialAuditLog()
    ref = resolve_credential_ref(
        "k", scope_kind="process", scope_id="proc", source_kind="env"
    )
    with pytest.raises(ValueError, match="access_site"):
        record_credential_access_event(
            ref,
            access_site="",
            caller_agent_id="a1",
            caller_profile_id="p1",
            decision="allowed",
            audit_log=log,
        )


def test_record_credential_access_event_rejects_unknown_decision() -> None:
    log = InMemoryCredentialAuditLog()
    ref = resolve_credential_ref(
        "k", scope_kind="process", scope_id="proc", source_kind="env"
    )
    with pytest.raises(ValueError, match="decision"):
        record_credential_access_event(
            ref,
            access_site="x.y.z",
            caller_agent_id="a1",
            caller_profile_id="p1",
            decision="maybe",  # type: ignore[arg-type]
            audit_log=log,
        )


def test_reload_after_auth_failure_emits_typed_rotation_event() -> None:
    log = InMemoryCredentialAuditLog()
    ref = resolve_credential_ref(
        "github_pat",
        scope_kind="agent",
        scope_id="agent-1",
        source_kind="env",
        env_name="GITHUB_TOKEN",
        rotation_policy="reload_on_auth_failure",
    )
    event = reload_credential_after_auth_failure(ref, audit_log=log)
    assert isinstance(event, CredentialRotationEvent)
    assert event.trigger == "auth_invalid"
    assert event.credential_id == "github_pat"
    assert event.scope_kind == ref.scope_kind
    assert event.scope_id == ref.scope_id
    field_names = {f.name for f in dataclasses.fields(event)}
    assert "value" not in field_names
    assert "secret" not in field_names
    assert "token" not in field_names
    assert "old_value" not in field_names
    assert "new_value" not in field_names


def test_reload_after_auth_failure_refuses_static_credential() -> None:
    log = InMemoryCredentialAuditLog()
    ref = resolve_credential_ref(
        "static_key",
        scope_kind="process",
        scope_id="proc",
        source_kind="env",
        env_name="API_KEY",
        rotation_policy="static",
    )
    with pytest.raises(ValueError, match="reload_on_auth_failure"):
        reload_credential_after_auth_failure(ref, audit_log=log)
    assert log.rotation_events() == ()


def test_access_event_count_matches_read_count() -> None:
    log = InMemoryCredentialAuditLog()
    ref = resolve_credential_ref(
        "k", scope_kind="process", scope_id="proc", source_kind="env"
    )

    seams = (
        "base.config.env.EnvironmentConfig.get",
        "tools.config.get_tool_env",
        "tools.github.env.get_github_token",
    )
    for site in seams:
        record_credential_access_event(
            ref,
            access_site=site,
            caller_agent_id="a1",
            caller_profile_id="p1",
            decision="allowed",
            audit_log=log,
        )

    access_events = log.access_events()
    assert len(access_events) == len(seams)
    assert tuple(e.access_site for e in access_events) == seams


def test_audit_log_preserves_fifo_ordering() -> None:
    log = InMemoryCredentialAuditLog()
    ref = resolve_credential_ref(
        "k",
        scope_kind="process",
        scope_id="proc",
        source_kind="env",
        env_name="API_KEY",
        rotation_policy="reload_on_auth_failure",
    )

    record_credential_access_event(
        ref,
        access_site="s1",
        caller_agent_id="a1",
        caller_profile_id="p1",
        decision="allowed",
        audit_log=log,
    )
    reload_credential_after_auth_failure(ref, audit_log=log)
    record_credential_access_event(
        ref,
        access_site="s2",
        caller_agent_id="a1",
        caller_profile_id="p1",
        decision="allowed",
        audit_log=log,
    )

    types = [type(e).__name__ for e in log.events]
    assert types == [
        "CredentialAccessEvent",
        "CredentialRotationEvent",
        "CredentialAccessEvent",
    ]


def test_credential_ref_has_no_prose_field() -> None:
    field_names = {f.name for f in dataclasses.fields(CredentialRef)}
    # Anti-LLM: no fields that imply prose-derived sensitivity verdicts or
    # value inspection.
    forbidden = {
        "sensitivity",
        "looks_secret",
        "value",
        "secret",
        "token",
        "prose",
        "description",
        "rationale",
    }
    assert field_names & forbidden == set()
    assert {
        "credential_id",
        "scope_kind",
        "scope_id",
        "source_kind",
        "env_name",
        "rotation_policy",
    } <= field_names


def test_access_event_has_no_prose_field() -> None:
    field_names = {f.name for f in dataclasses.fields(CredentialAccessEvent)}
    forbidden = {"value", "secret", "token", "rationale", "verdict"}
    assert field_names & forbidden == set()
    assert {
        "event_id",
        "credential_id",
        "scope_kind",
        "scope_id",
        "access_site",
        "caller_agent_id",
        "caller_profile_id",
        "decision",
        "recorded_at",
    } <= field_names


def test_rotation_event_has_no_prose_field() -> None:
    field_names = {f.name for f in dataclasses.fields(CredentialRotationEvent)}
    forbidden = {
        "value",
        "old_value",
        "new_value",
        "secret",
        "token",
        "rationale",
        "verdict",
    }
    assert field_names & forbidden == set()
    assert {
        "event_id",
        "credential_id",
        "scope_kind",
        "scope_id",
        "trigger",
        "recorded_at",
    } <= field_names
