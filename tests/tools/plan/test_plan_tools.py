from __future__ import annotations

import unittest
from types import SimpleNamespace

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry
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


class RegistrationTests(unittest.TestCase):
    def test_all_six_plan_tools_register(self) -> None:
        registry = ToolRegistry([])
        register(registry)
        names = set(registry.list().keys())
        self.assertIn("plan.set", names)
        self.assertIn("plan.add", names)
        self.assertIn("plan.update", names)
        self.assertIn("plan.complete", names)
        self.assertIn("plan.list", names)
        self.assertIn("plan.clear", names)

    def test_plan_list_is_read_only_scope(self) -> None:
        registry = ToolRegistry([])
        register(registry)
        policy = registry.policy_for("plan.list")
        # `min_scope=READ_ONLY` means the resolved policy permits read-only callers.
        self.assertEqual(policy.tool_name, "plan.list")

    def test_mutators_are_write_safe_scope(self) -> None:
        registry = ToolRegistry([])
        register(registry)
        for mutator in (
            "plan.set",
            "plan.add",
            "plan.update",
            "plan.complete",
            "plan.clear",
        ):
            spec = registry.list().get(mutator)
            self.assertIsNotNone(spec, f"Tool {mutator!r} did not register")


class ResultShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_store_for_tests()

    def test_set_returns_plan_envelope_with_items(self) -> None:
        result = _h_set({"items": ["a", "b"]}, _ctx())
        self.assertIn("plan", result)
        self.assertIn("message", result)
        plan = result["plan"]
        self.assertEqual(plan["session_id"], "sess-test")
        self.assertEqual([item["text"] for item in plan["items"]], ["a", "b"])
        self.assertEqual([item["status"] for item in plan["items"]], ["todo", "todo"])
        self.assertEqual(plan["summary"], "0/2 done, 0 in progress")

    def test_add_returns_updated_plan(self) -> None:
        _h_set({"items": ["a"]}, _ctx())
        result = _h_add({"item": "b"}, _ctx())
        self.assertEqual([item["text"] for item in result["plan"]["items"]], ["a", "b"])

    def test_update_returns_plan_with_new_status(self) -> None:
        _h_set({"items": ["a", "b"]}, _ctx())
        result = _h_update({"index": 0, "status": "in_progress"}, _ctx())
        statuses = [item["status"] for item in result["plan"]["items"]]
        self.assertEqual(statuses, ["in_progress", "todo"])

    def test_complete_marks_done(self) -> None:
        _h_set({"items": ["a"]}, _ctx())
        result = _h_complete({"index": 0}, _ctx())
        self.assertEqual(result["plan"]["items"][0]["status"], "done")
        self.assertEqual(result["plan"]["summary"], "1/1 done, 0 in progress")

    def test_list_returns_current_plan(self) -> None:
        _h_set({"items": ["x"]}, _ctx())
        result = _h_list({}, _ctx())
        self.assertEqual(len(result["plan"]["items"]), 1)
        self.assertEqual(result["plan"]["items"][0]["text"], "x")

    def test_list_with_no_plan_returns_empty_envelope(self) -> None:
        result = _h_list({}, _ctx("sess-no-plan"))
        self.assertEqual(result["plan"]["items"], [])
        self.assertEqual(result["plan"]["summary"], "0/0 done, 0 in progress")

    def test_clear_returns_empty_envelope(self) -> None:
        _h_set({"items": ["a"]}, _ctx())
        result = _h_clear({}, _ctx())
        self.assertEqual(result["plan"]["items"], [])
        self.assertEqual(result["message"], "Plan cleared.")


class ErrorMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_store_for_tests()

    def test_update_without_plan_raises_plan_empty(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_update({"index": 0, "status": "done"}, _ctx("sess-no-plan"))
        self.assertEqual(ctx.exception.code, "PLAN_EMPTY")

    def test_complete_without_plan_raises_plan_empty(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_complete({"index": 0}, _ctx("sess-no-plan"))
        self.assertEqual(ctx.exception.code, "PLAN_EMPTY")

    def test_add_without_plan_raises_plan_empty(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_add({"item": "x"}, _ctx("sess-no-plan"))
        self.assertEqual(ctx.exception.code, "PLAN_EMPTY")

    def test_update_out_of_range_raises_invalid_plan_index(self) -> None:
        _h_set({"items": ["a"]}, _ctx())
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_update({"index": 99, "status": "done"}, _ctx())
        self.assertEqual(ctx.exception.code, "INVALID_PLAN_INDEX")

    def test_update_invalid_status_raises_invalid_plan_status(self) -> None:
        _h_set({"items": ["a"]}, _ctx())
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_update({"index": 0, "status": "frobnicate"}, _ctx())
        self.assertEqual(ctx.exception.code, "INVALID_PLAN_STATUS")


class SessionIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_store_for_tests()

    def test_two_sessions_have_independent_plans(self) -> None:
        _h_set({"items": ["a-1", "a-2"]}, _ctx("sess-a"))
        _h_set({"items": ["b-1"]}, _ctx("sess-b"))

        plan_a = _h_list({}, _ctx("sess-a"))["plan"]
        plan_b = _h_list({}, _ctx("sess-b"))["plan"]

        self.assertEqual(len(plan_a["items"]), 2)
        self.assertEqual(len(plan_b["items"]), 1)
        self.assertEqual(plan_a["items"][0]["text"], "a-1")
        self.assertEqual(plan_b["items"][0]["text"], "b-1")

    def test_fallback_session_id_used_when_context_lacks_one(self) -> None:
        # When session_id is absent on the context, the handler falls back
        # to "_default" so the tool stays usable in test/bootstrap flows.
        ctx_without_session = SimpleNamespace()
        result = _h_set({"items": ["x"]}, ctx_without_session)
        self.assertEqual(result["plan"]["session_id"], "_default")
