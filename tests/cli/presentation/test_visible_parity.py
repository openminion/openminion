from __future__ import annotations

from types import SimpleNamespace

from openminion.cli.status.token_usage import TokenUsageSnapshot
from openminion.cli.presentation.visible_parity import (
    handle_effort_command,
    handle_statusline_command,
    handle_undo_command,
    render_context_report,
    format_memory_report,
    render_memory_report,
    render_skills_report,
    render_tasks_report,
    statusline_label,
)
from openminion.modules.memory.runtime.capture_status import (
    CaptureProcessingSummary,
    RecallProcessingSummary,
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

    def context_budget_snapshot(self):
        return {
            "max_tokens": 8000,
            "budget_source": "runtime_cap",
            "selected_recent_count": 12,
            "selected_recent_tokens": 640,
            "trim_reason": "token_budget",
            "compacted_count": 3,
            "compaction_reason": "token_pressure",
            "overflow": False,
        }

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
    assert "budget   8000 tokens (runtime_cap)" in body
    assert "recent   12 messages · 640 tokens" in body
    assert "allocation summary/retrieval unavailable" in body
    assert "action   token_pressure · 3 compacted" in body
    assert max(map(len, body.splitlines())) <= 80


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


def test_format_memory_report_shows_content_free_capture_health() -> None:
    body = format_memory_report(
        [],
        [],
        capture=CaptureProcessingSummary(
            pending=1,
            processed=2,
            succeeded_no_output=3,
            rejected=0,
            failed_terminal=1,
            oldest_pending_at="2026-08-24T00:00:00Z",
            eligible=7,
            terminal=6,
            integrity_errors=0,
        ),
        recall=RecallProcessingSummary(
            health="healthy",
            mode="shadow",
            capabilities=("keyword", "graph", "vector"),
            score_domain="hybrid-semantic-v1",
            selected_memory=2,
            selected_knowledge=1,
            omission_reasons=(("budget", 1), ("relevance", 2)),
        ),
    )

    assert "capture     7 eligible · 1 pending · 6 terminal" in body
    assert "terminal    2 processed · 3 no output · 0 rejected · 1 failed" in body
    assert "integrity   0 errors" in body
    assert "oldest      2026-08-24T00:00:00Z" in body
    assert "recall      healthy · mode shadow" in body
    assert "capability  keyword, graph, vector" in body
    assert "score       hybrid-semantic-v1" in body
    assert "selected    memory 2 · knowledge 1" in body
    assert "omissions   budget 1 · relevance 2" in body


def test_format_memory_status_does_not_render_sensitive_runtime_fields() -> None:
    body = format_memory_report(
        [],
        [],
        capture=CaptureProcessingSummary(
            pending=0,
            processed=0,
            succeeded_no_output=0,
            rejected=0,
            failed_terminal=0,
            oldest_pending_at="",
        ),
        recall=SimpleNamespace(
            health="degraded",
            mode="sophiagraph",
            capabilities=("keyword",),
            score_domain="structured-retrieval-v1",
            selected_memory=0,
            selected_knowledge=0,
            omission_reasons=(),
            transcript="private transcript",
            query="private query",
            exception="private exception",
            path="/private/path",
            url="https://private.invalid",
            provider="provider-name",
            model="model-name",
            secret="secret-value",
        ),
    )

    assert "recall      degraded" in body
    for sensitive in (
        "private transcript",
        "private query",
        "private exception",
        "/private/path",
        "https://private.invalid",
        "provider-name",
        "model-name",
        "secret-value",
    ):
        assert sensitive not in body


def test_render_skills_report_uses_runtime_rows() -> None:
    body = render_skills_report(_Runtime())

    assert "reviewer" in body
    assert "config" in body
    assert "120 tokens" in body
    assert "Use /skills <skill_id> to view details." in body


def test_render_skills_report_passes_exact_skill_id_to_runtime() -> None:
    class _SkillRuntime:
        def skills_report(self, skill_id: str = "") -> str:
            return f"detail:{skill_id}"

    assert render_skills_report(_SkillRuntime(), "demo-skill") == "detail:demo-skill"


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
