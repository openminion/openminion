from pathlib import Path

import pytest

from openminion.modules.storage.runtime.idempotency_store import IdempotencyStore
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.sqlite import connect_database


@pytest.fixture
def store(tmp_path: Path):
    database_path = tmp_path / "state" / "openminion.db"
    migrate_database(database_path)
    connection = connect_database(database_path)
    try:
        yield IdempotencyStore(connection)
    finally:
        connection.close()


def test_reserve_and_get(store: IdempotencyStore) -> None:
    created = store.reserve(method="turn.send", idempotency_key="k1", request_hash="h1")
    assert created is True

    record = store.get(method="turn.send", idempotency_key="k1")
    assert record is not None
    assert record.request_hash == "h1"
    assert record.status == "in_progress"
    assert record.response == {}


def test_reserve_is_unique_per_method_key(store: IdempotencyStore) -> None:
    first = store.reserve(method="turn.send", idempotency_key="k1")
    second = store.reserve(method="turn.send", idempotency_key="k1")
    other_method = store.reserve(method="turn.update", idempotency_key="k1")

    assert first is True
    assert second is False
    assert other_method is True


def test_complete_updates_existing_record(store: IdempotencyStore) -> None:
    store.reserve(method="turn.send", idempotency_key="k1", request_hash="h1")
    record = store.complete(
        method="turn.send",
        idempotency_key="k1",
        response={"ok": True},
        status="completed",
    )

    assert record.status == "completed"
    assert record.request_hash == "h1"
    assert record.response == {"ok": True}


def test_complete_inserts_when_missing(store: IdempotencyStore) -> None:
    record = store.complete(
        method="turn.send",
        idempotency_key="missing",
        request_hash="h2",
        response={"result": "cached"},
        status="completed",
    )
    assert record.request_hash == "h2"
    assert record.response == {"result": "cached"}
    assert record.status == "completed"
