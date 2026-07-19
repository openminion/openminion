from __future__ import annotations

import pytest

from openminion.base.config.env import EnvironmentConfig
from openminion.modules.runtime.credentials import (
    CredentialAccessEvent,
    CredentialRotationEvent,
    InMemoryCredentialAuditLog,
    resolve_credential_env_value,
    resolve_credential_ref,
)
from openminion.tools.config import resolve_tool_credential_value
from openminion.tools.github.auth import reload_github_pat_after_auth_invalid
from openminion.tools.github.env import (
    resolve_github_pat_through_credential_boundary,
)
from openminion.tools.gws.plugin import gws_redacted_credential_placeholder


SECRET_VALUE = "ghp_DO_NOT_LEAK_abc123"


def _env_with_token(value: str) -> EnvironmentConfig:
    return EnvironmentConfig.from_sources(
        process_env={},
        runtime_env={"GITHUB_TOKEN": value},
    )


def test_runtime_credential_owner_routes_five_step_flow() -> None:
    log = InMemoryCredentialAuditLog()
    env = _env_with_token(SECRET_VALUE)
    ref = resolve_credential_ref(
        "github_pat",
        scope_kind="tool_family",
        scope_id="profile-gh",
        source_kind="env",
        env_name="GITHUB_TOKEN",
        rotation_policy="reload_on_auth_failure",
    )

    value = resolve_credential_env_value(
        ref,
        caller_agent_id="agent-1",
        caller_profile_id="profile-gh",
        access_site="modules.runtime.credentials.resolve_credential_env_value",
        audit_log=log,
        env=env,
    )

    assert value == SECRET_VALUE
    access_events = log.access_events()
    assert len(access_events) == 1
    assert access_events[0].decision == "allowed"
    assert access_events[0].credential_id == "github_pat"
    # Event never carries the secret value.
    for event in log.events:
        assert SECRET_VALUE not in str(event)


def test_credential_owner_rejects_non_env_source() -> None:
    ref = resolve_credential_ref(
        "vault_token",
        scope_kind="process",
        scope_id="proc",
        source_kind="secret_ref",
    )

    with pytest.raises(ValueError, match="env-source"):
        resolve_credential_env_value(
            ref,
            caller_agent_id="agent-1",
            caller_profile_id="profile-1",
            access_site="modules.runtime.credentials",
            audit_log=InMemoryCredentialAuditLog(),
        )


def test_tools_config_seam_routes_five_step_flow() -> None:
    log = InMemoryCredentialAuditLog()
    env = _env_with_token(SECRET_VALUE)
    ref = resolve_credential_ref(
        "github_pat",
        scope_kind="tool_family",
        scope_id="profile-gh",
        source_kind="env",
        env_name="GITHUB_TOKEN",
        rotation_policy="reload_on_auth_failure",
    )

    value = resolve_tool_credential_value(
        ref,
        caller_agent_id="agent-1",
        caller_profile_id="profile-gh",
        access_site="tools.config.resolve_tool_credential_value",
        audit_log=log,
        env=env,
    )

    assert value == SECRET_VALUE
    assert len(log.access_events()) == 1


def test_github_env_seam_resolves_through_boundary() -> None:
    log = InMemoryCredentialAuditLog()
    env = _env_with_token(SECRET_VALUE)

    value, ref = resolve_github_pat_through_credential_boundary(
        caller_agent_id="agent-1",
        caller_profile_id="profile-gh",
        audit_log=log,
        env=env,
    )

    assert value == SECRET_VALUE
    assert ref.credential_id == "github_pat"
    assert ref.source_kind == "env"
    assert ref.rotation_policy == "reload_on_auth_failure"
    assert len(log.access_events()) == 1


def test_github_auth_invalid_path_emits_rotation_event() -> None:
    log = InMemoryCredentialAuditLog()
    env = _env_with_token(SECRET_VALUE)

    _value, ref = resolve_github_pat_through_credential_boundary(
        caller_agent_id="agent-1",
        caller_profile_id="profile-gh",
        audit_log=log,
        env=env,
    )

    new_env = _env_with_token(SECRET_VALUE + "_rotated")
    new_value, rotation_event = reload_github_pat_after_auth_invalid(
        ref,
        audit_log=log,
        env=new_env,
    )

    assert isinstance(rotation_event, CredentialRotationEvent)
    assert rotation_event.trigger == "auth_invalid"
    assert rotation_event.credential_id == "github_pat"
    assert rotation_event.scope_kind == ref.scope_kind
    assert rotation_event.scope_id == ref.scope_id
    assert new_value == SECRET_VALUE + "_rotated"
    assert SECRET_VALUE not in str(rotation_event)
    assert (SECRET_VALUE + "_rotated") not in str(rotation_event)

    assert len(log.rotation_events()) == 1


def test_no_rotation_event_when_auth_invalid_path_did_not_run() -> None:
    log = InMemoryCredentialAuditLog()
    env = _env_with_token(SECRET_VALUE)
    resolve_github_pat_through_credential_boundary(
        caller_agent_id="agent-1",
        caller_profile_id="profile-gh",
        audit_log=log,
        env=env,
    )
    assert log.rotation_events() == ()


def test_gws_credential_placeholder_never_returns_raw_value_or_env_name() -> None:
    ref = resolve_credential_ref(
        "gws_oauth_refresh",
        scope_kind="tool_family",
        scope_id="profile-gws",
        source_kind="secret_ref",
        env_name="",
        rotation_policy="static",
    )
    placeholder = gws_redacted_credential_placeholder(ref)
    assert SECRET_VALUE not in placeholder
    assert "GITHUB_TOKEN" not in placeholder
    # Typed metadata IS present so downstream audit consumers can correlate.
    assert "gws_oauth_refresh" in placeholder
    assert "tool_family:profile-gws" in placeholder
    assert "source=secret_ref" in placeholder


def test_read_count_matches_access_event_count_across_seams() -> None:
    log = InMemoryCredentialAuditLog()
    env = _env_with_token(SECRET_VALUE)
    ref = resolve_credential_ref(
        "github_pat",
        scope_kind="tool_family",
        scope_id="profile-gh",
        source_kind="env",
        env_name="GITHUB_TOKEN",
        rotation_policy="reload_on_auth_failure",
    )

    resolve_credential_env_value(
        ref,
        caller_agent_id="agent-1",
        caller_profile_id="profile-gh",
        access_site="base.config.env",
        audit_log=log,
        env=env,
    )
    resolve_tool_credential_value(
        ref,
        caller_agent_id="agent-1",
        caller_profile_id="profile-gh",
        access_site="tools.config",
        audit_log=log,
        env=env,
    )
    resolve_github_pat_through_credential_boundary(
        caller_agent_id="agent-1",
        caller_profile_id="profile-gh",
        audit_log=log,
        env=env,
    )

    access_events = log.access_events()
    assert len(access_events) == 3
    sites = tuple(e.access_site for e in access_events)
    assert sites == (
        "base.config.env",
        "tools.config",
        "tools.github.env.resolve_github_pat_through_credential_boundary",
    )
    for event in access_events:
        assert isinstance(event, CredentialAccessEvent)
        assert SECRET_VALUE not in str(event)
