from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from openminion.cli.status import format_token_usage_summary


EFFORT_VALUES: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
STATUSLINE_PRESETS: dict[str, str] = {
    "default": "",
    "minimal": "preset:minimal",
    "ops": "preset:ops",
    "cost": "preset:cost",
}


def render_context_report(runtime: Any) -> str:
    snapshot = _safe_call(getattr(runtime, "token_usage_snapshot", None))
    budget = _safe_call(getattr(runtime, "context_budget_snapshot", None)) or {}
    lines = ["Context usage:"]
    if snapshot is None:
        lines.append("  tokens   unavailable")
    else:
        summary = format_token_usage_summary(snapshot)
        lines.append(f"  tokens   {summary or 'no usage yet'}")
        used = getattr(snapshot, "context_used_tokens", None)
        limit = getattr(snapshot, "context_limit_tokens", None)
        if used is not None and limit:
            lines.append(f"  grid     {_usage_grid(int(used), int(limit))}")
    if budget:
        max_tokens = int(budget.get("max_tokens", 0) or 0)
        source = str(budget.get("budget_source", "") or "unavailable")
        limit_label = f"{max_tokens} tokens" if max_tokens > 0 else "count fallback"
        lines.append(f"  budget   {limit_label} ({source})")
        selected_count = int(budget.get("selected_recent_count", 0) or 0)
        selected_tokens = int(budget.get("selected_recent_tokens", 0) or 0)
        lines.append(f"  recent   {selected_count} messages · {selected_tokens} tokens")
        lines.append("  allocation summary/retrieval unavailable")
        reason = str(budget.get("compaction_reason") or budget.get("trim_reason") or "")
        if reason:
            count = int(budget.get("compacted_count", 0) or 0)
            suffix = f" · {count} compacted" if count else ""
            lines.append(f"  action   {reason}{suffix}")
        if budget.get("overflow"):
            lines.append("  status   overflow")
    lines.extend(_context_inventory_lines(runtime))
    return "\n".join(lines)


def render_memory_report(runtime: Any) -> str:
    if report := _runtime_report(runtime, "memory_report", "/memory"):
        return report
    rows = _safe_call(getattr(runtime, "list_memory_records", None)) or []
    candidates = _safe_call(getattr(runtime, "list_memory_candidates", None)) or []
    return format_memory_report(
        rows,
        candidates,
        session_id=str(getattr(runtime, "session_id", "") or ""),
    )


def format_memory_report(
    rows: list[Any], candidates: list[Any], *, session_id: str = ""
) -> str:
    records = list(rows or [])
    pending = list(candidates or [])
    lines = [
        "Memory:",
        f"  records     {len(records)}",
        f"  candidates  {len(pending)}",
    ]
    if not records and not pending:
        lines.extend(("", "No persisted memory for this session or agent."))
        return "\n".join(lines)

    type_counts: dict[str, int] = {}
    summaries: list[Any] = []
    other_records: list[Any] = []
    for row in records:
        record_type = _memory_text(row, "type") or "record"
        type_counts[record_type] = type_counts.get(record_type, 0) + 1
        if record_type == "session_summary":
            summaries.append(row)
        else:
            other_records.append(row)

    lines.extend(("", "By type:"))
    for record_type, count in sorted(
        type_counts.items(), key=lambda item: (-item[1], item[0])
    ):
        lines.append(f"  {_memory_type_label(record_type):<18} {count}")

    current_summary = next(
        (
            row
            for row in summaries
            if _memory_text(row, "key") == f"session_summary:{session_id}"
        ),
        None,
    )
    if current_summary is not None:
        lines.extend(("", "Current session summary:"))
        questions = _summary_user_questions(current_summary)
        if questions:
            lines.extend(f"  - {question[:120]}" for question in questions[-5:])
        else:
            lines.append(f"  - {_memory_record_title(current_summary)}")
        if updated_at := _memory_text(current_summary, "updated_at"):
            lines.append(f"  updated {updated_at.replace('T', ' ')[:19]} UTC")

    if other_records:
        lines.extend(("", "Recent records:"))
        for row in other_records[:6]:
            lines.append(f"  - {_memory_record_title(row)}")
            record_type = _memory_type_label(_memory_text(row, "type") or "record")
            scope = _memory_scope_label(_memory_text(row, "scope"))
            lines.append(f"    {record_type} · {scope}")

    previous_summary_count = len(summaries) - int(current_summary is not None)
    if previous_summary_count:
        lines.extend(("", f"Previous session summaries: {previous_summary_count}"))

    if pending:
        lines.extend(("", "Pending candidates:"))
        for row in pending[:5]:
            lines.append(f"  - {_memory_record_title(row)}")

    lines.extend(
        (
            "",
            "The current turn is added after it finishes. Full question history stays in the session transcript.",
        )
    )
    return "\n".join(lines)


def _memory_value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _memory_text(row: Any, key: str) -> str:
    return str(_memory_value(row, key) or "").strip()


def _memory_record_title(row: Any) -> str:
    title = (
        _memory_text(row, "title")
        or _memory_text(row, "content_preview")
        or _memory_text(row, "candidate_id")
        or _memory_text(row, "id")
        or "Untitled record"
    )
    first_line = next(
        (line.strip() for line in title.splitlines() if line.strip()), title
    )
    return first_line.removeprefix("- ")[:120]


def _summary_user_questions(row: Any) -> list[str]:
    content = _memory_value(row, "content")
    summary_text = (
        str(content.get("summary_text", "") or "") if isinstance(content, dict) else ""
    )
    source = summary_text or _memory_text(row, "title")
    questions: list[str] = []
    for line in source.splitlines():
        normalized = line.strip().removeprefix("- ").strip()
        role, separator, text = normalized.partition(":")
        if separator and role.strip().lower() == "user" and text.strip():
            question = text.strip()
            if question not in questions:
                questions.append(question)
    return questions


def _memory_type_label(record_type: str) -> str:
    return str(record_type or "record").replace("_", " ")


def _memory_scope_label(scope: str) -> str:
    return str(scope or "unknown").partition(":")[0] or "unknown"


def render_skills_report(runtime: Any) -> str:
    if report := _runtime_report(runtime, "skills_report", "/skills"):
        return report
    rows = _safe_call(getattr(runtime, "list_skill_rows", None)) or []
    if not rows:
        return "(no skills)"
    lines = ["Skills:"]
    for row in list(rows)[:20]:
        skill_id = (
            _row_value(row, "id")
            or _row_value(row, "skill_id")
            or _row_value(row, "name")
        )
        source = _row_value(row, "source") or _row_value(row, "status") or ""
        tokens = _row_value(row, "tokens") or _row_value(row, "estimated_tokens") or ""
        suffix = ""
        if source:
            suffix += f" · {source}"
        if tokens:
            suffix += f" · {tokens} tokens"
        lines.append(f"  - {skill_id}{suffix}")
    return "\n".join(lines)


def render_tasks_report(runtime: Any, task_id: str = "") -> str:
    from openminion.modules.task.surface import (
        build_task_surface,
        resolve_task_surface_source,
    )

    surface = build_task_surface(resolve_task_surface_source(runtime))
    selected_id = str(task_id or "").strip()
    if selected_id:
        task = surface.show_task(selected_id)
        if task is None:
            return f"Task not found: {selected_id}"
        lines = [
            "Task",
            "====",
            f"id: {task.get('id')}",
            f"title: {task.get('title')}",
            f"status: {task.get('status')}",
            f"operator_state: {task.get('operator_state', '-')}",
            f"resume_action: {task.get('resume_action', '-')}",
        ]
        if task.get("due_at"):
            lines.append(f"due: {task.get('due_at')}")
        return "\n".join(lines)

    payload = surface.inventory()
    lines = ["Tasks", "====="]
    tasks = list(payload.get("tasks", []))
    if not tasks:
        lines.append("No tasks found.")
    for task in tasks[:20]:
        lines.append(
            f"[{task.get('status', 'PENDING')}] {task.get('id')}: "
            f"{task.get('title')} "
            f"(operator={task.get('operator_state', '-')}, "
            f"resume={task.get('resume_action', '-')})"
        )
    pending = list(payload.get("pending_actions", []))
    if pending:
        lines.extend(("", "Pending actions:"))
        for action in pending[:20]:
            lines.append(
                f"- {action.get('decision_id')}: task={action.get('task_id') or '-'}"
            )
    return "\n".join(lines)


def handle_effort_command(runtime: Any, arg: str) -> str:
    value = str(arg or "").strip().lower()
    getter = getattr(runtime, "effort_level", "")
    current = str(getter() if callable(getter) else getter or "").strip() or "default"
    if not value:
        return f"effort → {current}\nUse `/effort {'|'.join(EFFORT_VALUES)}|default`."
    setter = getattr(runtime, "set_effort_level", None)
    if not callable(setter):
        return "(/effort: runtime does not expose set_effort_level)"
    try:
        return f"effort → {setter(value)}"
    except ValueError as exc:
        return f"/effort: {exc}"


def handle_statusline_command(runtime: Any, arg: str) -> str:
    value = str(arg or "").strip()
    setter = getattr(runtime, "set_statusline_command", None)
    getter = getattr(runtime, "statusline_command", None)
    if not value:
        current = str(getter() if callable(getter) else getter or "").strip()
        return (
            f"statusline → {current or 'default'}\n"
            "Presets: default|minimal|ops|cost. "
            "Use `/statusline <preset>` or `/statusline <custom command>`."
        )
    if not callable(setter):
        return "(/statusline: runtime does not expose set_statusline_command)"
    preset = STATUSLINE_PRESETS.get(value.lower())
    if preset is not None:
        setter(preset)
        return f"statusline → {value.lower()}"
    return f"statusline → {setter(value)}"


def handle_undo_command(runtime: Any, arg: str, *, working_dir: str = "") -> str:
    value = str(arg or "").strip()
    if value.startswith("file "):
        target = value.split(maxsplit=1)[1].strip()
        return _restore_file_with_git(target, working_dir=working_dir)
    undoer = getattr(runtime, "undo_last_turn", None)
    if not callable(undoer):
        return "(/undo: runtime does not expose undo_last_turn)"
    try:
        result = undoer()
    except Exception as exc:
        return f"/undo: {exc}"
    if isinstance(result, dict):
        if not result.get("ok", False):
            return str(result.get("message") or "(no undoable action)")
        return str(result.get("message") or "rewound one turn")
    return str(result or "rewound one turn")


def statusline_label(runtime: Any) -> str:
    getter = getattr(runtime, "statusline_label", None)
    if callable(getter):
        try:
            return str(getter() or "").strip()
        except (AttributeError, TypeError, ValueError):
            return ""
    return ""


def _usage_grid(used: int, limit: int, *, width: int = 50) -> str:
    if limit <= 0:
        return "□" * width
    filled = max(0, min(width, round((used / limit) * width)))
    return "■" * filled + "□" * (width - filled)


def _context_inventory_lines(runtime: Any) -> list[str]:
    tools = _safe_call(getattr(runtime, "list_tools", None)) or []
    memory = _safe_call(getattr(runtime, "list_memory_records", None)) or []
    skills = _safe_call(getattr(runtime, "list_skill_rows", None)) or []
    return [
        f"  tools    {len(tools)}",
        f"  memory   {len(memory)}",
        f"  skills   {len(skills)}",
    ]


def _safe_call(callback: Any) -> Any:
    if not callable(callback):
        return None
    try:
        return callback()
    except Exception:
        return None


def _runtime_report(runtime: Any, attribute: str, command: str) -> str | None:
    reporter = getattr(runtime, attribute, None)
    if not callable(reporter):
        return None
    try:
        return str(reporter() or "").strip() or None
    except Exception as exc:
        return f"{command}: {exc}"


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _restore_file_with_git(target: str, *, working_dir: str = "") -> str:
    rel = str(target or "").strip()
    if not rel:
        return "usage: /undo file <path>"
    root = Path(working_dir or ".").resolve(strict=False)
    candidate = (root / rel).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return "/undo file: path must stay inside the workspace"
    proc = subprocess.run(
        ["git", "restore", "--", rel],
        cwd=str(root),
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "git restore failed").strip()
        return f"/undo file: {detail}"
    return f"restored {rel}"


__all__ = [
    "EFFORT_VALUES",
    "STATUSLINE_PRESETS",
    "handle_effort_command",
    "handle_statusline_command",
    "handle_undo_command",
    "format_memory_report",
    "render_context_report",
    "render_memory_report",
    "render_skills_report",
    "render_tasks_report",
    "statusline_label",
]
