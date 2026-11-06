from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterator

import pytest

from openminion.modules.a2a.constants import (
    A2A_IDEMPOTENCY_STATUS_IN_PROGRESS,
    A2A_IDEMPOTENCY_STATUS_SUCCESS,
)
from openminion.modules.a2a.storage import SQLiteStateStore
from openminion.modules.a2a.storage.base import StateStore, idempotency_slot_is_stale
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


@pytest.fixture(params=["memory", "sqlite"])
def state_store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[StateStore]:
    store = (
        MemoryStateStore()
        if request.param == "memory"
        else SQLiteStateStore(tmp_path / "state.db")
    )
    try:
        yield store
    finally:
        store.close()


def test_reservation_completion_checks_generation_and_status(
    state_store: StateStore,
) -> None:
    reserved, reservation = state_store.reserve_idempotency(_KEY, _SCOPE)
    assert reserved and reservation is not None

    wrong_owner = replace(reservation, created_at=_OLD)
    assert not state_store.finish_idempotency(
        wrong_owner, A2A_IDEMPOTENCY_STATUS_SUCCESS
    )
    assert not state_store.finish_idempotency(
        replace(reservation, key="missing"), A2A_IDEMPOTENCY_STATUS_SUCCESS
    )
    reserved, existing = state_store.reserve_idempotency(_KEY, _SCOPE)
    assert not reserved and existing is not None
    assert existing.status == A2A_IDEMPOTENCY_STATUS_IN_PROGRESS

    assert state_store.finish_idempotency(
        reservation, A2A_IDEMPOTENCY_STATUS_SUCCESS, result_inline={"receipt": 1}
    )
    assert not state_store.finish_idempotency(
        reservation, A2A_IDEMPOTENCY_STATUS_SUCCESS, result_inline={"receipt": 2}
    )
    reserved, existing = state_store.reserve_idempotency(_KEY, _SCOPE)
    assert not reserved and existing is not None
    assert existing.status == A2A_IDEMPOTENCY_STATUS_SUCCESS
    assert existing.result_inline == {"receipt": 1}
