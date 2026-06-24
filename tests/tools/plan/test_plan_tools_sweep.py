from __future__ import annotations

import unittest
from types import SimpleNamespace

from openminion.modules.session.todo import InMemoryTodoStore as InMemoryPlanStore
from openminion.modules.session.todo.constants import (
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_TODO,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.todo import plugin as plan_plugin
from openminion.tools.todo import register
from openminion.tools.todo.plugin import (
    _h_add,
    _h_clear,
    _h_complete,
    _h_list,
    _h_set,
    _h_update,
    _reset_store_for_tests,
)


def _ctx(session_id: str = "sess-test") -> object:
    return SimpleNamespace(session_id=session_id)


class RegistryExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_store_for_tests()
        self.registry = ToolRegistry([])
        register(self.registry)

    def _resolve_handler(self, name: str):
        spec = self.registry.list()[name]
        return spec.handler

    def test_each_tool_handler_callable_from_registry(self) -> None:
        for name in (
            "plan.set",
            "plan.add",
            "plan.update",
            "plan.complete",
            "plan.list",
            "plan.clear",
        ):
            handler = self._resolve_handler(name)
            self.assertTrue(callable(handler), f"{name} handler is not callable")

    def test_set_via_registry_returns_envelope_shape(self) -> None:
        handler = self._resolve_handler("plan.set")
        result = handler({"items": ["a", "b"]}, _ctx())
        self.assertIn("plan", result)
        self.assertIn("message", result)
        self.assertEqual(len(result["plan"]["items"]), 2)


class CrossSessionInterferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_store_for_tests()

    def test_update_in_one_session_leaves_other_untouched(self) -> None:
        _h_set({"items": ["x", "y"]}, _ctx("sess-a"))
        _h_set({"items": ["x", "y"]}, _ctx("sess-b"))

        _h_update({"index": 0, "status": "done"}, _ctx("sess-a"))

        plan_a = _h_list({}, _ctx("sess-a"))["plan"]
        plan_b = _h_list({}, _ctx("sess-b"))["plan"]

        self.assertEqual(plan_a["items"][0]["status"], STATUS_DONE)
        self.assertEqual(plan_b["items"][0]["status"], STATUS_TODO)

    def test_clear_in_one_session_leaves_other_intact(self) -> None:
        _h_set({"items": ["a"]}, _ctx("sess-a"))
        _h_set({"items": ["b"]}, _ctx("sess-b"))

        _h_clear({}, _ctx("sess-a"))

        self.assertEqual(_h_list({}, _ctx("sess-a"))["plan"]["items"], [])
        self.assertEqual(len(_h_list({}, _ctx("sess-b"))["plan"]["items"]), 1)

    def test_add_in_one_session_does_not_change_other_count(self) -> None:
        _h_set({"items": ["x"]}, _ctx("sess-a"))
        _h_set({"items": ["x"]}, _ctx("sess-b"))

        _h_add({"item": "extra"}, _ctx("sess-a"))

        self.assertEqual(len(_h_list({}, _ctx("sess-a"))["plan"]["items"]), 2)
        self.assertEqual(len(_h_list({}, _ctx("sess-b"))["plan"]["items"]), 1)

    def test_complete_in_one_session_leaves_other_in_progress_count_untouched(
        self,
    ) -> None:
        _h_set({"items": ["x", "y"]}, _ctx("sess-a"))
        _h_set({"items": ["x", "y"]}, _ctx("sess-b"))
        _h_update({"index": 0, "status": "in_progress"}, _ctx("sess-a"))
        _h_update({"index": 0, "status": "in_progress"}, _ctx("sess-b"))

        _h_complete({"index": 0}, _ctx("sess-a"))

        self.assertEqual(
            _h_list({}, _ctx("sess-a"))["plan"]["items"][0]["status"], STATUS_DONE
        )
        self.assertEqual(
            _h_list({}, _ctx("sess-b"))["plan"]["items"][0]["status"],
            STATUS_IN_PROGRESS,
        )


class PostClearBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_store_for_tests()
        _h_set({"items": ["a", "b"]}, _ctx())
        _h_clear({}, _ctx())

    def test_post_clear_add_raises_plan_empty(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_add({"item": "z"}, _ctx())
        self.assertEqual(ctx.exception.code, "PLAN_EMPTY")

    def test_post_clear_update_raises_plan_empty(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_update({"index": 0, "status": "done"}, _ctx())
        self.assertEqual(ctx.exception.code, "PLAN_EMPTY")

    def test_post_clear_complete_raises_plan_empty(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_complete({"index": 0}, _ctx())
        self.assertEqual(ctx.exception.code, "PLAN_EMPTY")

    def test_post_clear_list_returns_empty_envelope_without_error(self) -> None:
        result = _h_list({}, _ctx())
        self.assertEqual(result["plan"]["items"], [])
        self.assertEqual(result["plan"]["summary"], "0/0 done, 0 in progress")
        self.assertIn("message", result)

    def test_post_clear_set_reinitializes_with_fresh_indices(self) -> None:
        result = _h_set({"items": ["fresh-1", "fresh-2", "fresh-3"]}, _ctx())
        plan = result["plan"]
        self.assertEqual([item["index"] for item in plan["items"]], [0, 1, 2])
        self.assertEqual([item["status"] for item in plan["items"]], [STATUS_TODO] * 3)


class SummaryFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_store_for_tests()

    def test_empty_plan_summary(self) -> None:
        result = _h_set({"items": []}, _ctx())
        self.assertEqual(result["plan"]["summary"], "0/0 done, 0 in progress")

    def test_all_todo_summary(self) -> None:
        result = _h_set({"items": ["a", "b", "c"]}, _ctx())
        self.assertEqual(result["plan"]["summary"], "0/3 done, 0 in progress")

    def test_mixed_status_summary(self) -> None:
        _h_set({"items": ["a", "b", "c", "d"]}, _ctx())
        _h_update({"index": 0, "status": STATUS_DONE}, _ctx())
        _h_update({"index": 1, "status": STATUS_IN_PROGRESS}, _ctx())
        _h_update({"index": 2, "status": STATUS_BLOCKED}, _ctx())
        result = _h_list({}, _ctx())
        self.assertEqual(result["plan"]["summary"], "1/4 done, 1 in progress")

    def test_complete_advances_done_count_and_clears_in_progress(self) -> None:
        _h_set({"items": ["a"]}, _ctx())
        _h_update({"index": 0, "status": STATUS_IN_PROGRESS}, _ctx())
        before = _h_list({}, _ctx())
        self.assertEqual(before["plan"]["summary"], "0/1 done, 1 in progress")
        _h_complete({"index": 0}, _ctx())
        after = _h_list({}, _ctx())
        self.assertEqual(after["plan"]["summary"], "1/1 done, 0 in progress")


class PositionAddTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_store_for_tests()
        _h_set({"items": ["a", "c"]}, _ctx())

    def test_add_at_position_zero_inserts_at_front(self) -> None:
        result = _h_add({"item": "z", "position": 0}, _ctx())
        texts = [item["text"] for item in result["plan"]["items"]]
        self.assertEqual(texts, ["z", "a", "c"])
        self.assertEqual([item["index"] for item in result["plan"]["items"]], [0, 1, 2])

    def test_add_at_position_one_inserts_between(self) -> None:
        result = _h_add({"item": "b", "position": 1}, _ctx())
        texts = [item["text"] for item in result["plan"]["items"]]
        self.assertEqual(texts, ["a", "b", "c"])

    def test_add_with_position_beyond_end_appends(self) -> None:
        result = _h_add({"item": "z", "position": 99}, _ctx())
        texts = [item["text"] for item in result["plan"]["items"]]
        self.assertEqual(texts, ["a", "c", "z"])

    def test_add_with_default_position_appends(self) -> None:
        result = _h_add({"item": "z"}, _ctx())
        texts = [item["text"] for item in result["plan"]["items"]]
        self.assertEqual(texts, ["a", "c", "z"])

    def test_add_with_position_below_minus_one_raises_invalid_plan_index(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_add({"item": "z", "position": -5}, _ctx())
        self.assertEqual(ctx.exception.code, "INVALID_PLAN_INDEX")


class ResetAndCapacityTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_store_for_tests()

    def test_set_replaces_prior_plan(self) -> None:
        _h_set({"items": ["old-1", "old-2"]}, _ctx())
        _h_update({"index": 0, "status": STATUS_DONE}, _ctx())
        result = _h_set({"items": ["new-1"]}, _ctx())
        texts = [item["text"] for item in result["plan"]["items"]]
        statuses = [item["status"] for item in result["plan"]["items"]]
        self.assertEqual(texts, ["new-1"])
        self.assertEqual(statuses, [STATUS_TODO])

    def test_complete_out_of_range_raises_invalid_plan_index(self) -> None:
        _h_set({"items": ["only"]}, _ctx())
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_complete({"index": 99}, _ctx())
        self.assertEqual(ctx.exception.code, "INVALID_PLAN_INDEX")

    def test_set_with_too_many_items_raises_invalid_plan_index(self) -> None:
        plan_plugin._plan_store = InMemoryPlanStore(max_items_per_plan=2)
        try:
            with self.assertRaises(ToolRuntimeError) as ctx:
                _h_set({"items": ["a", "b", "c"]}, _ctx())
            self.assertEqual(ctx.exception.code, "INVALID_PLAN_INDEX")
        finally:
            _reset_store_for_tests()

    def test_add_at_item_cap_raises_invalid_plan_index(self) -> None:
        plan_plugin._plan_store = InMemoryPlanStore(max_items_per_plan=2)
        try:
            _h_set({"items": ["a", "b"]}, _ctx())
            with self.assertRaises(ToolRuntimeError) as ctx:
                _h_add({"item": "c"}, _ctx())
            self.assertEqual(ctx.exception.code, "INVALID_PLAN_INDEX")
        finally:
            _reset_store_for_tests()


class UpdateStatusCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_store_for_tests()
        _h_set({"items": ["item"]}, _ctx())

    def test_status_todo(self) -> None:
        _h_update({"index": 0, "status": STATUS_TODO}, _ctx())
        self.assertEqual(_h_list({}, _ctx())["plan"]["items"][0]["status"], STATUS_TODO)

    def test_status_in_progress(self) -> None:
        _h_update({"index": 0, "status": STATUS_IN_PROGRESS}, _ctx())
        self.assertEqual(
            _h_list({}, _ctx())["plan"]["items"][0]["status"], STATUS_IN_PROGRESS
        )

    def test_status_done(self) -> None:
        _h_update({"index": 0, "status": STATUS_DONE}, _ctx())
        self.assertEqual(_h_list({}, _ctx())["plan"]["items"][0]["status"], STATUS_DONE)

    def test_status_blocked(self) -> None:
        _h_update({"index": 0, "status": STATUS_BLOCKED}, _ctx())
        self.assertEqual(
            _h_list({}, _ctx())["plan"]["items"][0]["status"], STATUS_BLOCKED
        )


class DefaultSessionFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_store_for_tests()

    def test_handler_with_no_session_id_uses_default_bucket(self) -> None:
        ctx_no_session = SimpleNamespace()  # no `session_id` attribute
        result = _h_set({"items": ["x"]}, ctx_no_session)
        self.assertEqual(result["plan"]["session_id"], "_default")

    def test_handler_with_empty_session_id_uses_default_bucket(self) -> None:
        ctx_empty = SimpleNamespace(session_id="")
        result = _h_set({"items": ["x"]}, ctx_empty)
        self.assertEqual(result["plan"]["session_id"], "_default")

    def test_handler_with_whitespace_session_id_uses_default_bucket(self) -> None:
        ctx_ws = SimpleNamespace(session_id="   ")
        result = _h_set({"items": ["x"]}, ctx_ws)
        self.assertEqual(result["plan"]["session_id"], "_default")

    def test_explicit_default_session_id_collides_with_fallback(self) -> None:
        _h_set({"items": ["from-explicit"]}, _ctx("_default"))
        ctx_no_session = SimpleNamespace()
        result = _h_list({}, ctx_no_session)
        self.assertEqual(result["plan"]["items"][0]["text"], "from-explicit")
