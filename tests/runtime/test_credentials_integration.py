from __future__ import annotations

from openminion.base.config.env import (
    EnvironmentConfig,
    resolve_credential_env_value,
)
from openminion.modules.runtime.credentials import (
    CredentialAccessEvent,
    CredentialRotationEvent,
    InMemoryCredentialAuditLog,
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


# base/config/env.py seam


def test_base_env_seam_routes_five_step_flow() -> None:
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
        access_site="base.config.env.resolve_credential_env_value",
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


# tools/config.py seam


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


# tools/github/env.py seam


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


# tools/github/auth.py seam — auth-failure reload


def test_github_auth_invalid_path_emits_rotation_event() -> None:
    log = InMemoryCredentialAuditLog()
    env = _env_with_token(SECRET_VALUE)

    # Initial resolve.
    _value, ref = resolve_github_pat_through_credential_boundary(
        caller_agent_id="agent-1",
        caller_profile_id="profile-gh",
        audit_log=log,
        env=env,
    )

    # Simulate AUTH_INVALID response → typed reload path.
    new_env = _env_with_token(SECRET_VALUE + "_rotated")
    new_value, rotation_event = reload_github_pat_after_auth_invalid(
        ref,
        audit_log=log,
        env=new_env,
    )

    assert isinstance(rotation_event, CredentialRotationEvent)
    assert rotation_event.trigger == "auth_invalid"
    assert rotation_event.credential_id == "github_pat"
    # Reload never widens scope.
    assert rotation_event.scope_kind == ref.scope_kind
    assert rotation_event.scope_id == ref.scope_id
    # Reload picked up the rotated value but never stored it on the event.
    assert new_value == SECRET_VALUE + "_rotated"
    assert SECRET_VALUE not in str(rotation_event)
    assert (SECRET_VALUE + "_rotated") not in str(rotation_event)

    # Rotation event is in the log exactly once.
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
    # No reload called — rotation event MUST NOT be present.
    assert log.rotation_events() == ()


# tools/gws/plugin.py seam — canonical credential placeholder


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


# Cross-seam: read ↔ access-event parity


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

    # Three credential reads at three named seams.
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
    # All events name distinct caller-declared sites.
    sites = tuple(e.access_site for e in access_events)
    assert sites == (
        "base.config.env",
        "tools.config",
        "tools.github.env.resolve_github_pat_through_credential_boundary",
    )
    # None of the events carry the secret value.
    for event in access_events:
        assert isinstance(event, CredentialAccessEvent)
        assert SECRET_VALUE not in str(event)
