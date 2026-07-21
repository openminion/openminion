from __future__ import annotations

import time
from typing import Any

from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime import EchoBrain
from openminion.modules.controlplane.runtime.worker.inbox import InboxWorker
from openminion.modules.controlplane.runtime.worker.outbox import OutboxWorker
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore
from openminion.modules.controlplane.channels.telegram.config import (
    AccessConfig,
    ActionsConfig,
    PairingConfig,
    PollingConfig,
    TelegramChannelConfig,
)
from openminion.modules.controlplane.channels.telegram.delivery import (
    TelegramDeliveryService,
)
from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
)

from tests.controlplane.telegram.integration.fixtures import (
    CapturingOutboundSender,
    MockCommandParser,
    MockCommandRegistry,
    attach_inbox_worker,
    attach_outbox_worker,
    drain_inbox,
    drain_outbox,
)
from tests.controlplane.telegram.integration.transports import (
    DeterministicTelegramTransport,
)


ALLOWED_GROUP_ID = -100123
DENIED_GROUP_ID = -100999
GROUP_USER_ID = 777


class CapturingAuditLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(
        self,
        event_type: str,
        *,
        outcome: str = "ok",
        severity: str = "info",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.events.append(
            {
                "event_type": event_type,
                "outcome": outcome,
                "severity": severity,
                "details": dict(details or {}),
            }
        )


def _inject_group_message(
    transport: DeterministicTelegramTransport,
    *,
    chat_id: int,
    user_id: int,
    text: str,
    message_id: int,
) -> int:
    update = {
        "update_id": 0,
        "message": {
            "message_id": message_id,
            "from": {"id": user_id, "is_bot": False, "first_name": "Grp"},
            "chat": {"id": chat_id, "type": "supergroup", "title": "Test Group"},
            "date": int(time.time()),
            "text": text,
        },
    }
    return transport.api.enqueue_update(update)


def _build_runner(
    *,
    tmp_path,
    config: TelegramChannelConfig,
    transport: DeterministicTelegramTransport,
    audit: CapturingAuditLogger,
) -> tuple[
    TelegramPollingRunner,
    CapturingOutboundSender,
    SQLiteControlPlaneStore,
    InboxWorker,
    OutboxWorker,
]:
    sqlite_store = SQLiteControlPlaneStore(str(tmp_path / "cp.db"))
    outbound = CapturingOutboundSender()
    router = Router(sqlite_store)
    runtime = ControlPlaneDispatcher(
        store=sqlite_store,
        router=router,
        parser=MockCommandParser(),
        command_registry=MockCommandRegistry(),
        brain_client=EchoBrain(),
        outbound_sender=outbound,
        audit_logger=audit,
    )
    delivery = TelegramDeliveryService(
        api=transport.api,
        delivery_config=config.delivery,
        reply_config=config.reply,
    )
    runner = TelegramPollingRunner(
        config=config,
        api=transport.api,
        runtime=runtime,
        delivery=delivery,
        state_store=None,
        pairing_service=None,
        audit_logger=audit,
    )
    runner._api = transport.api
    runner._delivery._api = transport.api
    runner._initialized = True
    runner._bot_username = "testbot"
    runner._account_id = "telegram-bot:123456789"
    inbox_worker = attach_inbox_worker(runner, store=sqlite_store, audit_logger=audit)
    outbox_worker = attach_outbox_worker(runner, store=sqlite_store, audit_logger=audit)
    return runner, outbound, sqlite_store, inbox_worker, outbox_worker


def test_group_chat_allowlisted_dispatches_and_denied_short_circuits(tmp_path) -> None:
    config = TelegramChannelConfig(
        enabled=True,
        bot_token="test-token",
        mode="polling",
        polling=PollingConfig(
            timeout_seconds=1,
            limit=100,
            persist_offset=False,
            drop_pending_on_start=False,
        ),
        access=AccessConfig(
            # Spec uses these names; real AccessConfig fields:
            #   group_policy, allow_group_chat_ids, dm_policy
            dm_policy="deny",
            group_policy="allowlist",
            allow_group_chat_ids=[ALLOWED_GROUP_ID],
            # Happy-path text is plain (no command / no @mention); disable
            # mention gate so the message can reach the runtime dispatch.
            mention_only_in_groups=False,
        ),
        pairing=PairingConfig(enabled=False, mode="off"),
        actions=ActionsConfig(
            send_message=True,
            edit_message=False,
            reactions=False,
            inline_buttons=False,
        ),
    )

    transport = DeterministicTelegramTransport(bot_token="test-token")
    audit = CapturingAuditLogger()
    runner, outbound, sqlite_store, inbox_worker, outbox_worker = _build_runner(
        tmp_path=tmp_path,
        config=config,
        transport=transport,
        audit=audit,
    )

    try:
        _inject_group_message(
            transport,
            chat_id=ALLOWED_GROUP_ID,
            user_id=GROUP_USER_ID,
            text="hello group",
            message_id=1,
        )
        _inject_group_message(
            transport,
            chat_id=DENIED_GROUP_ID,
            user_id=GROUP_USER_ID,
            text="go away",
            message_id=2,
        )

        processed = runner.run_once()
        assert processed == 2
        drain_inbox(inbox_worker)
        drain_outbox(outbox_worker)

        outbound_texts = transport.get_outbound_texts()
        assert any("hello group" in t for t in outbound_texts), outbound_texts
        assert not any("go away" in t for t in outbound_texts), outbound_texts

        deny_events = [e for e in audit.events if e["event_type"] == "cp.access.deny"]
        assert len(deny_events) == 1, audit.events
        deny = deny_events[0]
        assert deny["outcome"] == "denied"
        assert deny["severity"] == "warning"
        deny_details = deny["details"]
        assert str(deny_details.get("reason")) == "group_allowlist_miss"
        assert str(deny_details.get("chat_id")) == str(DENIED_GROUP_ID)

        allow_events = [
            e
            for e in audit.events
            if e["event_type"] == "cp.access.allow"
            and str(e["details"].get("chat_id")) == str(ALLOWED_GROUP_ID)
        ]
        assert len(allow_events) == 1, audit.events

        assert outbound.get_all() == []
    finally:
        sqlite_store.close()
