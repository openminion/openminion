from __future__ import annotations

from typing import Any

from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.pairing import (
    ControlPlanePairingService,
    ControlPlanePairingStore,
    PairingAttempt,
    PairingPolicy,
)
from openminion.modules.controlplane.runtime.audit import AuditLogger
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore


class _Adapter:
    channel_id = "test"
    account_namespace = "test"

    def extract_pairing_attempt(
        self,
        inbound: InboundMessage,
        *,
        channel_context: dict[str, Any] | None = None,
    ) -> PairingAttempt | None:
        if not inbound.text.startswith("/openminion pair "):
            return None
        token = inbound.text.rsplit(" ", 1)[-1]
        return PairingAttempt(
            channel="test",
            token=token,
            account_id=inbound.user_key,
            chat_key=inbound.chat_key,
            chat_type="private",
            extra={"subject_id": inbound.chat_key, "user_id": inbound.user_key},
        )

    def format_pairing_hint(self, token: str, *, ttl_seconds: int) -> str:
        return f"pair {token} in {ttl_seconds}s"

    def format_success_reply(self) -> str:
        return "paired"

    def format_failure_reply(self, reason: str) -> str:
        return f"failed:{reason}"


def _message(text: str) -> InboundMessage:
    return InboundMessage(user_key="test:user:1", chat_key="test:chat:2", text=text)


def _service() -> tuple[
    ControlPlanePairingService, InMemoryControlPlaneStore, AuditLogger
]:
    store = InMemoryControlPlaneStore()
    audit = AuditLogger()
    service = ControlPlanePairingService(
        policy=PairingPolicy(hash_pepper="pepper", default_scopes=["chat.interact"]),
        store=ControlPlanePairingStore(store),
        adapter=_Adapter(),
        bridge_store=store,
        audit_logger=audit,
    )
    return service, store, audit


def test_pairing_service_issues_and_consumes_token() -> None:
    service, store, audit = _service()
    issued = service.issue_token(
        expected_account_id="test:user:1",
        expected_chat_key="test:chat:2",
        token_ttl_seconds=60,
        scopes=["chat.interact"],
        token="token_1",
    )
    result = service.handle_pairing_attempt(
        _message(f"/openminion pair {issued.token}")
    )

    assert result.handled is True
    assert result.reply_text == "paired"
    assert store.get_pairing(channel="test", chat_id="test:chat:2") is not None
    assert [event.event_type for event in audit.events] == [
        "cp.pairing.token.issued",
        "cp.pairing.token.consumed",
    ]


def test_pairing_service_rejects_replay_and_records_audit() -> None:
    service, _store, audit = _service()
    issued = service.issue_token(
        expected_account_id="test:user:1",
        expected_chat_key="test:chat:2",
        token_ttl_seconds=60,
        scopes=["chat.interact"],
        token="token_2",
    )
    assert service.handle_pairing_attempt(_message(f"/openminion pair {issued.token}"))
    replay = service.handle_pairing_attempt(
        _message(f"/openminion pair {issued.token}")
    )

    assert replay.reply_text == "failed:already_used"
    assert audit.events[-1].event_type == "cp.pairing.token.rejected"
    assert audit.events[-1].outcome == "already_used"


def test_pairing_service_ignores_non_pairing_message() -> None:
    service, _store, _audit = _service()
    result = service.handle_pairing_attempt(_message("hello"))
    assert result.handled is False
