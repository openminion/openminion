from __future__ import annotations

from openminion.base.config.action_policy import ACTION_POLICY_SESSION_OVERRIDE_KEY
from openminion.services.gateway.turn.runtime import _extract_ephemeral_prompt_metadata


def test_extract_ephemeral_prompt_metadata_includes_cron_metadata() -> None:
    extracted = _extract_ephemeral_prompt_metadata(
        {
            "cron_job_id": "job-123",
            "cron_run_id": "run-456",
            "cwd": "/tmp/workspace/project",
            "scheduled_for": "2026-03-20T00:00:00Z",
            "workspace_root": "/tmp/workspace",
            "ignored_key": "ignored",
        }
    )

    assert extracted == {
        "cron_job_id": "job-123",
        "cron_run_id": "run-456",
        "cwd": "/tmp/workspace/project",
        "scheduled_for": "2026-03-20T00:00:00Z",
        "workspace_root": "/tmp/workspace",
    }


def test_extract_ephemeral_prompt_metadata_includes_watch_contract() -> None:
    extracted = _extract_ephemeral_prompt_metadata(
        {
            "watch_job": "true",
            "watch_turn_kind": "check",
            "watch_description": "release status",
            "watch_alert_condition": "status is not ready",
            "watch_profile_id": "profile-1",
            "watch_target_id": "target-1",
            "watch_allowed_tools": "file.read,time",
            "watch_max_iterations": "5",
            "watch_timeout_seconds": "60",
            "watch_write_authorized": "false",
            "watch_write_authorization_scope": "",
            "ignored": "value",
        }
    )

    assert extracted == {
        "watch_job": "true",
        "watch_turn_kind": "check",
        "watch_description": "release status",
        "watch_alert_condition": "status is not ready",
        "watch_profile_id": "profile-1",
        "watch_target_id": "target-1",
        "watch_allowed_tools": "file.read,time",
        "watch_max_iterations": "5",
        "watch_timeout_seconds": "60",
        "watch_write_authorized": "false",
    }


def test_extract_ephemeral_prompt_metadata_includes_memory_consolidation_context() -> (
    None
):
    extracted = _extract_ephemeral_prompt_metadata(
        {
            "memory_consolidation_job": "true",
            "memory_consolidation_target_scope": "global:system",
            "memory_consolidation_batch_limit": "8",
            "memory_consolidation_max_iterations": "2",
            "memory_consolidation_timeout_seconds": "30",
        }
    )

    assert extracted == {
        "memory_consolidation_job": "true",
        "memory_consolidation_target_scope": "global:system",
        "memory_consolidation_batch_limit": "8",
        "memory_consolidation_max_iterations": "2",
        "memory_consolidation_timeout_seconds": "30",
    }


def test_extract_ephemeral_prompt_metadata_keeps_turn_tool_scope() -> None:
    extracted = _extract_ephemeral_prompt_metadata(
        {
            "subagent_context_id": "subagent-1",
            "subagent_tool_allowlist": "file.read,web.search",
            "turn_tool_allowlist": "file.read",
            "turn_tool_allowlist_supplied": "true",
            "ignored_key": "ignored",
        }
    )

    assert extracted == {
        "subagent_context_id": "subagent-1",
        "subagent_tool_allowlist": "file.read,web.search",
        "turn_tool_allowlist": "file.read",
        "turn_tool_allowlist_supplied": "true",
    }


def test_extract_ephemeral_prompt_metadata_keeps_permission_posture() -> None:
    extracted = _extract_ephemeral_prompt_metadata(
        {
            "permission_mode": "bypass",
            "permission_overrides": '{"file.write": "auto"}',
            ACTION_POLICY_SESSION_OVERRIDE_KEY: "bypass",
            "private_value": "drop-me",
        }
    )

    assert extracted == {
        "permission_mode": "bypass",
        "permission_overrides": '{"file.write": "auto"}',
        ACTION_POLICY_SESSION_OVERRIDE_KEY: "bypass",
    }


def test_extract_ephemeral_prompt_metadata_keeps_project_scope() -> None:
    extracted = _extract_ephemeral_prompt_metadata(
        {
            "linked_task_id": "task-1",
            "project_tool_calls_remaining": "12",
            "project_act_profile": "research",
            "autonomy_run_id": "internal-run",
        }
    )

    assert extracted == {
        "linked_task_id": "task-1",
        "project_tool_calls_remaining": "12",
        "project_act_profile": "research",
    }


def test_extract_ephemeral_prompt_metadata_forwards_added_roots_in_memory() -> None:
    raw_roots = '["/tmp/shared"]'

    extracted = _extract_ephemeral_prompt_metadata(
        {"openminion_ephemeral_workspace_roots": raw_roots}
    )

    assert extracted == {"openminion_ephemeral_workspace_roots": raw_roots}


def test_actual_inbound_message_preserves_only_admitted_security_context():
    import json
    from types import SimpleNamespace
    from openminion.services.gateway.turn.flow_agent import (
        GatewayTurnAgentExecutionMixin,
    )
    from openminion.services.gateway.turn.lifecycle import _GatewayTurnLifecycleOps
    from openminion.tools.security.trusted_source import run_context

    scope = json.dumps(
        {
            "job_id": "job",
            "run_id": "run",
            "revision": "a" * 40,
            "admission_fingerprint": "b" * 64,
        }
    )
    gateway = GatewayTurnAgentExecutionMixin()
    gateway._lifecycle_ops = SimpleNamespace(
        optional_ids=_GatewayTurnLifecycleOps.optional_ids
    )
    routing = SimpleNamespace(
        normalized_inbound_metadata={
            "security_scan_context": scope,
            "workspace_root": "/approved/source",
            "permission_mode": "readonly",
            "unrelated_security_hint": "excluded",
        },
        session=SimpleNamespace(id="security-run"),
        normalized_request_id="request",
        routing_action="new",
        routing_reason="explicit",
        lifecycle=SimpleNamespace(thread_state="new", qualifier=""),
        conversation_id="run",
        thread_id="",
        attach_id="",
    )
    message = gateway._build_inbound_message(
        routing,
        channel="console",
        target="cron",
        body="audit",
        run_id="run",
        authenticity_decision=SimpleNamespace(
            reason_code="local", verified=True, mode="local"
        ),
        participant_id="configured-agent",
    )
    assert message.metadata["security_scan_context"] == scope
    assert message.metadata["workspace_root"] == "/approved/source"
    assert message.metadata["permission_mode"] == "readonly"
    assert "unrelated_security_hint" not in message.metadata
    tool_context = SimpleNamespace(
        policy=SimpleNamespace(raw={"context_metadata": message.metadata})
    )
    assert run_context(tool_context) == json.loads(scope)
