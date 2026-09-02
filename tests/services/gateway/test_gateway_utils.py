from __future__ import annotations

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
