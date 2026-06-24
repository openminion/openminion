from __future__ import annotations

from pathlib import Path

from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.interfaces import CONTROLPLANE_INTERFACE_VERSION
from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.contracts.outbound import (
    OutboundPayload,
    to_legacy_payload,
)
from openminion.modules.controlplane.runtime.rate_limit import (
    ControlPlaneRateLimiter,
    RateLimitPolicy,
)
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime import EchoBrain
from openminion.modules.controlplane.runtime.security import ScopeAuthorizer
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore
from openminion.modules.controlplane.runtime.worker.inbox import InboxWorker
from openminion.modules.controlplane.runtime.worker.outbox import OutboxWorker
from openminion.modules.controlplane.runtime.channels import ChannelRegistry


class _AuditCollector:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(
        self, event_type: str, *, details: dict[str, object], **kwargs: object
    ) -> None:
        payload = dict(details)
        payload.update(kwargs)
        self.events.append((event_type, payload))


def _make_dispatcher(store: SQLiteControlPlaneStore) -> ControlPlaneDispatcher:
    return ControlPlaneDispatcher(
        store=store,
        router=Router(store),
        parser=SlashCommandParser(),
        command_registry=CommandRegistry(store=store, auth=None),
        brain_client=EchoBrain(),
    )


def test_inbox_enqueue_is_idempotent(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    payload = {"text": "hello", "user_key": "u1", "chat_key": "c1"}
    first_id, first_inserted = store.enqueue_inbox(
        channel="telegram",
        chat_id="c1",
        channel_message_id="m-1",
        user_id="u1",
        payload=payload,
    )
    second_id, second_inserted = store.enqueue_inbox(
        channel="telegram",
        chat_id="c1",
        channel_message_id="m-1",
        user_id="u1",
        payload=payload,
    )
    assert first_inserted is True
    assert second_inserted is False
    assert first_id == second_id
    store.close()


def test_unpaired_inbox_message_is_rejected_except_pair_command(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    dispatcher = _make_dispatcher(store)
    worker = InboxWorker(
        store=store,
        dispatcher=dispatcher,
        authorizer=ScopeAuthorizer(store=store),
    )
    store.enqueue_inbox(
        channel="telegram",
        chat_id="100",
        channel_message_id="msg-1",
        user_id="42",
        payload={
            "text": "/help",
            "user_key": "telegram:42",
            "chat_key": "telegram:100",
        },
    )
    result = worker.run_once()
    assert result is not None
    assert result["status"] == "unpaired"
    outbox = store.claim_outbox(lock_owner="test")
    assert outbox is not None
    assert "not paired" in outbox["payload_json"]

    store.enqueue_inbox(
        channel="telegram",
        chat_id="100",
        channel_message_id="msg-1b",
        user_id="42",
        payload={
            "text": "/pair ABC123",
            "user_key": "telegram:42",
            "chat_key": "telegram:100",
        },
    )
    pair_result = worker.run_once()
    assert pair_result is not None
    assert pair_result["status"] == "done"
    store.close()


def test_paired_inbox_command_flows_to_outbox(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    session_id = store.new_session("telegram:42", "telegram:100")
    store.upsert_pairing(
        channel="telegram",
        chat_id="100",
        user_id="42",
        session_id=session_id,
        scopes=[
            "cp.message.read",
            "cp.message.write",
            "session.read",
            "session.write",
            "run.start",
        ],
    )
    dispatcher = _make_dispatcher(store)
    worker = InboxWorker(
        store=store,
        dispatcher=dispatcher,
        authorizer=ScopeAuthorizer(store=store),
    )
    store.enqueue_inbox(
        channel="telegram",
        chat_id="100",
        channel_message_id="msg-2",
        user_id="42",
        payload={"text": "/new", "user_key": "telegram:42", "chat_key": "telegram:100"},
    )
    result = worker.run_once()
    assert result is not None
    assert result["status"] == "done"
    outbox = store.claim_outbox(lock_owner="test")
    assert outbox is not None
    assert '"type": "command_result"' in outbox["payload_json"]
    store.close()


def test_rate_limiter_blocks_excess_messages(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    session_id = store.new_session("telegram:42", "telegram:100")
    store.upsert_pairing(
        channel="telegram",
        chat_id="100",
        user_id="42",
        session_id=session_id,
        scopes=[
            "cp.message.read",
            "cp.message.write",
            "session.read",
            "session.write",
            "run.start",
        ],
    )
    dispatcher = _make_dispatcher(store)
    worker = InboxWorker(
        store=store,
        dispatcher=dispatcher,
        authorizer=ScopeAuthorizer(store=store),
        rate_limiter=ControlPlaneRateLimiter(
            store=store,
            policy=RateLimitPolicy(
                chat_window_s=60,
                chat_limit=1,
                user_window_s=60,
                user_limit=10,
                session_window_s=60,
                session_limit=10,
            ),
        ),
    )
    store.enqueue_inbox(
        channel="telegram",
        chat_id="100",
        channel_message_id="msg-3",
        user_id="42",
        payload={
            "text": "hello",
            "user_key": "telegram:42",
            "chat_key": "telegram:100",
        },
    )
    first = worker.run_once()
    assert first is not None and first["status"] == "done"

    store.enqueue_inbox(
        channel="telegram",
        chat_id="100",
        channel_message_id="msg-4",
        user_id="42",
        payload={
            "text": "hello again",
            "user_key": "telegram:42",
            "chat_key": "telegram:100",
        },
    )
    second = worker.run_once()
    assert second is not None and second["status"] == "rate_limited"
    store.close()


def test_outbox_worker_retries_then_dead_letters(tmp_path: Path) -> None:
    class _FailingAdapter:
        contract_version = CONTROLPLANE_INTERFACE_VERSION
        channel_id = "telegram"

        def start(self, stop_event=None) -> None:  # pragma: no cover - not used
            del stop_event

        def deliver(self, payload, ctx):  # noqa: ANN001
            del payload, ctx
            raise RuntimeError("network down")

    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    audit = _AuditCollector()
    outbox_id = store.enqueue_outbox(
        channel="telegram",
        chat_id="100",
        payload={"text": "hello"},
    )
    registry = ChannelRegistry()
    registry.register(_FailingAdapter())
    worker = OutboxWorker(
        store=store,
        registry=registry,
        audit_logger=audit,
        max_attempts=3,
        max_backoff_s=1,
    )

    status_1 = worker.run_once()
    assert status_1 is not None and status_1["status"] in {"retry", "failed"}
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE cp_outbox SET next_attempt_at = ? WHERE outbox_id = ?",
            ("1970-01-01T00:00:00+00:00", outbox_id),
        )

    status_2 = worker.run_once()
    assert status_2 is not None
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE cp_outbox SET next_attempt_at = ? WHERE outbox_id = ?",
            ("1970-01-01T00:00:00+00:00", outbox_id),
        )

    status_3 = worker.run_once()
    assert status_3 is not None and status_3["status"] == "dead"
    final = store.get_outbox(outbox_id)
    assert final is not None
    assert final["status"] == "dead"
    assert any(
        event == "cp.delivery.failed" and data.get("reason") == "delivery_exception"
        for event, data in audit.events
    )
    assert any(
        event == "cp.outbox.deadletter"
        and data.get("reason") == "max_attempts_exceeded"
        for event, data in audit.events
    )
    store.close()


def test_outbox_worker_routes_delivery_via_registry(tmp_path: Path) -> None:
    class _Adapter:
        contract_version = CONTROLPLANE_INTERFACE_VERSION
        channel_id = "telegram"

        def __init__(self) -> None:
            self.calls: list[tuple[dict[str, object], object]] = []

        def start(self, stop_event=None) -> None:  # pragma: no cover - not used
            del stop_event

        def deliver(self, payload, ctx):
            self.calls.append((dict(payload), ctx))
            return {"ok": True}

    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    audit = _AuditCollector()
    outbox_id = store.enqueue_outbox(
        channel="telegram",
        chat_id="100",
        payload={"text": "hello", "type": "chat"},
    )
    registry = ChannelRegistry()
    adapter = _Adapter()
    registry.register(adapter)

    worker = OutboxWorker(store=store, registry=registry, audit_logger=audit)
    result = worker.run_once()

    assert result is not None
    assert result["status"] == "sent"
    assert result["outbox_id"] == outbox_id
    assert len(adapter.calls) == 1
    sent_payload, sent_ctx = adapter.calls[0]
    assert sent_payload["text"] == "hello"
    assert sent_ctx.channel == "telegram"
    assert sent_ctx.chat_id == "100"
    assert sent_ctx.outbox_id == outbox_id
    assert any(
        event == "cp.route.outbox.selected" and data.get("reason") == "registry_route"
        for event, data in audit.events
    )
    assert any(
        event == "cp.delivery.sent" and data.get("reason") == "delivery_ok"
        for event, data in audit.events
    )
    final = store.get_outbox(outbox_id)
    assert final is not None
    assert final["status"] == "sent"
    store.close()


def test_outbox_worker_requires_registry(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    try:
        OutboxWorker(store=store, registry=None)  # type: ignore[arg-type]
        raise AssertionError("expected compatibility failure")
    except Exception as exc:
        assert "channel_registry" in str(exc)
    finally:
        store.close()


def test_dispatcher_returns_typed_outbound_payload(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    dispatcher = _make_dispatcher(store)
    payload, _ctx = dispatcher.dispatch(
        InboundMessage(user_key="telegram:42", chat_key="telegram:100", text="hello")
    )
    assert isinstance(payload, OutboundPayload)
    legacy = to_legacy_payload(payload)
    assert legacy["type"] == "chat"
    assert "hello" in legacy["text"]
    store.close()
