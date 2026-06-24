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
    lines.extend(_context_inventory_lines(runtime))
    return "\n".join(lines)


def render_memory_report(runtime: Any) -> str:
    reporter = getattr(runtime, "memory_report", None)
    if callable(reporter):
        try:
            body = str(reporter() or "").strip()
            if body:
                return body
        except Exception as exc:
            return f"/memory: {exc}"
    rows = _safe_call(getattr(runtime, "list_memory_records", None)) or []
    candidates = _safe_call(getattr(runtime, "list_memory_candidates", None)) or []
    if not rows and not candidates:
        return "(no memory)"
    lines = [
        "Memory:",
        f"  promoted   {len(rows)}",
        f"  candidates {len(candidates)}",
    ]
    for row in list(rows)[:8]:
        title = (
            _row_value(row, "title")
            or _row_value(row, "content_preview")
            or _row_value(row, "id")
        )
        if title:
            lines.append(f"  - {str(title)[:96]}")
    return "\n".join(lines)


def render_skills_report(runtime: Any) -> str:
    reporter = getattr(runtime, "skills_report", None)
    if callable(reporter):
        try:
            body = str(reporter() or "").strip()
            if body:
                return body
        except Exception as exc:
            return f"/skills: {exc}"
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
        result = setter(preset)
        return f"statusline → {value.lower() if result or value else 'default'}"
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
    "render_context_report",
    "render_memory_report",
    "render_skills_report",
    "statusline_label",
]
