from __future__ import annotations

from openminion.cli.presentation.plan_render import (
    EMPTY_PLAN_TEXT,
    STATUS_MARK,
    UNKNOWN_STATUS_MARK,
    render_plan,
    render_plan_envelope,
)


class TestStatusMark:
    def test_todo_mark_is_blank(self) -> None:
        assert STATUS_MARK["todo"] == " "

    def test_in_progress_mark_is_arrow(self) -> None:
        assert STATUS_MARK["in_progress"] == "→"

    def test_done_mark_is_x(self) -> None:
        assert STATUS_MARK["done"] == "x"

    def test_blocked_mark_is_bang(self) -> None:
        assert STATUS_MARK["blocked"] == "!"

    def test_all_four_marks_are_distinct(self) -> None:
        marks = {
            STATUS_MARK[status] for status in ("todo", "in_progress", "done", "blocked")
        }
        assert len(marks) == 4


class TestRenderPlan:
    def test_none_plan_returns_empty_text(self) -> None:
        assert render_plan(None) == EMPTY_PLAN_TEXT

    def test_plan_with_no_items_returns_empty_text(self) -> None:
        assert (
            render_plan({"items": [], "summary": "0/0 done, 0 in progress"})
            == EMPTY_PLAN_TEXT
        )

    def test_single_todo_item(self) -> None:
        plan = {
            "items": [{"index": 0, "text": "Read config", "status": "todo"}],
            "summary": "0/1 done, 0 in progress",
        }
        output = render_plan(plan)
        assert "Plan (0/1 done, 0 in progress):" in output
        assert "[ ] Read config" in output

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
        assert "[x] alpha" in output
        assert "[→] beta" in output
        assert "[!] gamma" in output
        assert "[ ] delta" in output
        assert "[→] gamma" not in output
        assert "[!] beta" not in output

    def test_summary_is_used_verbatim_not_recomputed(self) -> None:
        plan = {
            "items": [
                {"index": 0, "text": "x", "status": "todo"},
            ],
            "summary": "WHATEVER THE ENVELOPE SAID",
        }
        output = render_plan(plan)
        assert "Plan (WHATEVER THE ENVELOPE SAID):" in output

    def test_empty_summary_falls_back_to_plain_header(self) -> None:
        plan = {
            "items": [{"index": 0, "text": "x", "status": "todo"}],
            "summary": "",
        }
        output = render_plan(plan)
        assert output.startswith("Plan:")

    def test_unknown_status_gets_fallback_mark(self) -> None:
        plan = {
            "items": [
                {"index": 0, "text": "future", "status": "deferred"},
            ],
            "summary": "0/1 done, 0 in progress",
        }
        output = render_plan(plan)
        assert f"[{UNKNOWN_STATUS_MARK}] future" in output

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
        assert first_idx < second_idx < third_idx


class TestRenderEnvelope:
    def test_none_envelope_returns_empty_text(self) -> None:
        assert render_plan_envelope(None) == EMPTY_PLAN_TEXT

    def test_envelope_missing_plan_key_returns_empty_text(self) -> None:
        assert render_plan_envelope({"message": "no plan"}) == EMPTY_PLAN_TEXT

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
        assert "[x] task" in output
