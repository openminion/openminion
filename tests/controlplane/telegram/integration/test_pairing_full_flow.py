from __future__ import annotations

from pathlib import Path

from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime import AuditLogger, EchoBrain
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
from openminion.modules.controlplane.channels.telegram.pairing import (
    TelegramPairingService,
)
from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
)
from openminion.modules.controlplane.channels.telegram.state import (
    TelegramPollStateStore,
)

from openminion.modules.controlplane.runtime.worker.inbox import InboxWorker
from openminion.modules.controlplane.runtime.worker.outbox import OutboxWorker

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


PAIRED_USER_ID = 456
PAIRED_CHAT_ID = 456


def _make_telegram_config() -> TelegramChannelConfig:
    return TelegramChannelConfig(
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
            dm_policy="allowlist",
            allow_from_user_ids=[],
            group_policy="deny",
        ),
        pairing=PairingConfig(
            enabled=True,
            mode="dm",
            default_scopes=["chat.interact"],
        ),
        actions=ActionsConfig(
            send_message=True,
            edit_message=False,
            reactions=False,
            inline_buttons=False,
        ),
    )


def _build_runner(
    *,
    config: TelegramChannelConfig,
    transport: DeterministicTelegramTransport,
    poll_state_store: TelegramPollStateStore,
    sqlite_store: SQLiteControlPlaneStore,
) -> tuple[
    TelegramPollingRunner,
    CapturingOutboundSender,
    AuditLogger,
    InboxWorker,
    OutboxWorker,
]:
    from openminion.modules.controlplane.runtime.dispatcher import (
        ControlPlaneDispatcher,
    )

    parser = MockCommandParser()
    command_registry = MockCommandRegistry()
    outbound = CapturingOutboundSender()
    audit = AuditLogger()
    router = Router(sqlite_store)
    runtime = ControlPlaneDispatcher(
        store=sqlite_store,
        router=router,
        parser=parser,
        command_registry=command_registry,
        brain_client=EchoBrain(),
        outbound_sender=outbound,
        audit_logger=audit,
    )

    delivery = TelegramDeliveryService(
        api=transport.api,
        delivery_config=config.delivery,
        reply_config=config.reply,
    )
    pairing_service = TelegramPairingService(
        config=config.pairing,
        store=poll_state_store,
        controlplane_store=sqlite_store,
    )
    runner = TelegramPollingRunner(
        config=config,
        api=transport.api,
        runtime=runtime,
        delivery=delivery,
        state_store=poll_state_store,
        pairing_service=pairing_service,
        audit_logger=audit,
    )
    runner._api = transport.api
    runner._delivery._api = transport.api
    runner._initialized = True
    runner._bot_username = "testbot"
    runner._account_id = "telegram-bot:123456789"
    inbox_worker = attach_inbox_worker(runner, store=sqlite_store, audit_logger=audit)
    outbox_worker = attach_outbox_worker(runner, store=sqlite_store, audit_logger=audit)
    return runner, outbound, audit, inbox_worker, outbox_worker


def test_pairing_full_flow_happy_and_rejected_replay(tmp_path: Path) -> None:
    poll_state_store = TelegramPollStateStore(str(tmp_path / "poll.db"))
    sqlite_store = SQLiteControlPlaneStore(str(tmp_path / "cp.db"))
    try:
        config = _make_telegram_config()

        standalone_pairing = TelegramPairingService(
            config=config.pairing,
            store=poll_state_store,
            controlplane_store=sqlite_store,
        )
        issued = standalone_pairing.issue_token(
            expected_user_id=PAIRED_USER_ID,
            expected_chat_id=PAIRED_CHAT_ID,
            token_ttl_seconds=60,
        )
        assert issued.token
        assert issued.expires_at_ts > 0
        assert issued.scopes == ["chat.interact"]

        import sqlite3

        with sqlite3.connect(str(tmp_path / "poll.db")) as conn:
            row = conn.execute(
                "SELECT expected_user_id, expected_chat_id, expires_at_ts "
                "FROM telegram_pair_tokens"
            ).fetchone()
        assert row is not None
        assert int(row[0]) == PAIRED_USER_ID
        assert int(row[1]) == PAIRED_CHAT_ID
        assert int(row[2]) == issued.expires_at_ts

        assert (
            sqlite_store.get_pairing(channel="telegram", chat_id=str(PAIRED_CHAT_ID))
            is None
        )

        transport = DeterministicTelegramTransport(bot_token="test-token")
        runner, _outbound, audit, inbox_worker, outbox_worker = _build_runner(
            config=config,
            transport=transport,
            poll_state_store=poll_state_store,
            sqlite_store=sqlite_store,
        )
        transport.inject_message(
            chat_id=PAIRED_CHAT_ID,
            user_id=PAIRED_USER_ID,
            text=f"/start {issued.token}",
            message_id=1,
        )
        processed = runner.run_once()
        assert processed == 1
        drain_outbox(outbox_worker)

        pairing_texts = transport.get_outbound_texts()
        assert any("Paired" in t for t in pairing_texts), pairing_texts

        binding = sqlite_store.get_pairing(
            channel="telegram", chat_id=str(PAIRED_CHAT_ID)
        )
        assert binding is not None
        assert str(binding["status"]).lower() == "active"
        assert str(binding["user_id"]) == str(PAIRED_USER_ID)
        paired_session_id = str(binding["session_id"])
        assert paired_session_id

        transport.inject_message(
            chat_id=PAIRED_CHAT_ID,
            user_id=PAIRED_USER_ID,
            text="hello paired world",
            message_id=2,
        )
        processed = runner.run_once()
        assert processed == 1
        drain_inbox(inbox_worker)
        drain_outbox(outbox_worker)

        dispatched_events = [
            e for e in audit.events if e.get("event") == "cp.chat.dispatched"
        ]
        assert dispatched_events, audit.events
        assert str(dispatched_events[-1].get("agent_id") or "").strip() != ""

        assert any("hello paired world" in c for c in transport.get_outbound_texts())

        transport.inject_message(
            chat_id=PAIRED_CHAT_ID,
            user_id=PAIRED_USER_ID,
            text=f"/start {issued.token}",
            message_id=3,
        )
        binding_before = sqlite_store.get_pairing(
            channel="telegram", chat_id=str(PAIRED_CHAT_ID)
        )
        assert binding_before is not None
        before_last_seen = str(binding_before.get("last_seen_at") or "")

        processed = runner.run_once()
        assert processed == 1
        drain_outbox(outbox_worker)

        all_texts = transport.get_outbound_texts()
        assert any("failed or expired" in t.lower() for t in all_texts), all_texts

        binding_after = sqlite_store.get_pairing(
            channel="telegram", chat_id=str(PAIRED_CHAT_ID)
        )
        assert binding_after is not None
        assert str(binding_after["session_id"]) == paired_session_id
        assert str(binding_after["status"]).lower() == "active"
        assert before_last_seen == str(binding_after.get("last_seen_at") or "")

        with sqlite3.connect(str(tmp_path / "poll.db")) as conn:
            outcomes = [
                row[0]
                for row in conn.execute(
                    "SELECT outcome FROM telegram_pair_attempts ORDER BY id"
                )
            ]
        assert "already_used" in outcomes, outcomes
    finally:
        poll_state_store.close()
        sqlite_store.close()
