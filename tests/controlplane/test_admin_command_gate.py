from __future__ import annotations

import pytest

from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.contracts.models import (
    ParsedCommand,
    ResolvedContext,
)
from openminion.modules.controlplane.runtime.auth import AuthEvaluator
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore


def _ctx(user_key: str) -> ResolvedContext:
    return ResolvedContext(
        user_key=user_key,
        chat_key="chat-1",
        session_id="sess-1",
        agent_id="agent:default",
        role="user",
        trace_id="trace-1",
        span_id="span-1",
    )


@pytest.mark.parametrize(
    "canonical,args",
    [
        ("artifact.purge", []),
        ("memory.promote", ["mem-1"]),
        ("config.set", ["k", "v"]),
        ("approve", ["req-1"]),
        ("deny", ["req-1"]),
    ],
)
def test_admin_commands_require_admin_role(
    canonical: str,
    args: list[str],
) -> None:
    store = InMemoryControlPlaneStore()
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    registry = CommandRegistry(store=store, auth=auth)

    command = ParsedCommand(
        canonical=canonical,
        original_text=f"/{canonical}",
        args=args,
    )

    denied = registry.execute(command, _ctx("user:regular"))
    assert denied.ok is False
    assert denied.error is not None
    assert denied.error["code"] == "PERMISSION_DENIED"

    allowed = registry.execute(command, _ctx("user:admin"))
    assert allowed.ok is True


def test_non_admin_command_skips_admin_gate() -> None:
    store = InMemoryControlPlaneStore()
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    registry = CommandRegistry(store=store, auth=auth)

    command = ParsedCommand(canonical="help", original_text="/help", args=[])
    result = registry.execute(command, _ctx("user:regular"))

    assert result.ok is True
