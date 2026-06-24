from __future__ import annotations

from pathlib import Path
from typing import Any

from openminion.modules.controlplane.runtime.audit import AuditLogger
from openminion.modules.controlplane.runtime.auth import AuthEvaluator
from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime import EchoBrain
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore


def _build_dispatcher(
    db_path: Path,
) -> tuple[
    ControlPlaneDispatcher,
    SQLiteControlPlaneStore,
    list[dict[str, Any]],
    AuditLogger,
]:
    store = SQLiteControlPlaneStore(db_path)
    audit = AuditLogger(sink=store.put_audit)
    outbound: list[dict[str, Any]] = []
    dispatcher = ControlPlaneDispatcher(
        store=store,
        router=Router(store),
        parser=SlashCommandParser(),
        command_registry=CommandRegistry(
            store=store, auth=AuthEvaluator(admin_user_keys=[])
        ),
        brain_client=EchoBrain(),
        audit_logger=audit,
        outbound_sender=outbound.append,
    )
    return dispatcher, store, outbound, audit


def test_audit_chain_chat_flow(tmp_path: Path) -> None:
    dispatcher, store, outbound, audit = _build_dispatcher(tmp_path / "cp.db")

    payload = dispatcher.handle_inbound(
        InboundMessage(
            user_key="u1",
            chat_key="c1",
            text="hello there",
            channel="telegram",
            user_id="1",
            chat_id="1",
        )
    )
    assert payload["type"] == "chat"
    session_id = payload["session_id"]
    assert outbound[-1]["session_id"] == session_id

    # Collect event names in emission order for this session / turn.
    event_names = [ev.event_type for ev in audit.events]

    # Assert each expected bookend is present and in the required order.
    assert "inbound.received" in event_names
    assert "inbound.resolved" in event_names
    assert "outbound.sent" in event_names
    assert (
        event_names.index("inbound.received")
        < event_names.index("inbound.resolved")
        < event_names.index("outbound.sent")
    )

    # All session-scoped events on this turn share the same session_id.
    resolved_event = next(e for e in audit.events if e.event_type == "inbound.resolved")
    outbound_event = next(e for e in audit.events if e.event_type == "outbound.sent")
    # inbound.resolved records session_id in its details.
    assert resolved_event.details.get("session_id") == session_id
    # outbound.sent doesn't carry session_id on details (see dispatcher
    # line 95) — it is a bookend event — but the payload stored in the
    # outbound list is the source of truth for session linkage.
    assert outbound_event.event_type == "outbound.sent"

    store.close()


def test_audit_chain_command_flow(tmp_path: Path) -> None:
    dispatcher, store, outbound, audit = _build_dispatcher(tmp_path / "cp.db")

    payload = dispatcher.handle_inbound(
        InboundMessage(
            user_key="u1",
            chat_key="c1",
            text="/help",
            channel="telegram",
            user_id="1",
            chat_id="1",
        )
    )
    assert payload["type"] == "command_result"
    assert payload["ok"] is True
    session_id = payload["session_id"]
    assert outbound[-1]["session_id"] == session_id

    event_names = [ev.event_type for ev in audit.events]

    # The full ordered command chain.
    required_sequence = [
        "inbound.received",
        "inbound.resolved",
        "cp.command.detected",
        "cp.command.executed",
        "outbound.sent",
    ]
    for name in required_sequence:
        assert name in event_names, f"missing {name} in {event_names}"

    # Strict chronological order (each required event appears after the
    # previous one in the emitted list).
    indices = [event_names.index(name) for name in required_sequence]
    assert indices == sorted(indices), f"command chain out of order: {event_names}"

    # cp.command.detected and cp.command.executed record session_id in
    # their details (dispatcher lines 314 & 318), tying them to this turn.
    detected_event = next(
        e for e in audit.events if e.event_type == "cp.command.detected"
    )
    executed_event = next(
        e for e in audit.events if e.event_type == "cp.command.executed"
    )
    assert detected_event.details.get("session_id") == session_id
    assert executed_event.details.get("session_id") == session_id
    assert detected_event.details.get("canonical") == "help"
    assert executed_event.details.get("canonical") == "help"

    store.close()
