from __future__ import annotations

from pathlib import Path

from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.contracts.models import (
    AuthContext,
    ParsedCommand,
    ResolvedContext,
)
from openminion.modules.controlplane.runtime import EchoBrain
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime.security import ScopeAuthorizer
from openminion.modules.controlplane.runtime.worker.inbox import InboxWorker
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore


def _context() -> ResolvedContext:
    return ResolvedContext(
        user_key="telegram:42",
        chat_key="telegram:100",
        session_id="session-1",
        agent_id="agent:default",
        role="user",
        trace_id="trace-1",
        span_id="span-1",
    )


def test_builtin_commands_use_command_specific_scopes() -> None:
    store = SQLiteControlPlaneStore(":memory:")
    registry = CommandRegistry(store=store)
    authorizer = ScopeAuthorizer(command_registry=registry)
    read_only = AuthContext(role="paired", scopes=("cp.message.read",))

    help_command = ParsedCommand(canonical="help", original_text="/help", args=[])
    new_command = ParsedCommand(
        canonical="session.new", original_text="/session new", args=[]
    )

    assert authorizer.command_allowed(help_command, read_only) == (True, "ok")
    assert authorizer.command_allowed(new_command, read_only) == (
        False,
        "missing scopes: session.write",
    )
    assert authorizer.message_allowed(read_only) == (
        False,
        "missing scopes: cp.message.write, run.start",
    )
    assert authorizer.message_allowed(
        AuthContext(role="paired", scopes=("chat.interact",))
    ) == (True, "ok")
    store.close()


def test_unavailable_commands_fail_and_stay_out_of_help() -> None:
    store = SQLiteControlPlaneStore(":memory:")
    registry = CommandRegistry(store=store)

    result = registry.execute(
        ParsedCommand(canonical="export", original_text="/export", args=[]),
        _context(),
    )
    help_result = registry.execute(
        ParsedCommand(canonical="help", original_text="/help", args=[]),
        _context(),
    )

    assert result.ok is False
    assert result.error == {
        "code": "FEATURE_UNAVAILABLE",
        "feature": "Session export",
    }
    assert "/export" not in help_result.text
    assert "/artifact.ls" not in help_result.text
    assert "/memory.ls" not in help_result.text
    assert "/run.status" not in help_result.text
    store.close()


def test_durable_inbox_denies_chat_without_message_and_run_scopes(
    tmp_path: Path,
) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    session_id = store.new_session("telegram:42", "telegram:100")
    store.upsert_pairing(
        channel="telegram",
        chat_id="100",
        user_id="42",
        session_id=session_id,
        scopes=["cp.message.read", "session.read"],
    )
    parser = SlashCommandParser()
    registry = CommandRegistry(store=store)
    dispatcher = ControlPlaneDispatcher(
        store=store,
        router=Router(store),
        parser=parser,
        command_registry=registry,
        brain_client=EchoBrain(),
    )
    worker = InboxWorker(
        store=store,
        dispatcher=dispatcher,
        authorizer=ScopeAuthorizer(store=store, command_registry=registry),
    )
    store.enqueue_inbox(
        channel="telegram",
        chat_id="100",
        channel_message_id="message-1",
        user_id="42",
        payload={
            "text": "hello",
            "user_key": "telegram:42",
            "chat_key": "telegram:100",
        },
    )

    result = worker.run_once()
    outbox = store.claim_outbox(lock_owner="test")

    assert result is not None and result["status"] == "denied"
    assert outbox is not None
    assert "missing scopes: cp.message.write, run.start" in outbox["payload_json"]
    store.close()
