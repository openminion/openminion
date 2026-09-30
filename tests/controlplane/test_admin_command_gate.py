from __future__ import annotations

import pytest

from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.commands.module import (
    AuthRequirement,
    CommandSchema,
    CommandSpec,
)
from openminion.modules.controlplane.contracts.models import (
    CommandResult,
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
def test_admin_commands_reject_non_admin_before_execution(
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

    unavailable = registry.execute(command, _ctx("user:admin"))
    assert unavailable.ok is False
    assert unavailable.error is not None
    assert unavailable.error["code"] == "FEATURE_UNAVAILABLE"


def test_non_admin_command_skips_admin_gate() -> None:
    store = InMemoryControlPlaneStore()
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    registry = CommandRegistry(store=store, auth=auth)

    command = ParsedCommand(canonical="help", original_text="/help", args=[])
    result = registry.execute(command, _ctx("user:regular"))

    assert result.ok is True


def test_registered_admin_requirement_is_enforced_without_hardcoded_name() -> None:
    store = InMemoryControlPlaneStore()
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    registry = CommandRegistry(store=store, auth=auth)

    def handler(_command: ParsedCommand, _ctx: ResolvedContext) -> CommandResult:
        return CommandResult(ok=True, text="ok")

    registry.register_command_spec(
        CommandSpec(
            name="identity.upsert",
            schema=CommandSchema(
                name="identity.upsert",
                description="test",
                usage="/identity.upsert",
            ),
            handler=handler,
            auth_requirement=AuthRequirement.ADMIN,
            module_name="identity",
        )
    )
    command = ParsedCommand(
        canonical="identity.upsert",
        original_text="/identity.upsert",
        args=[],
    )

    denied = registry.execute(command, _ctx("user:regular"))
    allowed = registry.execute(command, _ctx("user:admin"))

    assert denied.ok is False
    assert denied.error["code"] == "PERMISSION_DENIED"
    assert allowed.ok is True
