from __future__ import annotations

from pathlib import Path

from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime.security import ScopeAuthorizer
from openminion.modules.controlplane.runtime.worker.inbox import InboxWorker
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore


class _AuditCollector:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(
        self, event_type: str, *, details: dict[str, object], **kwargs: object
    ) -> None:
        payload = dict(details)
        payload.update(kwargs)
        self.events.append((event_type, payload))


class _FailingBrain:
    contract_version = "controlplane.v1"

    def run(
        self,
        *,
        session_id: str,
        agent_id: str,
        user_text: str | None,
        attachment_refs: list[str],
        trace_id: str,
    ) -> dict[str, object]:
        del session_id, agent_id, user_text, attachment_refs, trace_id
        raise RuntimeError("network secret=abc123")


def _make_worker(
    store: SQLiteControlPlaneStore,
    audit: _AuditCollector,
    *,
    max_attempts: int = 3,
    max_backoff_s: int = 1,
) -> InboxWorker:
    parser = SlashCommandParser()
    dispatcher = ControlPlaneDispatcher(
        store=store,
        router=Router(store),
        parser=parser,
        command_registry=CommandRegistry(store=store, auth=None),
        brain_client=_FailingBrain(),
    )
    return InboxWorker(
        store=store,
        dispatcher=dispatcher,
        authorizer=ScopeAuthorizer(store=store),
        audit_logger=audit,
        max_attempts=max_attempts,
        max_backoff_s=max_backoff_s,
    )


def _pair(store: SQLiteControlPlaneStore) -> None:
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


def _enqueue(store: SQLiteControlPlaneStore) -> str:
    inbox_id, created = store.enqueue_inbox(
        channel="telegram",
        chat_id="100",
        channel_message_id="msg-1",
        user_id="42",
        payload={
            "text": "hello",
            "user_key": "telegram:42",
            "chat_key": "telegram:100",
        },
    )
    assert created is True
    return inbox_id


def _make_due(store: SQLiteControlPlaneStore, inbox_id: str) -> None:
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE cp_inbox SET next_attempt_at = ? WHERE inbox_id = ?",
            ("1970-01-01T00:00:00+00:00", inbox_id),
        )


def test_inbox_worker_retries_then_dead_letters(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    audit = _AuditCollector()
    _pair(store)
    inbox_id = _enqueue(store)
    worker = _make_worker(store, audit, max_attempts=3, max_backoff_s=1)

    first = worker.run_once()
    assert first is not None and first["status"] == "retry"
    assert store.claim_inbox(lock_owner="early") is None

    _make_due(store, inbox_id)
    second = worker.run_once()
    assert second is not None and second["status"] == "retry"

    _make_due(store, inbox_id)
    third = worker.run_once()
    assert third is not None and third["status"] == "dead"

    row = store.get_inbox(inbox_id)
    assert row is not None
    assert row["status"] == "dead"
    assert row["attempts"] == 3
    assert store.claim_inbox(lock_owner="after-dead") is None
    assert [event for event, _details in audit.events].count("cp.inbox.retry") == 2
    assert [event for event, _details in audit.events].count("cp.inbox.deadletter") == 1
    store.close()


def test_inbox_processing_stale_lock_is_reclaimed(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    _pair(store)
    inbox_id = _enqueue(store)

    claimed = store.claim_inbox(lock_owner="first")
    assert claimed is not None
    assert claimed["inbox_id"] == inbox_id
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE cp_inbox SET locked_at = ? WHERE inbox_id = ?",
            ("1970-01-01T00:00:00+00:00", inbox_id),
        )

    reclaimed = store.claim_inbox(lock_owner="second", reclaim_ttl_s=1)
    assert reclaimed is not None
    assert reclaimed["inbox_id"] == inbox_id
    assert reclaimed["lock_owner"] == "second"
    store.close()


def test_authorization_denial_is_acknowledged_not_retried(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    audit = _AuditCollector()
    _enqueue(store)
    worker = _make_worker(store, audit, max_attempts=3, max_backoff_s=1)

    result = worker.run_once()
    assert result is not None
    assert result["status"] == "unpaired"
    row = store.claim_inbox(lock_owner="after-auth")
    assert row is None
    store.close()
