from __future__ import annotations

import pytest

from openminion.modules.controlplane.runtime.audit import AuditLogger
from openminion.modules.controlplane.runtime.auth import AuthEvaluator
from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime import EchoBrain
from openminion.modules.controlplane.storage.store import PostgresControlPlaneStore

from tests.storage.postgres_test_utils import open_postgres_record_store

pytestmark = pytest.mark.postgres


@pytest.mark.postgres
def test_postgres_dispatcher_roundtrip() -> None:
    with open_postgres_record_store("cpe02_dispatcher_roundtrip") as (
        record_store,
        _schema_name,
    ):
        store = PostgresControlPlaneStore(record_store=record_store)
        try:
            audit = AuditLogger(sink=store.put_audit)
            outbound: list[dict] = []
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

            payload = dispatcher.handle_inbound(
                InboundMessage(
                    user_key="telegram:cpe02-user",
                    chat_key="telegram:cpe02-chat",
                    text="hello from cpe-02",
                    channel="telegram",
                    user_id="cpe02-user",
                    chat_id="cpe02-chat",
                )
            )

            assert len(outbound) == 1
            assert outbound[0] is payload
            assert payload["type"] == "chat"
            assert "hello from cpe-02" in payload["text"]
            session_id = payload["session_id"]
            assert session_id

            sessions = store.list_sessions("telegram:cpe02-user", "telegram:cpe02-chat")
            assert any(s["session_id"] == session_id for s in sessions), (
                f"session row not persisted to postgres: {sessions}"
            )

            turns = store.list_turns(session_id)
            assert any(
                t.get("role") == "user"
                and "hello from cpe-02" in (t.get("content") or "")
                for t in turns
            ), f"inbound turn not persisted: {turns}"

            audit_rows = store.list_audit(session_id=session_id)
            audit_events = {row.get("event_type") for row in audit_rows}
            assert "outbound.sent" in audit_events, (
                f"expected outbound.sent in audit events, got {audit_events!r}"
            )
        finally:
            store.close()
