from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.cli.interactive.terminal.shell.approval import (
    build_terminal_approval_callback,
)
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.policy.models import PolicyConfig
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.modules.runtime.sync import run_async_compat
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.ops import REGISTRAR, local_ops_service
from openminion.tools.ops.interfaces import TOOL_OPS_COMMAND_RUN

pytestmark = pytest.mark.e2e


class _ApprovalOverlay:
    def __init__(self, decisions: tuple[bool, ...]) -> None:
        self._decisions = iter(decisions)
        self.prompts: list[str] = []

    async def present_confirm_async(self, prompt: str) -> bool:
        self.prompts.append(prompt)
        return next(self._decisions)

    async def present_approval_async(self, *_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("ops commands must use exact allow-once approval")


def test_focus_remote_command_shows_exact_effect_and_reprompts(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    REGISTRAR.register(registry)
    policy = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    service = local_ops_service()
    service.action_policy = policy
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=registry,
        policy_ctl=policy,
        ops_service=service,
        artifactctl=SimpleNamespace(),
    )
    overlay = _ApprovalOverlay((False, True, True))
    terminal_approval = build_terminal_approval_callback(
        overlay=overlay,
        session_grants={"ops.command.run"},
    )
    adapter.set_approval_callback(
        lambda tool_name, args, call_id: run_async_compat(
            terminal_approval(tool_name, args, call_id)
        )
    )

    denied_plan = service.plan_command(
        target_id="local",
        argv=("printf", "denied"),
        session_id="focus-ops",
    )
    denied = adapter.execute(
        command={
            "tool_name": TOOL_OPS_COMMAND_RUN,
            "args": {
                "plan_id": denied_plan.plan_id,
                "plan_hash": denied_plan.plan_hash,
            },
        },
        session_id="focus-ops",
        trace_id="trace-denied",
    )
    assert denied["status"] == "error"
    assert service.list_evidence() == ()

    for text in ("ready", "again"):
        plan = service.plan_command(
            target_id="local",
            argv=("printf", text),
            session_id="focus-ops",
        )
        result = adapter.execute(
            command={
                "tool_name": TOOL_OPS_COMMAND_RUN,
                "args": {"plan_id": plan.plan_id, "plan_hash": plan.plan_hash},
            },
            session_id="focus-ops",
            trace_id=f"trace-{text}",
        )
        assert result["status"] == "success"
        assert result["outputs"]["verified"] is False
        assert result["outputs"]["content"] == text
        assert result["outputs"]["data"]["evidence"]["stdout_preview"] == text
        assert "failure" not in result["outputs"]["data"]["evidence"]

    assert len(overlay.prompts) == 3
    assert '"argv":["printf","denied"]' in overlay.prompts[0]
    assert '"target_id":"local"' in overlay.prompts[1]
    assert '"target_revision":1' in overlay.prompts[1]
    assert '"argv":["printf","ready"]' in overlay.prompts[1]
    assert '"cwd":""' in overlay.prompts[1]
    assert '"timeout_seconds":30.0' in overlay.prompts[1]
    assert '"expires_at":' in overlay.prompts[1]
    assert "…" not in overlay.prompts[1]
