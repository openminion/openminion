from __future__ import annotations

from pathlib import Path

from openminion.modules.brain.loop.tools.confirmation import (
    confirmation_required_user_message,
)
from openminion.modules.brain.schemas import ToolCommand
from openminion.modules.brain.adapters.tool import ToolAdapter
from openminion.modules.tool import ToolRegistry, ToolSpec
from openminion.modules.tool.base import Tool, ToolExecutionResult
from openminion.modules.tool.errors import ToolRuntimeError
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


class DenyAdapter:
    def evaluate(self, *, tool_name, tool_spec, args):
        del tool_name, tool_spec, args
        return PolicyDecision(
            allowed=False,
            reason="Denied by the configured policy.",
            code="POLICY_DENIED",
        )


def test_ops_pending_confirmation_redacts_structured_preview_secrets() -> None:
    command = ToolCommand(
        command_id="ops-1",
        title="Run remote command",
        tool_name="ops.command.run",
        args={"command": "curl -H 'Authorization: Bearer abcdefghijklmnop' /private"},
    )

    rendered = confirmation_required_user_message(
        command,
        {
            "target_id": "staging",
            "argv": [
                "curl",
                "-H",
                "Authorization: Bearer abcdefghijklmnop",
                "/private",
            ],
        },
    )

    assert "abcdefghijklmnop" not in rendered
    assert rendered.count("Bearer [REDACTED]") == 2


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


def test_os_adapter_executes_after_inline_approval(tmp_path: Path):
    adapter = ToolAdapter(workspace_root=tmp_path)
    approvals: list[tuple[str, dict[str, object], str]] = []

    def approve(tool_name, args, approval_id, _policy_facts):
        approvals.append((tool_name, args, approval_id))
        return True

    adapter.set_approval_callback(approve)
    result = adapter.execute(
        command={
            "tool_name": "file.write",
            "args": {"path": "probe.txt", "content": "hello"},
        },
        session_id="s1",
        trace_id="t1",
    )

    assert result["status"] == "success"
    assert (tmp_path / "probe.txt").read_text(encoding="utf-8") == "hello"
    assert approvals[0][0] == "file.write"
    assert approvals[0][1]["path"] == "probe.txt"
    assert approvals[0][2]


def test_default_permission_mode_asks_for_each_write(tmp_path: Path):
    adapter = ToolAdapter(workspace_root=tmp_path)
    approvals: list[str] = []

    def approve(tool_name, _args, _approval_id, _policy_facts):
        approvals.append(tool_name)
        return True

    adapter.set_approval_callback(approve)

    for filename in ("first.txt", "second.txt"):
        result = adapter.execute(
            command={
                "tool_name": "file.write",
                "args": {"path": filename, "content": "hello"},
            },
            session_id="s1",
            trace_id=filename,
        )
        assert result["status"] == "success"

    assert approvals == ["file.write", "file.write"]


def test_os_adapter_fails_closed_after_inline_denial(tmp_path: Path):
    adapter = ToolAdapter(workspace_root=tmp_path)
    adapter.set_approval_callback(lambda *_args: False)

    result = adapter.execute(
        command={
            "tool_name": "file.write",
            "args": {"path": "probe.txt", "content": "hello"},
        },
        session_id="s1",
        trace_id="t1",
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "POLICY_DENIED"
    assert not (tmp_path / "probe.txt").exists()


def test_exec_run_replays_after_inline_approval(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("OPENMINION_TOOL_EXEC_ENABLE_HOST_EXEC", "1")
    adapter = ToolAdapter(workspace_root=tmp_path)
    approvals: list[str] = []

    def approve(tool_name, _args, _approval_id, _policy_facts):
        approvals.append(tool_name)
        return True

    adapter.set_approval_callback(approve)
    result = adapter.execute(
        command={
            "tool_name": "exec.run",
            "args": {"command": "python3.11 -c \"print('approved')\""},
        },
        session_id="s1",
        trace_id="t1",
    )

    assert result["status"] == "success"
    assert "approved" in result["outputs"]["stdout"]
    assert approvals == ["exec.run"]


def test_exec_run_replay_survives_workspace_auto_posture(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("OPENMINION_TOOL_EXEC_ENABLE_HOST_EXEC", "1")
    result = ToolAdapter(workspace_root=tmp_path).execute(
        command={
            "tool_name": "exec.run",
            "args": {"command": "pwd"},
            "inputs": {
                "permission_mode": "auto",
                "permission_mode_origin": "global",
                "confirmation_grant_id": "grant-test-1",
                "confirmation_source": "policy_replay",
            },
        },
        session_id="s1",
        trace_id="t1",
    )

    assert result["status"] == "success"
    assert result["outputs"]["exit_code"] == 0


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


def test_confirmation_replay_keeps_policy_and_workspace_denials(tmp_path: Path):
    replay_inputs = {
        "confirmation_grant_id": "grant-test-1",
        "confirmation_source": "policy_replay",
    }
    denied = ToolAdapter(
        workspace_root=tmp_path,
        policy_adapter=DenyAdapter(),
    ).execute(
        command={
            "tool_name": "file.write",
            "args": {"path": "denied.txt", "content": "no"},
            "inputs": replay_inputs,
        },
        session_id="s1",
        trace_id="t1",
    )
    outside = ToolAdapter(
        workspace_root=tmp_path,
        policy_adapter=AllowAdapter(),
    ).execute(
        command={
            "tool_name": "file.write",
            "args": {"path": "../oppc-replay-outside.txt", "content": "no"},
            "inputs": replay_inputs,
        },
        session_id="s1",
        trace_id="t2",
    )

    assert denied["status"] == "error"
    assert outside["status"] == "error"
    assert not (tmp_path / "denied.txt").exists()
    assert not (tmp_path.parent / "oppc-replay-outside.txt").exists()


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


def test_policy_decision_inline_approval_carries_typed_facts(tmp_path: Path) -> None:
    facts: list[dict[str, object]] = []
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        policy_adapter=RequireConfirmAdapter(),
    )
    adapter.set_approval_callback(
        lambda _tool, _args, _approval_id, policy_facts: (
            facts.append(policy_facts) or False
        )
    )

    adapter.execute(
        command={
            "tool_name": "file.write",
            "args": {"path": "probe.txt", "content": "hello"},
        },
        session_id="s1",
        trace_id="t1",
    )

    assert facts == [
        {
            "canonical_tool": "file.write",
            "reason_code": "CONFIRM_REQUIRED",
            "duration_options": ["allow_once", "allow_session", "deny"],
        }
    ]


def test_tool_runtime_error_inline_approval_carries_typed_facts(tmp_path: Path) -> None:
    registry = ToolRegistry()

    def _handler(_args, _ctx):
        raise ToolRuntimeError(
            "CONFIRM_REQUIRED",
            "approval required",
            {
                "choices": ["allow_once", "deny"],
                "risk": {
                    "risk_class": "write",
                    "side_effects": "local",
                    "reversibility": "reversible",
                },
            },
        )

    registry.register(
        ToolSpec(
            name="file.change",
            args_model=dict,
            min_scope="WRITE_SAFE",
            handler=_handler,
        )
    )
    facts: list[dict[str, object]] = []
    adapter = ToolAdapter(workspace_root=tmp_path, runtime_registry=registry)
    adapter.set_approval_callback(
        lambda _tool, _args, _approval_id, policy_facts: (
            facts.append(policy_facts) or False
        )
    )

    adapter.execute(
        command={
            "tool_name": "file.change",
            "args": {},
            "inputs": {"permission_mode": "bypass"},
        },
        session_id="s1",
        trace_id="t1",
    )

    assert facts[0]["canonical_tool"] == "file.change"
    assert facts[0]["reason_code"] == "CONFIRM_REQUIRED"
    assert facts[0]["risk"] == {
        "risk_class": "write",
        "side_effects": "local",
        "reversibility": "reversible",
    }


def test_tool_result_inline_approval_carries_typed_facts(tmp_path: Path) -> None:
    class _ConfirmingTool(Tool):
        name = "browser"
        description = "typed result confirmation"

        def execute(self, arguments, context):
            del arguments, context
            return ToolExecutionResult(
                tool_name=self.name,
                ok=False,
                content="",
                error="approval required",
                data={
                    "error_code": "CONFIRM_REQUIRED",
                    "details": {
                        "choices": ["allow_once", "deny"],
                        "risk": {
                            "risk_class": "state_change",
                            "side_effects": "remote",
                            "reversibility": "unknown",
                        },
                    },
                },
            )

    class _RuntimeRegistry:
        def __init__(self) -> None:
            self._tools = {"browser": _ConfirmingTool()}

    facts: list[dict[str, object]] = []
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=_RuntimeRegistry(),
    )
    adapter.set_approval_callback(
        lambda _tool, _args, _approval_id, policy_facts: (
            facts.append(policy_facts) or False
        )
    )

    adapter.execute(
        command={"tool_name": "browser", "args": {}},
        session_id="s1",
        trace_id="t1",
    )

    assert facts[0]["canonical_tool"] == "browser"
    assert facts[0]["reason_code"] == "CONFIRM_REQUIRED"
    assert facts[0]["duration_options"] == ["allow_once", "deny"]
