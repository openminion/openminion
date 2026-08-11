from __future__ import annotations

from openminion.cli.status.token_usage import TokenUsageSnapshot
from openminion.cli.presentation.visible_parity import (
    handle_effort_command,
    handle_statusline_command,
    handle_undo_command,
    render_context_report,
    render_memory_report,
    render_skills_report,
    render_tasks_report,
    statusline_label,
)
from openminion.modules.task.runtime.lifecycle import TaskManager


class _Runtime:
    effort_level = ""
    statusline_command = ""

    def token_usage_snapshot(self):
        return TokenUsageSnapshot(
            session_total_tokens=25,
            turn_total_tokens=5,
            context_used_tokens=25,
            context_limit_tokens=100,
        )

    def list_tools(self):
        return [("file.read", True), ("exec.run", True)]

    def list_memory_records(self):
        return [{"id": "m1", "title": "Project preference"}]

    def list_memory_candidates(self):
        return [{"id": "c1"}]

    def list_skill_rows(self):
        return [{"id": "reviewer", "source": "config", "tokens": 120}]

    def set_effort_level(self, value: str) -> str:
        self.effort_level = "" if value == "default" else value
        return self.effort_level or "default"

    def set_statusline_command(self, value: str) -> str:
        self.statusline_command = "" if value == "off" else value
        return self.statusline_command or "default"

    def undo_last_turn(self):
        return {"ok": True, "message": "rewound latest turn"}


def test_render_context_report_includes_grid_and_inventory() -> None:
    body = render_context_report(_Runtime())

    assert "Context usage:" in body
    assert "grid" in body
    assert "■■" in body
    assert "tools    2" in body
    assert "memory   1" in body
    assert "skills   1" in body


def test_render_memory_report_uses_runtime_rows() -> None:
    body = render_memory_report(_Runtime())

    assert "records     1" in body
    assert "candidates  1" in body
    assert "Project preference" in body


def test_render_memory_report_structures_current_session_summary() -> None:
    runtime = _Runtime()
    runtime.session_id = "session-1"
    runtime.list_memory_records = lambda: [
        {
            "type": "session_summary",
            "key": "session_summary:session-1",
            "title": "- user: first question",
            "content": {
                "summary_text": (
                    "- user: first question\n"
                    "- assistant: first answer\n"
                    "- user: second question"
                )
            },
            "scope": "agent:agent-1",
            "updated_at": "2026-08-11T08:47:22+00:00",
        },
        {
            "type": "session_summary",
            "key": "session_summary:older",
            "title": "- user: hi",
            "scope": "agent:agent-1",
        },
        {
            "type": "user_preference",
            "title": "Concise answers",
            "scope": "agent:agent-1",
        },
    ]

    body = render_memory_report(runtime)

    assert "Current session summary:" in body
    assert "- first question" in body
    assert "- second question" in body
    assert "Previous session summaries: 1" in body
    assert "Recent records:" in body
    assert "user preference · agent" in body
    assert "assistant: first answer" not in body


def test_render_skills_report_uses_runtime_rows() -> None:
    body = render_skills_report(_Runtime())

    assert "reviewer" in body
    assert "config" in body
    assert "120 tokens" in body


def test_render_tasks_report_includes_operator_state_and_resume_action(
    tmp_path,
) -> None:
    manager = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    manager.create_task(
        session_id="session-1",
        mode_name="project",
        goal="ship durable work",
        agent_id="agent-1",
        task_id="task-1",
    )

    runtime = type("Runtime", (), {"task_manager": manager})()
    inventory_body = render_tasks_report(runtime)
    detail_body = render_tasks_report(runtime, "task-1")

    assert "operator=running" in inventory_body
    assert "resume=continue" in inventory_body
    assert "operator_state: running" in detail_body
    assert "resume_action: continue" in detail_body


def test_effort_and_statusline_handlers_delegate_to_runtime() -> None:
    runtime = _Runtime()

    assert handle_effort_command(runtime, "high") == "effort → high"
    assert runtime.effort_level == "high"
    assert handle_statusline_command(runtime, "echo ok") == "statusline → echo ok"
    assert runtime.statusline_command == "echo ok"


def test_statusline_presets_are_explicit_runtime_values() -> None:
    runtime = _Runtime()

    assert handle_statusline_command(runtime, "cost") == "statusline → cost"
    assert runtime.statusline_command == "preset:cost"
    body = handle_statusline_command(runtime, "")
    assert "Presets: default|minimal|ops|cost" in body


def test_undo_handler_delegates_context_rewind_to_runtime() -> None:
    assert handle_undo_command(_Runtime(), "") == "rewound latest turn"


def test_statusline_label_returns_empty_when_getter_errors() -> None:
    class _BadRuntime:
        def statusline_label(self):
            raise ValueError("bad statusline")

    assert statusline_label(_BadRuntime()) == ""
