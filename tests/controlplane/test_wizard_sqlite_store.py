from __future__ import annotations

import asyncio
import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import pytest

from openminion.modules.controlplane.wizard.store import (
    InMemoryWizardStore,
    SqliteWizardStore,
    StoreFactory,
    WizardSession,
    WizardState,
    _STORE_REGISTRY,
    get_wizard_store,
    register_store,
)


def _session_shape(session: WizardSession) -> Dict[str, Any]:
    data = asdict(session)
    for key in ("wizard_id", "created_at", "updated_at", "timeout_at"):
        data.pop(key, None)
    # state is an enum; asdict preserves it
    return data


@pytest.fixture()
def sqlite_path(tmp_path: Path) -> Path:
    return tmp_path / "wizard.db"


@pytest.fixture()
def memory_store() -> InMemoryWizardStore:
    return InMemoryWizardStore()


@pytest.fixture()
def sqlite_store(sqlite_path: Path) -> SqliteWizardStore:
    store = SqliteWizardStore(sqlite_path)
    yield store
    asyncio.get_event_loop().run_until_complete(store.close()) if False else None
    # close synchronously via run for cleanup safety
    try:
        asyncio.run(store.close())
    except RuntimeError:
        if not store._closed:
            store._conn.close()
            store._closed = True


@pytest.mark.asyncio
async def test_create_session_parity(
    memory_store: InMemoryWizardStore, sqlite_store: SqliteWizardStore
) -> None:
    mem = await memory_store.create_session(
        command_name="test.create",
        step=1,
        total_steps=3,
        user_key="user-A",
        chat_key="chat-A",
        session_id="sess-A",
    )
    sql = await sqlite_store.create_session(
        command_name="test.create",
        step=1,
        total_steps=3,
        user_key="user-A",
        chat_key="chat-A",
        session_id="sess-A",
    )

    mem_get = await memory_store.get_session(mem.wizard_id)
    sql_get = await sqlite_store.get_session(sql.wizard_id)
    assert mem_get is not None
    assert sql_get is not None

    assert _session_shape(mem_get) == _session_shape(sql_get)


@pytest.mark.asyncio
async def test_save_and_get_parity(
    memory_store: InMemoryWizardStore, sqlite_store: SqliteWizardStore
) -> None:
    mem = await memory_store.create_session("cmd.parity", 1, 4, "u", "c", "s")
    mem.session_data = {"answers": ["one", "two"], "extra": {"k": 1}}
    mem.draft_result = {"foo": "bar"}
    await memory_store.save_session(mem)

    sql = await sqlite_store.create_session("cmd.parity", 1, 4, "u", "c", "s")
    sql.session_data = {"answers": ["one", "two"], "extra": {"k": 1}}
    sql.draft_result = {"foo": "bar"}
    await sqlite_store.save_session(sql)

    mem_back = await memory_store.get_session(mem.wizard_id)
    sql_back = await sqlite_store.get_session(sql.wizard_id)
    assert _session_shape(mem_back) == _session_shape(sql_back)


@pytest.mark.asyncio
async def test_update_session_state_parity(
    memory_store: InMemoryWizardStore, sqlite_store: SqliteWizardStore
) -> None:
    mem = await memory_store.create_session("cmd.update", 1, 2, "u", "c")
    sql = await sqlite_store.create_session("cmd.update", 1, 2, "u", "c")

    mem_updated = await memory_store.update_session_state(
        mem.wizard_id, WizardState.COMPLETED, step=2
    )
    sql_updated = await sqlite_store.update_session_state(
        sql.wizard_id, WizardState.COMPLETED, step=2
    )
    assert mem_updated is not None
    assert sql_updated is not None
    assert mem_updated.state == sql_updated.state == WizardState.COMPLETED
    assert mem_updated.step == sql_updated.step == 2


@pytest.mark.asyncio
async def test_get_active_sessions_for_user_and_chat_parity(
    memory_store: InMemoryWizardStore, sqlite_store: SqliteWizardStore
) -> None:
    # Two sessions for user-1, one for user-2; chat-1 has two, chat-2 has one.
    for store in (memory_store, sqlite_store):
        await store.create_session("cmd.a", 1, 2, "user-1", "chat-1")
        await store.create_session("cmd.b", 1, 2, "user-1", "chat-2")
        await store.create_session("cmd.c", 1, 2, "user-2", "chat-1")

    mem_user1 = await memory_store.get_active_sessions_for_user("user-1")
    sql_user1 = await sqlite_store.get_active_sessions_for_user("user-1")
    assert len(mem_user1) == len(sql_user1) == 2

    mem_chat1 = await memory_store.get_active_sessions_for_chat("chat-1")
    sql_chat1 = await sqlite_store.get_active_sessions_for_chat("chat-1")
    assert len(mem_chat1) == len(sql_chat1) == 2


@pytest.mark.asyncio
async def test_timeout_session_parity(
    memory_store: InMemoryWizardStore, sqlite_store: SqliteWizardStore
) -> None:
    mem = await memory_store.create_session("cmd.t", 1, 2, "u", "c")
    sql = await sqlite_store.create_session("cmd.t", 1, 2, "u", "c")

    assert await memory_store.timeout_session(mem.wizard_id) is True
    assert await sqlite_store.timeout_session(sql.wizard_id) is True

    mem_back = await memory_store._get_raw_session(mem.wizard_id)
    sql_back = await sqlite_store._get_raw_session(sql.wizard_id)
    assert mem_back.state == sql_back.state == WizardState.TIMEOUT


@pytest.mark.asyncio
async def test_delete_session_parity(
    memory_store: InMemoryWizardStore, sqlite_store: SqliteWizardStore
) -> None:
    mem = await memory_store.create_session("cmd.d", 1, 1, "u", "c")
    sql = await sqlite_store.create_session("cmd.d", 1, 1, "u", "c")

    assert await memory_store.delete_session(mem.wizard_id) is True
    assert await sqlite_store.delete_session(sql.wizard_id) is True
    assert await memory_store.delete_session("missing") is False
    assert await sqlite_store.delete_session("missing") is False
    assert await memory_store.get_session(mem.wizard_id) is None
    assert await sqlite_store.get_session(sql.wizard_id) is None


@pytest.mark.asyncio
async def test_restart_survival_full_round_trip(sqlite_path: Path) -> None:
    store = SqliteWizardStore(sqlite_path)
    session = await store.create_session(
        command_name="skill.learn",
        step=2,
        total_steps=4,
        user_key="user-survive",
        chat_key="chat-survive",
        session_id="sess-survive",
        timeout_duration=timedelta(hours=2),
    )
    session.session_data = {
        "answers": [{"q": "name", "a": "alice"}, {"q": "age", "a": 30}],
        "nested": {"k": [1, 2, 3]},
    }
    session.draft_result = {"completed_steps": [1], "trace": "abc"}
    await store.save_session(session)

    captured = await store.get_session(session.wizard_id)
    assert captured is not None

    await store.close()

    store2 = SqliteWizardStore(sqlite_path)
    restored = await store2.get_session(session.wizard_id)
    assert restored is not None

    assert restored.wizard_id == session.wizard_id
    assert restored.command_name == "skill.learn"
    assert restored.state == WizardState.ACTIVE
    assert restored.step == 2
    assert restored.total_steps == 4
    assert restored.session_data == session.session_data
    assert restored.draft_result == session.draft_result
    assert restored.user_key == "user-survive"
    assert restored.chat_key == "chat-survive"
    assert restored.session_id == "sess-survive"
    assert restored.created_at.tzinfo is not None
    assert restored.updated_at.tzinfo is not None
    assert restored.timeout_at is not None and restored.timeout_at.tzinfo is not None
    assert abs((restored.created_at - captured.created_at).total_seconds()) < 1e-3
    assert abs((restored.timeout_at - captured.timeout_at).total_seconds()) < 1e-3

    await store2.close()


@pytest.mark.asyncio
async def test_expire_overdue_flips_only_active_past_due(
    sqlite_store: SqliteWizardStore,
) -> None:
    s1 = await sqlite_store.create_session("cmd.x", 1, 2, "u1", "c1")
    s2 = await sqlite_store.create_session("cmd.y", 1, 2, "u2", "c2")
    s3 = await sqlite_store.create_session("cmd.z", 1, 2, "u3", "c3")

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    s1.timeout_at = past
    s2.timeout_at = past
    await sqlite_store._save_raw_session(s1)
    await sqlite_store._save_raw_session(s2)

    flipped = await sqlite_store.expire_overdue()
    assert flipped == 2

    s1_back = await sqlite_store._get_raw_session(s1.wizard_id)
    s2_back = await sqlite_store._get_raw_session(s2.wizard_id)
    s3_back = await sqlite_store._get_raw_session(s3.wizard_id)
    assert s1_back.state == WizardState.TIMEOUT
    assert s2_back.state == WizardState.TIMEOUT
    assert s3_back.state == WizardState.ACTIVE


@pytest.mark.asyncio
async def test_expire_overdue_idempotent(sqlite_store: SqliteWizardStore) -> None:
    s1 = await sqlite_store.create_session("cmd.x", 1, 1, "u", "c")
    s1.timeout_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    await sqlite_store._save_raw_session(s1)

    first = await sqlite_store.expire_overdue()
    second = await sqlite_store.expire_overdue()
    assert first == 1
    assert second == 0


@pytest.mark.asyncio
async def test_state_enum_round_trip_all_values(
    sqlite_store: SqliteWizardStore,
) -> None:
    for state in WizardState:
        session = await sqlite_store.create_session(
            command_name=f"cmd.{state.value}", step=1, total_steps=1
        )
        session.state = state
        await sqlite_store._save_raw_session(session)

        back = await sqlite_store._get_raw_session(session.wizard_id)
        assert back is not None
        assert back.state == state, (
            f"State enum failed to round-trip for {state}: got {back.state}"
        )


def test_concurrent_creates_and_updates_no_database_locked(
    sqlite_path: Path,
) -> None:

    store = SqliteWizardStore(sqlite_path)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    created_ids: list[str] = []
    lock = threading.Lock()

    def worker(prefix: str) -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            barrier.wait()
            for i in range(20):
                session = loop.run_until_complete(
                    store.create_session(
                        command_name=f"cmd.{prefix}",
                        step=1,
                        total_steps=2,
                        user_key=f"user-{prefix}",
                        chat_key=f"chat-{prefix}",
                    )
                )
                with lock:
                    created_ids.append(session.wizard_id)
                loop.run_until_complete(
                    store.update_session_state(
                        session.wizard_id, WizardState.COMPLETED, step=2
                    )
                )
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)
        finally:
            loop.close()

    threads = [
        threading.Thread(target=worker, args=("A",)),
        threading.Thread(target=worker, args=("B",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == [], f"thread errors: {errors!r}"
    assert len(created_ids) == 40

    # Final state: every session should be COMPLETED.
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        for wid in created_ids:
            session = loop.run_until_complete(store._get_raw_session(wid))
            assert session is not None
            assert session.state == WizardState.COMPLETED
        loop.run_until_complete(store.close())
    finally:
        loop.close()


# Registry: explicit registration and factory wiring.


@pytest.mark.asyncio
async def test_register_store_explicit_sqlite(sqlite_path: Path) -> None:
    # Snapshot + clear the singleton registry so the test is hermetic.
    saved = dict(_STORE_REGISTRY)
    _STORE_REGISTRY.clear()
    try:
        sqlite_store = SqliteWizardStore(sqlite_path)
        register_store("sqlite", sqlite_store)
        retrieved = await get_wizard_store("sqlite")
        assert retrieved is sqlite_store

        # CPD-04 follow-on: when sqlite is registered, the default
        # ``in_memory`` lookup prefers it so dispatcher/wizard executor
        # callers automatically pick up the durable store.
        preferred = await get_wizard_store("in_memory")
        assert preferred is sqlite_store
        await sqlite_store.close()
    finally:
        _STORE_REGISTRY.clear()
        _STORE_REGISTRY.update(saved)


@pytest.mark.asyncio
async def test_in_memory_default_when_sqlite_not_registered() -> None:
    saved = dict(_STORE_REGISTRY)
    _STORE_REGISTRY.clear()
    try:
        store = await get_wizard_store("in_memory")
        assert isinstance(store, InMemoryWizardStore)
    finally:
        _STORE_REGISTRY.clear()
        _STORE_REGISTRY.update(saved)


def test_factory_sqlite_requires_path() -> None:
    with pytest.raises(ValueError):
        StoreFactory.create_store("sqlite")


def test_factory_creates_sqlite(sqlite_path: Path) -> None:
    store = StoreFactory.create_store("sqlite", sqlite_path=sqlite_path)
    assert isinstance(store, SqliteWizardStore)
    asyncio.run(store.close())


def test_inmemory_expire_overdue_is_no_op_when_clean() -> None:
    store = InMemoryWizardStore()
    flipped = asyncio.run(store.expire_overdue())
    assert flipped == 0
