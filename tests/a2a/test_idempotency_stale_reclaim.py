from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from openminion.modules.a2a.constants import (
    A2A_IDEMPOTENCY_STATUS_IN_PROGRESS,
    A2A_IDEMPOTENCY_STATUS_SUCCESS,
)
from openminion.modules.a2a.storage import SQLiteStateStore
from openminion.modules.a2a.storage.base import idempotency_slot_is_stale
from openminion.modules.a2a.storage.memory import MemoryStateStore

_OLD = "2000-01-01T00:00:00+00:00"
_SCOPE = "call:agent.echo:echo"
_KEY = "k-1"


def test_fresh_is_not_stale() -> None:
    from openminion.modules.a2a.models import iso_now

    assert idempotency_slot_is_stale(iso_now(), stale_after_sec=300) is False


def test_old_is_stale() -> None:
    assert idempotency_slot_is_stale(_OLD, stale_after_sec=300) is True


def test_unparseable_is_stale() -> None:
    assert idempotency_slot_is_stale("not-a-date", stale_after_sec=300) is True


@pytest.fixture
def memory_store() -> MemoryStateStore:
    return MemoryStateStore()


def test_memory_store_fresh_in_progress_is_not_reclaimed(
    memory_store: MemoryStateStore,
) -> None:
    reserved, _ = memory_store.reserve_idempotency(_KEY, _SCOPE)
    assert reserved is True
    reserved2, existing = memory_store.reserve_idempotency(
        _KEY, _SCOPE, stale_reclaim_after_sec=300
    )
    assert reserved2 is False
    assert existing.status == A2A_IDEMPOTENCY_STATUS_IN_PROGRESS


def test_memory_store_stale_in_progress_is_reclaimed(
    memory_store: MemoryStateStore,
) -> None:
    memory_store.reserve_idempotency(_KEY, _SCOPE)
    row = memory_store._idempotency[(_SCOPE, _KEY)]
    memory_store._idempotency[(_SCOPE, _KEY)] = replace(row, updated_at=_OLD)
    reserved, rec = memory_store.reserve_idempotency(
        _KEY, _SCOPE, stale_reclaim_after_sec=300
    )
    assert reserved is True
    assert rec.status == A2A_IDEMPOTENCY_STATUS_IN_PROGRESS
    assert rec.updated_at != _OLD


def test_memory_store_terminal_slot_is_never_reclaimed(
    memory_store: MemoryStateStore,
) -> None:
    memory_store.reserve_idempotency(_KEY, _SCOPE)
    memory_store.set_idempotency_result(
        _KEY, _SCOPE, A2A_IDEMPOTENCY_STATUS_SUCCESS, result_inline={"ok": True}
    )
    row = memory_store._idempotency[(_SCOPE, _KEY)]
    memory_store._idempotency[(_SCOPE, _KEY)] = replace(row, updated_at=_OLD)
    reserved, existing = memory_store.reserve_idempotency(
        _KEY, _SCOPE, stale_reclaim_after_sec=300
    )
    assert reserved is False
    assert existing.status == A2A_IDEMPOTENCY_STATUS_SUCCESS


def test_memory_store_no_ttl_means_no_reclaim(memory_store: MemoryStateStore) -> None:
    memory_store.reserve_idempotency(_KEY, _SCOPE)
    row = memory_store._idempotency[(_SCOPE, _KEY)]
    memory_store._idempotency[(_SCOPE, _KEY)] = replace(row, updated_at=_OLD)
    reserved, existing = memory_store.reserve_idempotency(_KEY, _SCOPE)
    assert reserved is False
    assert existing.status == A2A_IDEMPOTENCY_STATUS_IN_PROGRESS


@pytest.fixture
def sqlite_store(tmp_path: Path) -> SQLiteStateStore:
    return SQLiteStateStore(tmp_path / "state.db")


def _backdate(store: SQLiteStateStore) -> None:
    store._record_store.execute_count(
        "UPDATE idempotency_keys SET updated_at = ? WHERE scope = ? AND key = ?",
        (_OLD, _SCOPE, _KEY),
    )


def test_sqlite_store_stale_in_progress_is_reclaimed(
    sqlite_store: SQLiteStateStore,
) -> None:
    sqlite_store.reserve_idempotency(_KEY, _SCOPE)
    _backdate(sqlite_store)
    reserved, rec = sqlite_store.reserve_idempotency(
        _KEY, _SCOPE, stale_reclaim_after_sec=300
    )
    assert reserved is True
    assert rec.status == A2A_IDEMPOTENCY_STATUS_IN_PROGRESS
    assert rec.updated_at != _OLD


def test_sqlite_store_fresh_in_progress_is_not_reclaimed(
    sqlite_store: SQLiteStateStore,
) -> None:
    sqlite_store.reserve_idempotency(_KEY, _SCOPE)
    reserved, existing = sqlite_store.reserve_idempotency(
        _KEY, _SCOPE, stale_reclaim_after_sec=300
    )
    assert reserved is False
    assert existing.status == A2A_IDEMPOTENCY_STATUS_IN_PROGRESS


def test_sqlite_store_terminal_slot_is_never_reclaimed(
    sqlite_store: SQLiteStateStore,
) -> None:
    sqlite_store.reserve_idempotency(_KEY, _SCOPE)
    sqlite_store.set_idempotency_result(
        _KEY, _SCOPE, A2A_IDEMPOTENCY_STATUS_SUCCESS, result_inline={"ok": True}
    )
    _backdate(sqlite_store)
    reserved, existing = sqlite_store.reserve_idempotency(
        _KEY, _SCOPE, stale_reclaim_after_sec=300
    )
    assert reserved is False
    assert existing.status == A2A_IDEMPOTENCY_STATUS_SUCCESS
