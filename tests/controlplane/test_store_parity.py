from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.runtime.audit import AuditEvent
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore
from openminion.modules.controlplane.storage.store import SQLiteControlPlaneStore

StoreFactory = Callable[[Path], object]
JsonDict = dict[str, Any]


def _store_factories() -> tuple[StoreFactory, ...]:
    return (
        lambda tmp_path: InMemoryControlPlaneStore(),
        lambda tmp_path: SQLiteControlPlaneStore(tmp_path / "cp.db"),
    )


def _close_store(store: object) -> None:
    closer = getattr(store, "close", None)
    if closer is not None:
        closer()


def _run_parity(tmp_path: Path, scenario: Callable[[object], JsonDict]) -> None:
    outcomes: list[JsonDict] = []
    for index, factory in enumerate(_store_factories()):
        store = factory(tmp_path / f"store-{index}")
        try:
            outcomes.append(scenario(store))
        finally:
            _close_store(store)
    assert outcomes[0] == outcomes[1]


def _turn_payloads(store: object, session_id: str) -> list[JsonDict]:
    rows = getattr(store, "list_turns")(session_id)
    out: list[JsonDict] = []
    for row in rows:
        if hasattr(row, "content"):
            out.append(
                {
                    "role": row.role,
                    "content": row.content,
                    "attachments": list(row.attachments),
                    "meta": dict(row.meta),
                }
            )
            continue
        payload = dict(row.get("payload") or {})
        out.append(
            {
                "role": payload.get("role") or "user",
                "content": payload.get("content") or row.get("text"),
                "attachments": list(payload.get("attachments") or []),
                "meta": dict(payload.get("meta") or {}),
            }
        )
    return out


def test_session_resolution_and_owned_binding_parity(tmp_path: Path) -> None:
    def scenario(store: object) -> JsonDict:
        session_id = getattr(store, "resolve_session")("user:a", "chat:a")
        same = getattr(store, "resolve_session")("user:a", "chat:a")
        other_user_allowed = getattr(store, "bind_session_owned")(
            user_key="user:b",
            chat_key="chat:b",
            session_id=session_id,
            is_admin=False,
        )
        admin_allowed = getattr(store, "bind_session_owned")(
            user_key="user:b",
            chat_key="chat:b",
            session_id=session_id,
            is_admin=True,
        )
        return {
            "same_session_reused": same == session_id,
            "owner": getattr(store, "session_owner")(session_id),
            "agent": getattr(store, "resolve_agent")(session_id),
            "other_user_allowed": other_user_allowed,
            "admin_allowed": admin_allowed,
        }

    _run_parity(tmp_path, scenario)


def test_append_and_persist_turns_parity(tmp_path: Path) -> None:
    def scenario(store: object) -> JsonDict:
        session_id = getattr(store, "resolve_session")("user:turn", "chat:turn")
        getattr(store, "append_turn")(
            session_id=session_id,
            role="assistant",
            content="hello",
            attachments=["artifact://one"],
            meta={"source": "parity"},
        )
        inbound = InboundMessage(
            user_key="user:turn",
            chat_key="chat:turn",
            text="user says hi",
            channel="cli",
            metadata={"x": "y"},
        )
        getattr(store, "persist_inbound")(inbound, session_id)
        turns = _turn_payloads(store, session_id)
        return {
            "count": len(turns),
            "first": turns[0],
            "last_content": turns[-1]["content"],
        }

    _run_parity(tmp_path, scenario)


def test_pending_clarify_lifecycle_parity(tmp_path: Path) -> None:
    def scenario(store: object) -> JsonDict:
        payload = {
            "clarify_id": "clarify-1",
            "trace_id": "trace-1",
            "questions": [{"id": "q1", "question": "Which city?"}],
        }
        getattr(store, "set_pending_clarify")("sess-1", payload)
        loaded = getattr(store, "get_pending_clarify")("sess-1")
        listed = getattr(store, "list_pending_clarifies")()
        getattr(store, "clear_pending_clarify")("sess-1")
        return {
            "loaded": loaded,
            "listed_count": len(listed),
            "after_clear": getattr(store, "get_pending_clarify")("sess-1"),
        }

    _run_parity(tmp_path, scenario)


def test_inbox_dedupe_claim_and_ack_parity(tmp_path: Path) -> None:
    def scenario(store: object) -> JsonDict:
        first = getattr(store, "enqueue_inbox")(
            channel="telegram",
            chat_id="chat-1",
            channel_message_id="msg-1",
            user_id="user-1",
            payload={"text": "hello"},
            inbound_id="inbox-fixed",
        )
        second = getattr(store, "enqueue_inbox")(
            channel="telegram",
            chat_id="chat-1",
            channel_message_id="msg-1",
            user_id="user-1",
            payload={"text": "hello"},
            inbound_id="inbox-other",
        )
        claimed = getattr(store, "claim_inbox")(lock_owner="worker")
        assert claimed is not None
        getattr(store, "ack_inbox")(claimed["inbox_id"])
        final = getattr(store, "get_inbox")(claimed["inbox_id"])
        return {
            "first": first,
            "second": second,
            "claimed_status": claimed["status"],
            "attempts": claimed["attempts"],
            "final_status": final["status"] if final else None,
        }

    _run_parity(tmp_path, scenario)


def test_inbox_retry_dead_letter_parity(tmp_path: Path) -> None:
    def scenario(store: object) -> JsonDict:
        inbox_id, _ = getattr(store, "enqueue_inbox")(
            channel="telegram",
            chat_id="chat-2",
            channel_message_id="msg-2",
            user_id="user-2",
            payload={"text": "retry"},
            inbound_id="inbox-retry",
        )
        claimed = getattr(store, "claim_inbox")(lock_owner="worker")
        assert claimed is not None
        decision = getattr(store, "mark_inbox_retry")(
            inbox_id, error="boom", max_attempts=1
        )
        row = getattr(store, "get_inbox")(inbox_id)
        return {"decision": decision, "status": row["status"] if row else None}

    _run_parity(tmp_path, scenario)


def test_outbox_claim_and_sent_parity(tmp_path: Path) -> None:
    def scenario(store: object) -> JsonDict:
        outbox_id = getattr(store, "enqueue_outbox")(
            channel="telegram",
            chat_id="chat-1",
            payload={"text": "send"},
            outbox_id="outbox-fixed",
        )
        claimed = getattr(store, "claim_outbox")(lock_owner="sender")
        assert claimed is not None
        getattr(store, "mark_outbox_sent")(outbox_id)
        final = getattr(store, "get_outbox")(outbox_id)
        return {
            "outbox_id": outbox_id,
            "claimed_status": claimed["status"],
            "attempts": claimed["attempts"],
            "final_status": final["status"] if final else None,
        }

    _run_parity(tmp_path, scenario)


def test_outbox_retry_dead_letter_parity(tmp_path: Path) -> None:
    def scenario(store: object) -> JsonDict:
        outbox_id = getattr(store, "enqueue_outbox")(
            channel="telegram",
            chat_id="chat-2",
            payload={"text": "retry"},
            outbox_id="outbox-retry",
        )
        claimed = getattr(store, "claim_outbox")(lock_owner="sender")
        assert claimed is not None
        decision = getattr(store, "mark_outbox_retry")(
            outbox_id, error="boom", max_attempts=1
        )
        row = getattr(store, "get_outbox")(outbox_id)
        return {"decision": decision, "status": row["status"] if row else None}

    _run_parity(tmp_path, scenario)


def test_audit_filter_parity(tmp_path: Path) -> None:
    def scenario(store: object) -> JsonDict:
        getattr(store, "put_audit")(
            AuditEvent(event_type="ev", session_id="sess-A", trace_id="trace-1")
        )
        getattr(store, "put_audit")(
            AuditEvent(event_type="ev", session_id="sess-B", trace_id="trace-2")
        )
        by_session = getattr(store, "list_audit")(session_id="sess-A")
        by_trace = getattr(store, "list_audit")(trace_id="trace-2")
        by_event = getattr(store, "list_audit")(event_type="ev")
        return {
            "by_session": [row["session_id"] for row in by_session],
            "by_trace": [row["trace_id"] for row in by_trace],
            "by_event_count": len(by_event),
        }

    _run_parity(tmp_path, scenario)


def test_rate_limit_parity(tmp_path: Path) -> None:
    def scenario(store: object) -> JsonDict:
        first = getattr(store, "increment_rate_limit")(
            key_type="user", key_id="u1", window_seconds=60, limit=1
        )
        second = getattr(store, "increment_rate_limit")(
            key_type="user", key_id="u1", window_seconds=60, limit=1
        )
        return {
            "first_allowed": first["allowed"],
            "first_count": first["count"],
            "second_allowed": second["allowed"],
            "second_count": second["count"],
            "limit": second["limit"],
            "window_seconds": second["window_seconds"],
        }

    _run_parity(tmp_path, scenario)
