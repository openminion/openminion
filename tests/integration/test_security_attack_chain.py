from __future__ import annotations

from pathlib import Path

from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.contracts.models import (
    InboundMessage,
    ParsedCommand,
    ResolvedContext,
)
from openminion.modules.controlplane.runtime import AuditLogger, EchoBrain
from openminion.modules.controlplane.runtime.auth import AuthEvaluator
from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore
from openminion.modules.controlplane.channels.telegram.config import PairingConfig
from openminion.modules.controlplane.channels.telegram.models import (
    TelegramInboundEnvelope,
    TelegramUser,
)
from openminion.modules.controlplane.channels.telegram.pairing import (
    TelegramPairingService,
)
from openminion.modules.controlplane.channels.telegram.state import (
    TelegramPollStateStore,
)
from tests.controlplane.telegram.integration.fixtures import CapturingOutboundSender


ALICE_ID = 101
BOB_ID = 202


def _make_pairing_config() -> PairingConfig:
    return PairingConfig(
        enabled=True,
        mode="dm",
        default_scopes=["chat.interact"],
        token_ttl_seconds=120,
    )


def _envelope(
    *,
    user_id: int,
    text: str,
    update_id: int,
    message_id: int,
) -> TelegramInboundEnvelope:
    return TelegramInboundEnvelope(
        update_id=update_id,
        raw_type="message",
        chat_id=user_id,
        message_id=message_id,
        text=text,
        from_user=TelegramUser(id=user_id, username=f"user{user_id}"),
        chat_type="private",
    )


def _pair_user(
    *,
    pairing: TelegramPairingService,
    store: SQLiteControlPlaneStore,
    user_id: int,
    update_id: int,
) -> str:
    issued = pairing.issue_token(
        expected_user_id=user_id,
        expected_chat_id=user_id,
        token_ttl_seconds=120,
    )
    result = pairing.handle_start_pairing(
        _envelope(
            user_id=user_id,
            text=f"/start {issued.token}",
            update_id=update_id,
            message_id=update_id,
        ),
        bot_username="testbot",
    )
    assert result.handled is True
    assert result.reply_text == "Paired ✅"
    binding = store.get_pairing(channel="telegram", chat_id=str(user_id))
    assert binding is not None
    return str(binding["session_id"])


def _ctx(user_id: int, session_id: str) -> ResolvedContext:
    return ResolvedContext(
        user_key=f"telegram:{user_id}",
        chat_key=f"telegram:{user_id}",
        session_id=session_id,
        agent_id="agent:default",
        role="user",
        trace_id=f"trace-{user_id}",
        span_id=f"span-{user_id}",
    )


def test_security_attack_chain_blocks_session_takeover_and_admin_bypass(
    tmp_path: Path,
) -> None:
    poll_state_store = TelegramPollStateStore(str(tmp_path / "poll.db"))
    sqlite_store = SQLiteControlPlaneStore(str(tmp_path / "cp.db"))
    try:
        pairing = TelegramPairingService(
            config=_make_pairing_config(),
            store=poll_state_store,
            controlplane_store=sqlite_store,
        )
        alice_session = _pair_user(
            pairing=pairing,
            store=sqlite_store,
            user_id=ALICE_ID,
            update_id=1,
        )
        bob_session = _pair_user(
            pairing=pairing,
            store=sqlite_store,
            user_id=BOB_ID,
            update_id=2,
        )
        assert alice_session != bob_session

        audit = AuditLogger()
        auth = AuthEvaluator(admin_user_keys=["telegram:999"])
        router = Router(sqlite_store, auth=auth, audit_logger=audit)
        registry = CommandRegistry(store=sqlite_store, auth=auth, audit_logger=audit)
        outbound = CapturingOutboundSender()
        dispatcher = ControlPlaneDispatcher(
            store=sqlite_store,
            router=router,
            parser=SlashCommandParser(),
            command_registry=registry,
            brain_client=EchoBrain(),
            outbound_sender=outbound,
            audit_logger=audit,
        )

        alice_reply = dispatcher.handle_inbound(
            InboundMessage(
                user_key=f"telegram:{ALICE_ID}",
                chat_key=f"telegram:{ALICE_ID}",
                channel="telegram",
                user_id=str(ALICE_ID),
                chat_id=str(ALICE_ID),
                text="hello from alice",
            )
        )
        assert alice_reply["text"] == "[agent:default] hello from alice"

        attacked = router.resolve(
            InboundMessage(
                user_key=f"telegram:{BOB_ID}",
                chat_key=f"telegram:{BOB_ID}",
                channel="telegram",
                user_id=str(BOB_ID),
                chat_id=str(BOB_ID),
                text=f"/session {alice_session}",
            )
        )
        assert attacked.session_id == bob_session
        bindings = sqlite_store.list_session_bindings()
        bob_bindings = [
            item for item in bindings if item["chat_key"] == f"telegram:{BOB_ID}"
        ]
        assert bob_bindings
        assert all(item["session_id"] == bob_session for item in bob_bindings)
        denied = []
        for event in audit.events:
            payload = event.to_dict() if hasattr(event, "to_dict") else dict(event)
            event_type = payload.get("event_type") or payload.get("event")
            if event_type == "session.bind.denied":
                denied.append(payload)
        assert denied
        denied_payload = denied[-1].get("details", denied[-1])
        assert denied_payload["requested_session_id"] == alice_session
        assert denied_payload["owner_user_key"] == f"telegram:{ALICE_ID}"
        assert denied_payload["reason"] == "owner_mismatch"

        approve = registry.execute(
            ParsedCommand(
                canonical="approve",
                original_text="/approve req-1",
                args=["req-1"],
            ),
            _ctx(BOB_ID, bob_session),
        )
        assert approve.ok is False
        assert approve.error == {
            "code": "PERMISSION_DENIED",
            "reason": "command 'approve' requires admin role",
        }
    finally:
        if callable(getattr(poll_state_store, "close", None)):
            poll_state_store.close()
        sqlite_store.close()
