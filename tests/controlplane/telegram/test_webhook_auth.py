from __future__ import annotations

from dataclasses import replace
from typing import Any

from openminion.modules.controlplane.channels.telegram.config import (
    AccessConfig,
    ActionsConfig,
    DeliveryConfig,
    PairingConfig,
    ReplyConfig,
    TelegramChannelConfig,
    WebhookConfig,
)
from openminion.modules.controlplane.channels.telegram.models import DeliveryResult
from openminion.modules.controlplane.channels.telegram.webhook import (
    TelegramWebhookRunner,
)


class _AuthStore:
    def __init__(self, pairing: dict[str, Any] | None) -> None:
        self._pairing = pairing
        self.lookups: list[tuple[str, str]] = []

    def get_pairing(self, *, channel: str, chat_id: str) -> dict[str, Any] | None:
        self.lookups.append((channel, chat_id))
        return self._pairing


class _AuthClarifyStore(_AuthStore):
    def __init__(self, pairing: dict[str, Any] | None) -> None:
        super().__init__(pairing)
        self.pending: dict[str, dict[str, Any]] = {}

    def resolve_session(self, user_key: str, chat_key: str) -> str:
        return "sess-1"

    def set_pending_clarify(self, session_id: str, payload: dict[str, Any]) -> None:
        self.pending[session_id] = dict(payload)

    def get_pending_clarify(self, session_id: str) -> dict[str, Any] | None:
        payload = self.pending.get(session_id)
        return dict(payload) if isinstance(payload, dict) else None

    def clear_pending_clarify(self, session_id: str) -> None:
        self.pending.pop(session_id, None)


class _Runtime:
    def __init__(self, store: _AuthStore) -> None:
        self.store = store
        self.calls: list[Any] = []

    def handle_inbound(self, inbound: Any) -> dict[str, Any]:
        self.calls.append(inbound)
        return {
            "type": "chat",
            "text": "ok",
            "session_id": "sess-1",
            "agent_id": "agent:default",
        }


class _ClarifyRuntime(_Runtime):
    def handle_inbound(self, inbound: Any) -> dict[str, Any]:
        self.calls.append(inbound)
        return {
            "type": "clarify_error",
            "text": "Unknown clarify id `wrong-id`. Please answer using clarify id `clar-1` from the latest prompt.",
            "status": "waiting_user",
            "session_id": "sess-1",
            "agent_id": "agent:default",
            "data": {
                "error_code": "UNKNOWN_CLARIFY_ID",
            },
        }


class _API:
    def __init__(self) -> None:
        self.callback_answers: list[str] = []

    def get_me(self) -> dict[str, Any]:
        return {"id": "123", "username": "testbot"}

    def answer_callback_query(self, callback_query_id: str) -> dict[str, Any]:
        self.callback_answers.append(callback_query_id)
        return {"ok": True}


class _Delivery:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.payloads: list[dict[str, Any]] = []

    def send_text(self, *, text: str, target: Any) -> DeliveryResult:
        self.texts.append(text)
        return DeliveryResult(
            ok=True,
            sent_messages=[
                {
                    "message_id": 1,
                    "chat": {"id": target.chat_id},
                    "message_thread_id": target.topic_id,
                }
            ],
        )

    def send_payload(self, payload: dict[str, Any], target: Any) -> DeliveryResult:
        self.payloads.append(payload)
        return DeliveryResult(
            ok=True,
            sent_messages=[
                {
                    "message_id": 2,
                    "chat": {"id": target.chat_id},
                    "message_thread_id": target.topic_id,
                }
            ],
        )


class _AuditCollector:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(
        self, event_type: str, *, details: dict[str, object], **kwargs: object
    ) -> None:
        payload = dict(details)
        payload.update(kwargs)
        self.events.append((event_type, payload))


def _config() -> TelegramChannelConfig:
    return TelegramChannelConfig(
        enabled=True,
        bot_token="x",
        access=AccessConfig(
            dm_policy="allow", group_policy="allow", mention_only_in_groups=False
        ),
        webhook=WebhookConfig(
            enabled=True, secret="test-auth-secret"
        ),  # CSH-03: secret required
        pairing=PairingConfig(enabled=False),
        actions=ActionsConfig(
            send_message=True, edit_message=True, reactions=False, inline_buttons=True
        ),
        reply=ReplyConfig(mode="reply_to_user"),
        delivery=DeliveryConfig(parse_mode="plain", chunk_limit=500),
    )


def _private_update(text: str) -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "text": text,
            "chat": {"id": 111, "type": "private"},
            "from": {"id": 111, "username": "alice", "first_name": "Alice"},
        },
    }


def test_webhook_scope_authorizer_blocks_unpaired_command() -> None:
    auth_store = _AuthStore(pairing=None)
    runtime = _Runtime(auth_store)
    delivery = _Delivery()
    runner = TelegramWebhookRunner(
        config=_config(),
        api=_API(),  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
        delivery=delivery,  # type: ignore[arg-type]
        state_store=None,
    )

    result = runner.handle_webhook_update(
        _private_update("/help"), secret_token="test-auth-secret"
    )

    assert result["success"] is True
    assert runtime.calls == []
    assert auth_store.lookups == [("telegram", "111")]
    assert any("not paired" in text for text in delivery.texts)


def test_webhook_emits_reason_coded_access_and_delivery_audit_events() -> None:
    deny_runtime = _Runtime(_AuthStore(pairing=None))
    deny_delivery = _Delivery()
    deny_audit = _AuditCollector()
    deny_cfg = replace(
        _config(),
        access=AccessConfig(dm_policy="allowlist", allow_from_user_ids=[]),
    )
    deny_runner = TelegramWebhookRunner(
        config=deny_cfg,
        api=_API(),  # type: ignore[arg-type]
        runtime=deny_runtime,  # type: ignore[arg-type]
        delivery=deny_delivery,  # type: ignore[arg-type]
        state_store=None,
        audit_logger=deny_audit,
    )
    deny_result = deny_runner.handle_webhook_update(
        _private_update("hello"), secret_token="test-auth-secret"
    )
    assert deny_result["success"] is True
    assert any(
        event == "cp.access.deny" and data.get("reason") == "dm_allowlist_miss"
        for event, data in deny_audit.events
    )

    allow_runtime = _Runtime(
        _AuthStore(pairing={"pairing_id": "pair-1", "scopes": ["chat.interact"]})
    )
    allow_delivery = _Delivery()
    allow_audit = _AuditCollector()
    allow_cfg = replace(
        _config(),
        access=AccessConfig(
            dm_policy="allow", group_policy="allow", mention_only_in_groups=False
        ),
    )
    allow_runner = TelegramWebhookRunner(
        config=allow_cfg,
        api=_API(),  # type: ignore[arg-type]
        runtime=allow_runtime,  # type: ignore[arg-type]
        delivery=allow_delivery,  # type: ignore[arg-type]
        state_store=None,
        audit_logger=allow_audit,
    )
    allow_result = allow_runner.handle_webhook_update(
        _private_update("hello"), secret_token="test-auth-secret"
    )
    assert allow_result["success"] is True
    assert any(
        event == "cp.access.allow" and data.get("reason") == "ok"
        for event, data in allow_audit.events
    )
    assert any(
        event == "cp.route.runtime_dispatch"
        and data.get("reason") == "runtime_dispatch"
        for event, data in allow_audit.events
    )
    assert any(
        event == "cp.delivery.sent" and data.get("reason") == "delivery_ok"
        for event, data in allow_audit.events
    )


def test_webhook_scope_authorizer_blocks_missing_scopes() -> None:
    auth_store = _AuthStore(
        pairing={"pairing_id": "pair-1", "scopes": ["chat.interact"]}
    )
    runtime = _Runtime(auth_store)
    delivery = _Delivery()
    runner = TelegramWebhookRunner(
        config=_config(),
        api=_API(),  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
        delivery=delivery,  # type: ignore[arg-type]
        state_store=None,
    )

    result = runner.handle_webhook_update(
        _private_update("/new"), secret_token="test-auth-secret"
    )

    assert result["success"] is True
    assert runtime.calls == []
    assert auth_store.lookups == [("telegram", "111")]
    assert any("Permission denied" in text for text in delivery.texts)


def test_webhook_routes_unknown_clarify_id_to_runtime() -> None:
    auth_store = _AuthClarifyStore(
        pairing={
            "pairing_id": "pair-1",
            "scopes": [
                "cp.message.read",
                "cp.message.write",
                "session.read",
                "session.write",
                "run.start",
            ],
        }
    )
    auth_store.set_pending_clarify(
        "sess-1",
        {
            "clarify_id": "clar-1",
            "trace_id": "trace-clar-1",
            "session_id": "sess-1",
            "questions": [{"id": "q1", "question": "Which city?"}],
        },
    )
    runtime = _ClarifyRuntime(auth_store)
    delivery = _Delivery()
    runner = TelegramWebhookRunner(
        config=_config(),
        api=_API(),  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
        delivery=delivery,  # type: ignore[arg-type]
        state_store=None,
    )

    result = runner.handle_webhook_update(
        _private_update("/clarify wrong-id q1 San Diego"),
        secret_token="test-auth-secret",
    )

    assert result["success"] is True
    assert len(runtime.calls) == 1
    meta = runtime.calls[0].meta
    metadata = runtime.calls[0].metadata
    assert meta["trace_id"] == "trace-clar-1"
    assert metadata["trace_id"] == "trace-clar-1"
    assert meta == metadata
    clarify_answer = meta["clarify_answer"]
    assert clarify_answer["clarify_id"] == "wrong-id"
    assert clarify_answer["question_id"] == "q1"


def test_webhook_prefers_controlplane_pending_clarify_store() -> None:
    auth_store = _AuthClarifyStore(
        pairing={
            "pairing_id": "pair-1",
            "scopes": [
                "cp.message.read",
                "cp.message.write",
                "session.read",
                "session.write",
                "run.start",
            ],
        }
    )
    auth_store.set_pending_clarify(
        "sess-1",
        {
            "clarify_id": "clar-1",
            "trace_id": "trace-cp",
            "session_id": "sess-1",
            "questions": [{"id": "q1", "question": "Which city?"}],
        },
    )
    runtime = _ClarifyRuntime(auth_store)
    delivery = _Delivery()
    runner = TelegramWebhookRunner(
        config=_config(),
        api=_API(),  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
        delivery=delivery,  # type: ignore[arg-type]
        state_store=None,
    )

    result = runner.handle_webhook_update(
        _private_update("/clarify wrong-id q1 San Diego"),
        secret_token="test-auth-secret",
    )

    assert result["success"] is True
    assert len(runtime.calls) == 1
    meta = runtime.calls[0].meta
    metadata = runtime.calls[0].metadata
    assert meta["trace_id"] == "trace-cp"
    assert metadata["trace_id"] == "trace-cp"
    assert meta == metadata
    clarify_answer = meta["clarify_answer"]
    assert clarify_answer["clarify_id"] == "wrong-id"
    assert clarify_answer["question_id"] == "q1"
    assert clarify_answer["answer"] == "San Diego"
    assert not any("Unknown clarify id" in text for text in delivery.texts)
    assert any(
        "Unknown clarify id `wrong-id`" in payload["text"]
        for payload in delivery.payloads
    )
