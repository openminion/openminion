from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

import pytest

from openminion.modules.controlplane.contracts.models import (
    AttachmentInput,
    AttachmentRef,
    InboundMessage,
)
from openminion.modules.controlplane.storage import build_controlplane_store
from openminion.modules.controlplane.storage.store import (
    PostgresControlPlaneStore,
    SQLiteControlPlaneStore,
)
from openminion.modules.storage.engine import StorageEngineConfig
from tests.storage.postgres_test_utils import (
    build_postgres_storage_config,
    open_postgres_record_store,
)


def _backend_params():
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", marks=pytest.mark.postgres, id="postgres"),
    ]


@pytest.fixture(params=_backend_params())
def controlplane_store_case(request: pytest.FixtureRequest, tmp_path: Path):
    backend = str(request.param)
    with ExitStack() as stack:
        if backend == "sqlite":
            store = SQLiteControlPlaneStore(tmp_path / "cp.db")
        else:
            record_store, _schema_name = stack.enter_context(
                open_postgres_record_store("mpt1_controlplane")
            )
            store = PostgresControlPlaneStore(record_store=record_store)
        stack.callback(store.close)
        yield backend, store


def test_controlplane_store_round_trip(controlplane_store_case) -> None:
    _backend, store = controlplane_store_case
    session_id = store.new_session("user:1", "chat:1")
    rebound = store.rebind_session("user:1", "chat:1")
    assert rebound != session_id

    store.ensure_agent("agent:ops", "Ops")
    assert any(item["id"] == "agent:ops" for item in store.list_agents())
    store.set_agent(rebound, "agent:ops")
    assert store.resolve_agent(rebound) == "agent:ops"
    store.set_session_title(rebound, "Ops Session")
    assert store.get_session_title(rebound) == "Ops Session"

    store.bind_session("user:1", "chat:bound", rebound)
    binding = store.get_chat_binding("chat:bound")
    assert binding is not None
    assert binding["session_id"] == rebound
    store.set_chat_binding("chat:2", rebound, "agent:ops")
    assert store.resolve_session("user:1", "chat:2") == rebound
    assert any(item["session_id"] == rebound for item in store.list_sessions("user:1"))

    store.upsert_user("user:1", role="admin", profile_meta={"team": "ops"})
    user = store.get_user("user:1")
    assert user is not None
    assert user["role"] == "admin"

    inbound_row_id = store.put_inbound(
        chat_key="chat:1",
        user_key="user:1",
        text="hello",
        payload={"message_id": "msg-1"},
        session_id=rebound,
        agent_id="agent:ops",
    )
    outbound_row_id = store.put_outbound(
        chat_key="chat:1",
        text="world",
        payload={"message": "world"},
        session_id=rebound,
        agent_id="agent:ops",
    )
    assert inbound_row_id > 0
    assert outbound_row_id > 0

    persisted = InboundMessage(
        user_key="user:1",
        chat_key="chat:1",
        text="persisted",
        channel="cli",
        metadata={"source": "test"},
    )
    store.persist_inbound(persisted, rebound)
    store.append_turn(
        session_id=rebound,
        role="assistant",
        content="done",
        attachments=["artifact://one"],
        meta={"source": "append"},
    )
    turns = store.list_turns(rebound)
    assert len(turns) >= 3

    refs = store.attachment_refs_from_inputs(
        [
            AttachmentInput(
                name="file.txt", mime="text/plain", url="https://example.com/file.txt"
            ),
            AttachmentRef(kind="artifact", name="saved", ref="artifact://saved"),
        ]
    )
    assert refs[0] == "https://example.com/file.txt"
    assert refs[1] == "artifact://saved"

    store.set_pending_clarify(rebound, {"question": "continue?"})
    assert store.get_pending_clarify(rebound) == {"question": "continue?"}
    store.clear_pending_clarify(rebound)
    assert store.get_pending_clarify(rebound) is None

    inbox_id, created = store.enqueue_inbox(
        channel="telegram",
        chat_id="chat:1",
        channel_message_id="telegram-msg-1",
        user_id="user:1",
        payload={"text": "ping"},
    )
    assert created is True
    claimed_inbox = store.claim_inbox(lock_owner="worker-1")
    assert claimed_inbox is not None
    assert claimed_inbox["inbox_id"] == inbox_id
    store.ack_inbox(inbox_id)

    inbox_id_2, _created_2 = store.enqueue_inbox(
        channel="telegram",
        chat_id="chat:1",
        channel_message_id="telegram-msg-2",
        user_id="user:1",
        payload={"text": "ping-2"},
    )
    claimed_inbox_2 = store.claim_inbox(lock_owner="worker-2")
    assert claimed_inbox_2 is not None
    store.fail_inbox(inbox_id_2, "network")

    outbox_id = store.enqueue_outbox(
        channel="telegram",
        chat_id="chat:1",
        payload={"text": "out"},
    )
    claimed_outbox = store.claim_outbox(lock_owner="worker-3")
    assert claimed_outbox is not None
    assert claimed_outbox["outbox_id"] == outbox_id
    retry_status = store.mark_outbox_retry(outbox_id, error="retry me")
    assert retry_status in {"pending", "failed", "retry"}
    outbox = store.get_outbox(outbox_id)
    assert outbox is not None
    store.mark_outbox_sent(outbox_id)

    pairing_id = store.upsert_pairing(
        channel="telegram",
        chat_id="chat:pair",
        user_id="user:1",
        session_id=rebound,
        scopes=["chat.read"],
        note="linked",
    )
    assert pairing_id
    pairing = store.get_pairing(channel="telegram", chat_id="chat:pair")
    assert pairing is not None
    store.touch_pairing(channel="telegram", chat_id="chat:pair")
    backfill = store.backfill_pairings_to_principals(channel="telegram")
    assert backfill["scanned"] >= 1

    principal_id = store.upsert_principal(meta={"kind": "user"})
    store.bind_principal_subject(
        principal_id=principal_id,
        channel="telegram",
        subject_id="subject:1",
        scopes=["chat.read"],
        meta={"from": "test"},
    )
    assert (
        store.resolve_principal(channel="telegram", subject_id="subject:1")
        == principal_id
    )
    subject = store.get_channel_subject(channel="telegram", subject_id="subject:1")
    assert subject is not None
    store.touch_channel_subject(channel="telegram", subject_id="subject:1")

    rate = store.increment_rate_limit(
        key_type="chat",
        key_id="chat:1",
        window_seconds=60,
        limit=5,
    )
    assert rate["allowed"] is True

    store.put_audit(
        {
            "event_id": "audit-1",
            "event_type": "turn.completed",
            "severity": "info",
            "outcome": "ok",
            "session_id": rebound,
            "trace_id": "trace-1",
            "details": {"step": "done"},
        }
    )
    audit_rows = store.list_audit(session_id=rebound)
    assert audit_rows


def test_build_controlplane_store_returns_sqlite_store(tmp_path: Path) -> None:
    store = build_controlplane_store(
        config=StorageEngineConfig(
            root_dir=tmp_path / "storage",
            sqlite_path=tmp_path / "cp.db",
            fallback_root=tmp_path,
            record_backend="record.sqlite",
        ),
        database_path=tmp_path / "cp.db",
    )
    try:
        assert isinstance(store, SQLiteControlPlaneStore)
    finally:
        store.close()


@pytest.mark.postgres
def test_build_controlplane_store_returns_postgres_store(tmp_path: Path) -> None:
    with open_postgres_record_store("mpt1_controlplane_factory") as (
        _record_store,
        schema_name,
    ):
        store = build_controlplane_store(
            config=build_postgres_storage_config(
                tmp_path=tmp_path,
                schema_name=schema_name,
                sqlite_name="cp.db",
            ),
            database_path=tmp_path / "cp.db",
        )
        try:
            assert isinstance(store, PostgresControlPlaneStore)
        finally:
            store.close()
