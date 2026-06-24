from __future__ import annotations

from pathlib import Path

from openminion.modules.brain.adapters.tool import ToolAdapter
from openminion.modules.tool.plugin_api import PolicyDecision


class RequireConfirmAdapter:
    def evaluate(self, *, tool_name, tool_spec, args):
        return PolicyDecision(
            allowed=False,
            reason="Approval required",
            code="CONFIRM_REQUIRED",
            requires_confirm=True,
        )


class AllowAdapter:
    def evaluate(self, *, tool_name, tool_spec, args):
        del tool_name, tool_spec, args
        return PolicyDecision(
            allowed=True,
            reason="Allowed by policy adapter.",
            code="OK",
        )


def test_os_adapter_returns_needs_user_on_confirm_required(tmp_path: Path):
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        policy_adapter=RequireConfirmAdapter(),
    )
    result = adapter.execute(
        command={"tool_name": "exec.run", "args": {"command": "ls"}},
        session_id="s1",
        trace_id="t1",
    )

    assert result["status"] == "needs_user"
    assert result["error"]["code"] == "CONFIRM_REQUIRED"
    assert "approval_id" in result["error"]["details"]


def test_os_adapter_confirmation_grant_replay_uses_policy_gate(tmp_path: Path):
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        policy_adapter=AllowAdapter(),
    )
    base_command = {
        "tool_name": "file.write",
        "args": {"path": "probe.txt", "content": "hello"},
    }

    blocked = adapter.execute(
        command=base_command,
        session_id="s1",
        trace_id="t1",
    )
    assert blocked["status"] == "needs_user"
    assert blocked["error"]["code"] == "CONFIRM_REQUIRED"

    replay = adapter.execute(
        command={
            **base_command,
            "inputs": {
                "confirmation_grant_id": "grant-test-1",
                "confirmation_source": "policy_replay",
            },
        },
        session_id="s1",
        trace_id="t2",
    )
    assert replay["status"] == "success"


def test_os_adapter_background_watch_write_authorization_confirms_once(
    tmp_path: Path,
):
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        policy_adapter=AllowAdapter(),
    )
    result = adapter.execute(
        command={
            "tool_name": "file.write",
            "args": {"path": "probe.txt", "content": "hello"},
            "inputs": {
                "background_write_authorized": True,
                "background_write_authorization_source": "watch_subscription",
            },
        },
        session_id="watch:job-1",
        trace_id="run-1",
    )

    assert result["status"] == "success"
    assert result["outputs"]["background_watch_write_authorized"] is True
    assert result["outputs"]["background_watch_write_tool"] == "file.write"
