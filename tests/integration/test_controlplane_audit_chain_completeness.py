from __future__ import annotations

from pathlib import Path

from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.runtime import EchoBrain
from openminion.modules.controlplane.runtime.audit import AuditEvent, AuditLogger
from openminion.modules.controlplane.runtime.auth import AuthEvaluator
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore


def _build_dispatcher(
    db_path: Path,
) -> tuple[
    ControlPlaneDispatcher,
    SQLiteControlPlaneStore,
    list[dict[str, object]],
    AuditLogger,
]:
    store = SQLiteControlPlaneStore(db_path)
    audit = AuditLogger(sink=store.put_audit)
    outbound: list[dict[str, object]] = []
    dispatcher = ControlPlaneDispatcher(
        store=store,
        router=Router(store),
        parser=SlashCommandParser(),
        command_registry=CommandRegistry(
            store=store,
            auth=AuthEvaluator(admin_user_keys=[]),
        ),
        brain_client=EchoBrain(),
        audit_logger=audit,
        outbound_sender=outbound.append,
    )
    return dispatcher, store, outbound, audit


def _event_names(events: list[AuditEvent]) -> list[str]:
    return [event.event_type for event in events]


def test_ten_message_audit_chain_keeps_traces_and_sessions_separate(
    tmp_path: Path,
) -> None:
    dispatcher, store, outbound, audit = _build_dispatcher(tmp_path / "cp.db")
    expected_by_trace: dict[str, list[str]] = {}

    try:
        for idx in range(10):
            trace_id = f"trace-chain-{idx}"
            is_command = idx % 2 == 1
            expected_by_trace[trace_id] = (
                [
                    "inbound.received",
                    "inbound.resolved",
                    "cp.command.detected",
                    "cp.command.executed",
                    "outbound.sent",
                ]
                if is_command
                else [
                    "inbound.received",
                    "inbound.resolved",
                    "cp.chat.dispatched",
                    "outbound.sent",
                ]
            )
            dispatcher.handle_inbound(
                InboundMessage(
                    channel="telegram",
                    user_key=f"telegram:user:{idx}",
                    chat_key=f"telegram:chat:{idx}",
                    user_id=str(idx),
                    chat_id=str(idx),
                    text="/help" if is_command else f"chat message {idx}",
                    metadata={"trace_id": trace_id},
                )
            )

        assert len(outbound) == 10
        assert len({payload["session_id"] for payload in outbound}) == 10
        assert len({event.trace_id for event in audit.events}) == 10

        for trace_id, expected_names in expected_by_trace.items():
            trace_events = [
                event for event in audit.events if event.trace_id == trace_id
            ]
            assert _event_names(trace_events) == expected_names
            session_ids = {
                event.session_id or str(event.details.get("session_id", ""))
                for event in trace_events
                if event.event_type != "inbound.received"
            }
            assert len(session_ids) == 1
    finally:
        store.close()
