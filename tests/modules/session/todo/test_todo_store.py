from __future__ import annotations

import unittest

from openminion.modules.session.todo import (
    InMemoryTodoStore,
    InvalidTodoIndexError,
    InvalidTodoStatusError,
    Todo,
    TodoEmptyError,
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


class RoundTripTests(unittest.TestCase):
    def test_set_then_get_returns_todo_with_items(self) -> None:
        store = InMemoryTodoStore()
        todo = store.set_plan("sess-a", ["Read config", "Edit handler"])
        self.assertIsInstance(todo, Todo)
        self.assertEqual(todo.session_id, "sess-a")
        self.assertEqual(len(todo.items), 2)
        self.assertEqual(todo.items[0].text, "Read config")
        self.assertEqual(todo.items[0].status, STATUS_TODO)
        self.assertEqual(todo.items[0].index, 0)
        self.assertEqual(todo.items[1].index, 1)

        fetched = store.get_plan("sess-a")
        self.assertEqual(fetched, todo)

    def test_get_unknown_session_returns_none(self) -> None:
        store = InMemoryTodoStore()
        self.assertIsNone(store.get_plan("sess-missing"))

    def test_clear_drops_plan(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["item"])
        store.clear_plan("sess-a")
        self.assertIsNone(store.get_plan("sess-a"))

    def test_clear_unknown_session_is_noop(self) -> None:
        store = InMemoryTodoStore()
        store.clear_plan("sess-never-existed")
        self.assertEqual(store.session_count(), 0)

    def test_summary_string(self) -> None:
        store = InMemoryTodoStore()
        todo = store.set_plan("sess-a", ["a", "b", "c"])
        self.assertEqual(todo.summary(), "0/3 done, 0 in progress")
        store.update_item_status("sess-a", 1, STATUS_IN_PROGRESS)
        store.update_item_status("sess-a", 0, STATUS_DONE)
        self.assertEqual(store.get_plan("sess-a").summary(), "1/3 done, 1 in progress")


class UpdateStatusTests(unittest.TestCase):
    def test_update_to_each_valid_status(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["item"])
        for status in (STATUS_IN_PROGRESS, STATUS_DONE, STATUS_BLOCKED, STATUS_TODO):
            item = store.update_item_status("sess-a", 0, status)
            self.assertEqual(item.status, status)

    def test_update_advances_updated_at(self) -> None:
        clock = _FakeClock(start=100.0)
        store = InMemoryTodoStore(clock=clock)
        store.set_plan("sess-a", ["item"])
        clock.advance(5.0)
        item = store.update_item_status("sess-a", 0, STATUS_DONE)
        self.assertEqual(item.updated_at, 105.0)
        self.assertEqual(item.created_at, 100.0)

    def test_update_invalid_status_raises(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["item"])
        with self.assertRaises(InvalidTodoStatusError) as ctx:
            store.update_item_status("sess-a", 0, "frobnicate")  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.code, "INVALID_PLAN_STATUS")

    def test_update_out_of_range_raises(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["item"])
        with self.assertRaises(InvalidTodoIndexError) as ctx:
            store.update_item_status("sess-a", 5, STATUS_DONE)
        self.assertEqual(ctx.exception.code, "INVALID_PLAN_INDEX")

    def test_update_with_no_plan_raises_todo_empty(self) -> None:
        store = InMemoryTodoStore()
        with self.assertRaises(TodoEmptyError) as ctx:
            store.update_item_status("sess-never-set", 0, STATUS_DONE)
        self.assertEqual(ctx.exception.code, "PLAN_EMPTY")


class AddItemTests(unittest.TestCase):
    def test_append_with_default_position(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["first", "second"])
        added = store.add_item("sess-a", "third")
        self.assertEqual(added.index, 2)
        self.assertEqual(added.text, "third")
        plan = store.get_plan("sess-a")
        self.assertEqual(
            [item.text for item in plan.items], ["first", "second", "third"]
        )

    def test_insert_at_position_renumbers_indices(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["a", "c"])
        store.add_item("sess-a", "b", position=1)
        plan = store.get_plan("sess-a")
        self.assertEqual([item.text for item in plan.items], ["a", "b", "c"])
        self.assertEqual([item.index for item in plan.items], [0, 1, 2])

    def test_position_beyond_end_appends(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["a"])
        store.add_item("sess-a", "b", position=99)
        plan = store.get_plan("sess-a")
        self.assertEqual([item.text for item in plan.items], ["a", "b"])

    def test_negative_position_below_minus_one_raises(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["a"])
        with self.assertRaises(InvalidTodoIndexError) as ctx:
            store.add_item("sess-a", "x", position=-5)
        self.assertEqual(ctx.exception.code, "INVALID_PLAN_INDEX")

    def test_add_without_plan_raises_todo_empty(self) -> None:
        store = InMemoryTodoStore()
        with self.assertRaises(TodoEmptyError) as ctx:
            store.add_item("sess-never-set", "x")
        self.assertEqual(ctx.exception.code, "PLAN_EMPTY")


class CapacityTests(unittest.TestCase):
    def test_set_plan_exceeding_item_cap_raises(self) -> None:
        store = InMemoryTodoStore(max_items_per_plan=3)
        with self.assertRaises(InvalidTodoIndexError):
            store.set_plan("sess-a", ["a", "b", "c", "d"])

    def test_add_item_when_at_item_cap_raises(self) -> None:
        store = InMemoryTodoStore(max_items_per_plan=2)
        store.set_plan("sess-a", ["a", "b"])
        with self.assertRaises(InvalidTodoIndexError):
            store.add_item("sess-a", "c")

    def test_lru_evicts_oldest_session_when_at_session_cap(self) -> None:
        store = InMemoryTodoStore(max_sessions=2)
        store.set_plan("sess-a", ["a"])
        store.set_plan("sess-b", ["b"])
        store.get_plan("sess-a")
        store.set_plan("sess-c", ["c"])
        self.assertEqual(store.session_count(), 2)
        self.assertIsNone(store.get_plan("sess-b"))
        self.assertIsNotNone(store.get_plan("sess-a"))
        self.assertIsNotNone(store.get_plan("sess-c"))

    def test_max_sessions_below_one_rejected_in_constructor(self) -> None:
        with self.assertRaises(ValueError):
            InMemoryTodoStore(max_sessions=0)

    def test_max_items_below_one_rejected_in_constructor(self) -> None:
        with self.assertRaises(ValueError):
            InMemoryTodoStore(max_items_per_plan=0)

    def test_default_caps_are_documented_values(self) -> None:
        # If these defaults change, the constants module documentation must
        # update alongside; this test pins the contract.
        store = InMemoryTodoStore()
        self.assertEqual(store._max_sessions, 100)
        self.assertEqual(store._max_items_per_plan, DEFAULT_MAX_ITEMS_PER_PLAN)


class SessionIsolationTests(unittest.TestCase):
    def test_two_sessions_do_not_share_state(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["a-1", "a-2"])
        store.set_plan("sess-b", ["b-1"])

        plan_a = store.get_plan("sess-a")
        plan_b = store.get_plan("sess-b")

        self.assertEqual(len(plan_a.items), 2)
        self.assertEqual(len(plan_b.items), 1)
        self.assertEqual(plan_a.items[0].text, "a-1")
        self.assertEqual(plan_b.items[0].text, "b-1")

    def test_clear_one_session_leaves_others_intact(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["a"])
        store.set_plan("sess-b", ["b"])
        store.clear_plan("sess-a")
        self.assertIsNone(store.get_plan("sess-a"))
        self.assertIsNotNone(store.get_plan("sess-b"))

    def test_update_in_one_session_does_not_affect_another(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["x"])
        store.set_plan("sess-b", ["y"])
        store.update_item_status("sess-a", 0, STATUS_DONE)
        plan_b = store.get_plan("sess-b")
        self.assertEqual(plan_b.items[0].status, STATUS_TODO)


class EvictionLifecycleTests(unittest.TestCase):
    def test_evict_drops_plan(self) -> None:
        store = InMemoryTodoStore()
        store.set_plan("sess-a", ["x"])
        store.evict("sess-a")
        self.assertIsNone(store.get_plan("sess-a"))

    def test_evict_unknown_session_is_noop(self) -> None:
        store = InMemoryTodoStore()
        store.evict("sess-never-existed")
        self.assertEqual(store.session_count(), 0)
