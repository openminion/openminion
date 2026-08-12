from __future__ import annotations

from typing import Any

from ..contracts import ADAPTIVE_TERM_FINAL_TEXT, AdaptiveToolLoopOutcome
from ..evidence import _successful_substantive_tool_results

MUTATING_FILE_CLOSEOUT_KEY = "mutating_file_answer_only_closure_pending"
MUTATING_FILE_PATH_COUNTS_KEY = "mutating_file_success_path_counts"
_MUTATING_FILE_TOOL_NAMES = frozenset(
    {
        "code.patch",
        "file.edit",
        "file.write",
        "write_file",
    }
)


def _changed_paths_from_tool_results(tool_results: list[dict[str, Any]]) -> list[str]:
    changed_paths: list[str] = []
    for item in tool_results:
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        path = str(data.get("path", "") or "").strip()
        if path and path not in changed_paths:
            changed_paths.append(path)
    return changed_paths


def _is_mutating_file_tool_result(item: dict[str, Any]) -> bool:
    tool_name = str(item.get("tool_name") or "").strip().lower()
    if tool_name in _MUTATING_FILE_TOOL_NAMES:
        return True
    return tool_name.startswith(("code.patch", "file.edit", "file.write"))


def _mutating_file_tool_results(
    tool_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [item for item in tool_results if _is_mutating_file_tool_result(item)]


def _tool_evidence_lines(tool_results: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in tool_results[-5:]:
        tool_name = str(item.get("tool_name") or "tool").strip() or "tool"
        summary = str(item.get("content") or "").strip()
        if not summary:
            data = item.get("data")
            if isinstance(data, dict):
                summary = str(data.get("summary") or data.get("stdout") or "").strip()
        lines.append(f"- {tool_name}: {summary or 'success'}")
    return lines


def tool_evidence_closeout_text(loop_state: Any, *, reason: str) -> str:
    tool_results = _successful_substantive_tool_results(loop_state)
    if not tool_results:
        return ""
    mutating_results = _mutating_file_tool_results(tool_results)
    changed_paths = _changed_paths_from_tool_results(mutating_results)
    lines = [f"result: {reason}"]
    if changed_paths:
        lines.append(f"files changed: {', '.join(changed_paths[-8:])}")
    lines.append("tool evidence:")
    lines.extend(_tool_evidence_lines(tool_results))
    return "\n".join(lines)


def tool_evidence_closeout_outcome(
    *,
    profile: Any,
    loop_state: Any,
    allowed_tools: frozenset[str],
    reason: str,
    scratchpad_key: str,
) -> AdaptiveToolLoopOutcome | None:
    fallback_text = tool_evidence_closeout_text(loop_state, reason=reason)
    if not fallback_text:
        return None
    loop_state.scratchpad[scratchpad_key] = True
    loop_state.termination_reason = ADAPTIVE_TERM_FINAL_TEXT
    return AdaptiveToolLoopOutcome(
        profile_name=profile.profile_name,
        mode_name=profile.mode_name,
        termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
        state=loop_state,
        allowed_tools=allowed_tools,
        final_text=fallback_text,
    )


def mutating_file_evidence_fallback_text(loop_state: Any) -> str:
    tool_results = _successful_substantive_tool_results(loop_state)
    mutating_results = _mutating_file_tool_results(tool_results)
    changed_paths = _changed_paths_from_tool_results(mutating_results)
    if not changed_paths:
        return ""
    rendered_paths = ", ".join(changed_paths[-8:])
    return "\n".join(
        (
            "result: successful file writes were closed from tool evidence.",
            f"files changed: {rendered_paths}",
        )
    )
