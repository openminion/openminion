from __future__ import annotations

import pytest

from openminion.modules.session.todo import (
    FileTodoStore,
    InMemoryTodoStore,
    InvalidTodoIndexError,
    InvalidTodoStatusError,
    Todo,
    TodoEmptyError,
    get_default_todo_store,
    resolve_default_todo_store_path,
    reset_default_todo_store_for_tests,
)
from openminion.modules.session.todo.constants import (
    DEFAULT_MAX_ITEMS_PER_PLAN,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_TODO,
)


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float = 1.0) -> None:
        self._t += seconds


class TestRoundTrip:
    def test_set_then_get_returns_todo_with_items(self) -> None:
        store = InMemoryTodoStore()
        todo = store.set_plan("sess-a", ["Read config", "Edit handler"])
        assert isinstance(todo, Todo)
        assert todo.session_id == "sess-a"
        assert len(todo.items) == 2
        assert todo.items[0].text == "Read config"
        assert todo.items[0].status == STATUS_TODO
        assert todo.items[0].index == 0
        assert todo.items[1].index == 1

        fetched = store.get_plan("sess-a")
        assert fetched == todo

    def test_get_unknown_session_returns_none(self) -> None:
        store = InMemoryTodoStore()
        assert store.get_plan("sess-missing") is None

    def test_clear_drops_plan(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["item"])
        store.clear_plan("sess-a")
        assert store.get_plan("sess-a") is None

    def test_clear_unknown_session_is_noop(self) -> None:
        store = InMemoryTodoStore()
        store.clear_plan("sess-never-existed")
        assert store.session_count() == 0

    def test_summary_string(self) -> None:
        store = InMemoryTodoStore()
        todo = store.set_plan("sess-a", ["a", "b", "c"])
        assert todo.summary() == "0/3 done, 0 in progress"
        store.update_item_status("sess-a", 1, STATUS_IN_PROGRESS)
        store.update_item_status("sess-a", 0, STATUS_DONE)
        assert store.get_plan("sess-a").summary() == "1/3 done, 1 in progress"


class TestUpdateStatus:
    def test_update_to_each_valid_status(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["item"])
        for status in (STATUS_IN_PROGRESS, STATUS_DONE, STATUS_BLOCKED, STATUS_TODO):
            item = store.update_item_status("sess-a", 0, status)
            assert item.status == status

    def test_update_advances_updated_at(self) -> None:
        clock = _FakeClock(start=100.0)
        store = InMemoryTodoStore(clock=clock)
        store.set_plan("sess-a", ["item"])
        clock.advance(5.0)
        item = store.update_item_status("sess-a", 0, STATUS_DONE)
        assert item.updated_at == 105.0
        assert item.created_at == 100.0

    def test_update_invalid_status_raises(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["item"])
        with pytest.raises(InvalidTodoStatusError) as exc_info:
            store.update_item_status("sess-a", 0, "frobnicate")  # type: ignore[arg-type]
        assert exc_info.value.code == "INVALID_PLAN_STATUS"

    def test_update_out_of_range_raises(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["item"])
        with pytest.raises(InvalidTodoIndexError) as exc_info:
            store.update_item_status("sess-a", 5, STATUS_DONE)
        assert exc_info.value.code == "INVALID_PLAN_INDEX"

    def test_update_with_no_plan_raises_todo_empty(self) -> None:
        store = InMemoryTodoStore()
        with pytest.raises(TodoEmptyError) as exc_info:
            store.update_item_status("sess-never-set", 0, STATUS_DONE)
        assert exc_info.value.code == "PLAN_EMPTY"


class TestAddItem:
    def test_append_with_default_position(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["first", "second"])
        added = store.add_item("sess-a", "third")
        assert added.index == 2
        assert added.text == "third"
        plan = store.get_plan("sess-a")
        assert [item.text for item in plan.items] == ["first", "second", "third"]

    def test_insert_at_position_renumbers_indices(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["a", "c"])
        store.add_item("sess-a", "b", position=1)
        plan = store.get_plan("sess-a")
        assert [item.text for item in plan.items] == ["a", "b", "c"]
        assert [item.index for item in plan.items] == [0, 1, 2]

    def test_position_beyond_end_appends(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["a"])
        store.add_item("sess-a", "b", position=99)
        plan = store.get_plan("sess-a")
        assert [item.text for item in plan.items] == ["a", "b"]

    def test_negative_position_below_minus_one_raises(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["a"])
        with pytest.raises(InvalidTodoIndexError) as exc_info:
            store.add_item("sess-a", "x", position=-5)
        assert exc_info.value.code == "INVALID_PLAN_INDEX"

    def test_add_without_plan_raises_todo_empty(self) -> None:
        store = InMemoryTodoStore()
        with pytest.raises(TodoEmptyError) as exc_info:
            store.add_item("sess-never-set", "x")
        assert exc_info.value.code == "PLAN_EMPTY"


class TestCapacity:
    def test_set_plan_exceeding_item_cap_raises(self) -> None:
        store = InMemoryTodoStore(max_items_per_plan=3)
        with pytest.raises(InvalidTodoIndexError):
            store.set_plan("sess-a", ["a", "b", "c", "d"])

    def test_add_item_when_at_item_cap_raises(self) -> None:
        store = InMemoryTodoStore(max_items_per_plan=2)
        store.set_plan("sess-a", ["a", "b"])
        with pytest.raises(InvalidTodoIndexError):
            store.add_item("sess-a", "c")

    def test_lru_evicts_oldest_session_when_at_session_cap(self) -> None:
        store = InMemoryTodoStore(max_sessions=2)
        store.set_plan("sess-a", ["a"])
        store.set_plan("sess-b", ["b"])
        store.get_plan("sess-a")
        store.set_plan("sess-c", ["c"])
        assert store.session_count() == 2
        assert store.get_plan("sess-b") is None
        assert store.get_plan("sess-a") is not None
        assert store.get_plan("sess-c") is not None

    def test_max_sessions_below_one_rejected_in_constructor(self) -> None:
        with pytest.raises(ValueError):
            InMemoryTodoStore(max_sessions=0)

    def test_max_items_below_one_rejected_in_constructor(self) -> None:
        with pytest.raises(ValueError):
            InMemoryTodoStore(max_items_per_plan=0)

    def test_default_caps_are_documented_values(self) -> None:
        # If these defaults change, the constants module documentation must
        # update alongside; this test pins the contract.
        store = InMemoryTodoStore()
        assert store._max_sessions == 100
        assert store._max_items_per_plan == DEFAULT_MAX_ITEMS_PER_PLAN


class TestSessionIsolation:
    def test_two_sessions_do_not_share_state(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["a-1", "a-2"])
        store.set_plan("sess-b", ["b-1"])

        plan_a = store.get_plan("sess-a")
        plan_b = store.get_plan("sess-b")

        assert len(plan_a.items) == 2
        assert len(plan_b.items) == 1
        assert plan_a.items[0].text == "a-1"
        assert plan_b.items[0].text == "b-1"

    def test_clear_one_session_leaves_others_intact(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["a"])
        store.set_plan("sess-b", ["b"])
        store.clear_plan("sess-a")
        assert store.get_plan("sess-a") is None
        assert store.get_plan("sess-b") is not None

    def test_update_in_one_session_does_not_affect_another(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["x"])
        store.set_plan("sess-b", ["y"])
        store.update_item_status("sess-a", 0, STATUS_DONE)
        plan_b = store.get_plan("sess-b")
        assert plan_b.items[0].status == STATUS_TODO


class TestEvictionLifecycle:
    def test_evict_drops_plan(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["x"])
        store.evict("sess-a")
        assert store.get_plan("sess-a") is None

    def test_evict_unknown_session_is_noop(self) -> None:
        store = InMemoryTodoStore()
        store.evict("sess-never-existed")
        assert store.session_count() == 0


class TestFileTodoStore:
    def test_reopens_plan_from_disk(self, tmp_path) -> None:
        path = tmp_path / "session" / "todo.json"
        store = FileTodoStore(path)
        store.set_plan("sess-a", ["map gap", "ship fix"])
        store.update_item_status("sess-a", 0, STATUS_DONE)

        reopened = FileTodoStore(path)
        plan = reopened.get_plan("sess-a")

        assert plan is not None
        assert [item.text for item in plan.items] == ["map gap", "ship fix"]
        assert [item.status for item in plan.items] == [STATUS_DONE, STATUS_TODO]
        assert plan.summary() == "1/2 done, 0 in progress"

    def test_clear_persists_to_disk(self, tmp_path) -> None:
        path = tmp_path / "todo.json"
        store = FileTodoStore(path)
        store.set_plan("sess-a", ["remove me"])

        store.clear_plan("sess-a")

        assert FileTodoStore(path).get_plan("sess-a") is None

    def test_loaded_invalid_status_defaults_to_todo(self, tmp_path) -> None:
        path = tmp_path / "todo.json"
        path.write_text(
            '{"version":1,"sessions":{"sess-a":{"items":[{"text":"x","status":"bad"}]}}}',
            encoding="utf-8",
        )

        plan = FileTodoStore(path).get_plan("sess-a")

        assert plan is not None
        assert plan.items[0].status == STATUS_TODO

    def test_default_path_uses_data_root(self, tmp_path) -> None:
        path = resolve_default_todo_store_path(
            home_root=tmp_path / "home",
            data_root=tmp_path / "data",
            env={},
        )

        assert path == tmp_path / "data" / "session" / "todo.json"

    def test_reset_default_store_for_tests_uses_fresh_memory_store(self) -> None:
        reset_default_todo_store_for_tests()

        store = get_default_todo_store()

        assert isinstance(store, InMemoryTodoStore)
        assert not isinstance(store, FileTodoStore)
