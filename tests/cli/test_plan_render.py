from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from openminion.cli.chat.commands.plan import handle_plan_command
from openminion.cli.presentation.plan_render import (
    EMPTY_PLAN_TEXT,
    STATUS_MARK,
    UNKNOWN_STATUS_MARK,
    render_plan,
    render_plan_envelope,
)
from openminion.tools.todo.plugin import _h_set, _reset_store_for_tests


def _ctx(session_id: str = "sess-render-test") -> object:
    return SimpleNamespace(session_id=session_id)


class StatusMarkTests(unittest.TestCase):
    def test_todo_mark_is_blank(self) -> None:
        self.assertEqual(STATUS_MARK["todo"], " ")

    def test_in_progress_mark_is_arrow(self) -> None:
        self.assertEqual(STATUS_MARK["in_progress"], "→")

    def test_done_mark_is_x(self) -> None:
        self.assertEqual(STATUS_MARK["done"], "x")

    def test_blocked_mark_is_bang(self) -> None:
        self.assertEqual(STATUS_MARK["blocked"], "!")

    def test_all_four_marks_are_distinct(self) -> None:
        marks = {
            STATUS_MARK[status] for status in ("todo", "in_progress", "done", "blocked")
        }
        self.assertEqual(len(marks), 4)


class RenderPlanTests(unittest.TestCase):
    def test_none_plan_returns_empty_text(self) -> None:
        self.assertEqual(render_plan(None), EMPTY_PLAN_TEXT)

    def test_plan_with_no_items_returns_empty_text(self) -> None:
        self.assertEqual(
            render_plan({"items": [], "summary": "0/0 done, 0 in progress"}),
            EMPTY_PLAN_TEXT,
        )

    def test_single_todo_item(self) -> None:
        plan = {
            "items": [{"index": 0, "text": "Read config", "status": "todo"}],
            "summary": "0/1 done, 0 in progress",
        }
        output = render_plan(plan)
        self.assertIn("Plan (0/1 done, 0 in progress):", output)
        self.assertIn("[ ] Read config", output)

    def test_mixed_statuses_render_with_distinct_marks(self) -> None:
        plan = {
            "items": [
                {"index": 0, "text": "alpha", "status": "done"},
                {"index": 1, "text": "beta", "status": "in_progress"},
                {"index": 2, "text": "gamma", "status": "blocked"},
                {"index": 3, "text": "delta", "status": "todo"},
            ],
            "summary": "1/4 done, 1 in progress",
        }
        output = render_plan(plan)
        self.assertIn("[x] alpha", output)
        self.assertIn("[→] beta", output)
        self.assertIn("[!] gamma", output)
        self.assertIn("[ ] delta", output)
        self.assertNotIn("[→] gamma", output)
        self.assertNotIn("[!] beta", output)

    def test_summary_is_used_verbatim_not_recomputed(self) -> None:
        plan = {
            "items": [
                {"index": 0, "text": "x", "status": "todo"},
            ],
            "summary": "WHATEVER THE ENVELOPE SAID",
        }
        output = render_plan(plan)
        self.assertIn("Plan (WHATEVER THE ENVELOPE SAID):", output)

    def test_empty_summary_falls_back_to_plain_header(self) -> None:
        plan = {
            "items": [{"index": 0, "text": "x", "status": "todo"}],
            "summary": "",
        }
        output = render_plan(plan)
        self.assertTrue(output.startswith("Plan:"))

    def test_unknown_status_gets_fallback_mark(self) -> None:
        plan = {
            "items": [
                {"index": 0, "text": "future", "status": "deferred"},
            ],
            "summary": "0/1 done, 0 in progress",
        }
        output = render_plan(plan)
        self.assertIn(f"[{UNKNOWN_STATUS_MARK}] future", output)

    def test_items_render_in_envelope_order(self) -> None:
        plan = {
            "items": [
                {"index": 0, "text": "first", "status": "todo"},
                {"index": 1, "text": "second", "status": "todo"},
                {"index": 2, "text": "third", "status": "todo"},
            ],
            "summary": "0/3 done, 0 in progress",
        }
        output = render_plan(plan)
        first_idx = output.find("first")
        second_idx = output.find("second")
        third_idx = output.find("third")
        self.assertLess(first_idx, second_idx)
        self.assertLess(second_idx, third_idx)


class RenderEnvelopeTests(unittest.TestCase):
    def test_none_envelope_returns_empty_text(self) -> None:
        self.assertEqual(render_plan_envelope(None), EMPTY_PLAN_TEXT)

    def test_envelope_missing_plan_key_returns_empty_text(self) -> None:
        self.assertEqual(render_plan_envelope({"message": "no plan"}), EMPTY_PLAN_TEXT)

    def test_envelope_with_plan_renders_items(self) -> None:
        envelope = {
            "plan": {
                "session_id": "sess",
                "items": [{"index": 0, "text": "task", "status": "done"}],
                "summary": "1/1 done, 0 in progress",
            },
            "message": "anything",
        }
        output = render_plan_envelope(envelope)
        self.assertIn("[x] task", output)


class HandlePlanCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_store_for_tests()

    def _capture(
        self, line: str, *, session_id: str = "sess-plan-cmd"
    ) -> tuple[bool, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            handled = handle_plan_command(line, session_id=session_id)
        return handled, buf.getvalue()

    def test_plan_bare_renders_current_plan(self) -> None:
        _h_set({"items": ["read", "edit", "run"]}, _ctx("sess-plan-cmd"))
        handled, output = self._capture("/plan")
        self.assertTrue(handled)
        self.assertIn("Plan", output)
        self.assertIn("[ ] read", output)

    def test_plan_show_renders_current_plan(self) -> None:
        _h_set({"items": ["a"]}, _ctx("sess-plan-cmd"))
        handled, output = self._capture("/plan show")
        self.assertTrue(handled)
        self.assertIn("[ ] a", output)

    def test_plan_show_with_no_plan_renders_empty_text(self) -> None:
        handled, output = self._capture("/plan show", session_id="sess-virgin")
        self.assertTrue(handled)
        self.assertIn(EMPTY_PLAN_TEXT, output)

    def test_plan_clear_drops_plan_and_prints_confirmation(self) -> None:
        _h_set({"items": ["x"]}, _ctx("sess-plan-cmd"))
        handled, output = self._capture("/plan clear")
        self.assertTrue(handled)
        self.assertIn("Plan cleared.", output)
        self.assertIn(EMPTY_PLAN_TEXT, output)

        _, post = self._capture("/plan show")
        self.assertIn(EMPTY_PLAN_TEXT, post)

    def test_unknown_subaction_returns_usage_error(self) -> None:
        handled, output = self._capture("/plan frobnicate")
        self.assertTrue(handled)
        self.assertIn("usage:", output)
        self.assertIn("show", output)
        self.assertIn("clear", output)

    def test_handler_always_returns_true(self) -> None:
        for line in ("/plan", "/plan show", "/plan clear", "/plan xyz", "/plan   "):
            handled, _ = self._capture(line)
            self.assertTrue(handled, f"line={line!r} not handled")
