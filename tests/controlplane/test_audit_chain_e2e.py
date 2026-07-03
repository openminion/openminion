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

    event_names = [ev.event_type for ev in audit.events]

    assert "inbound.received" in event_names
    assert "inbound.resolved" in event_names
    assert "outbound.sent" in event_names
    assert (
        event_names.index("inbound.received")
        < event_names.index("inbound.resolved")
        < event_names.index("outbound.sent")
    )

    resolved_event = next(e for e in audit.events if e.event_type == "inbound.resolved")
    outbound_event = next(e for e in audit.events if e.event_type == "outbound.sent")
    assert resolved_event.details.get("session_id") == session_id
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

    required_sequence = [
        "inbound.received",
        "inbound.resolved",
        "cp.command.detected",
        "cp.command.executed",
        "outbound.sent",
    ]
    for name in required_sequence:
        assert name in event_names, f"missing {name} in {event_names}"

    indices = [event_names.index(name) for name in required_sequence]
    assert indices == sorted(indices), f"command chain out of order: {event_names}"

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
