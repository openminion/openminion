from __future__ import annotations

import re
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
_MUTATING_FILE_REQUEST_PATTERNS = (
    "build a tiny",
    "create a tiny",
    "create file",
    "direct exec.run commands for checks",
    "file.write",
    "files changed",
    "implement it",
    "one minimal check",
    "run a focused check",
    "use file tools for files",
)


def requested_closeout_markers(loop_state: Any) -> tuple[str, ...]:
    messages = [
        str(getattr(message, "content", "") or "")
        for message in list(getattr(loop_state, "messages", []) or [])
        if str(getattr(message, "role", "") or "").strip().lower() == "user"
    ]
    combined = "\n".join(messages)
    lowered = combined.lower()
    markers: list[str] = []
    for match in re.finditer(
        r"exact labels?\s+((?:`[^`]+`\s*,?\s*)+)",
        combined,
        re.IGNORECASE,
    ):
        markers.extend(
            token.strip().strip("`").rstrip(":").lower()
            for token in re.findall(r"`([^`]+)`", match.group(1))
            if token.strip()
        )
    markers.extend(
        token.strip().strip("`").rstrip(":").lower()
        for token in re.findall(r"`([^`]+:)`", combined)
        if token.strip()
    )
    for match in re.finditer(r"exact label\s+`([^`]+)`", combined, re.IGNORECASE):
        token = match.group(1).strip().rstrip(":").lower()
        if token:
            markers.append(token)
    if "validation result" in lowered:
        markers.append("validation")
    if "files changed" in lowered:
        markers.append("files changed")
    if "remaining follow-ups" in lowered:
        markers.append("follow-ups")
    if "next steps" in lowered:
        markers.append("next steps")
    if "recommended direction" in lowered or "recommendation" in lowered:
        markers.append("recommendation")
    unique: list[str] = []
    for marker in markers:
        if marker and marker not in unique:
            unique.append(marker)
    return tuple(unique)


def missing_requested_closeout_markers(loop_state: Any, text: str) -> tuple[str, ...]:
    normalized_text = str(text or "").lower()
    return tuple(
        marker
        for marker in requested_closeout_markers(loop_state)
        if marker not in normalized_text
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


def _user_requested_file_mutation(loop_state: Any) -> bool:
    user_text = "\n".join(
        str(getattr(message, "content", "") or "")
        for message in list(getattr(loop_state, "messages", []) or [])
        if str(getattr(message, "role", "") or "").strip().lower() == "user"
    ).lower()
    return any(pattern in user_text for pattern in _MUTATING_FILE_REQUEST_PATTERNS)


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
    mutation_requested = _user_requested_file_mutation(loop_state)
    mutating_results = _mutating_file_tool_results(tool_results)
    if mutation_requested and not mutating_results:
        return ""
    requested = requested_closeout_markers(loop_state)
    changed_paths = _changed_paths_from_tool_results(
        mutating_results if mutation_requested else tool_results
    )
    rendered_paths = ", ".join(changed_paths[-8:]) if changed_paths else "none recorded"
    lines: list[str] = []
    for marker in requested:
        if marker == "result":
            lines.append(f"result: {reason}")
        elif marker in {"files", "files changed"}:
            lines.append(f"{marker}: {rendered_paths}")
        elif marker in {"validation", "validation result"}:
            lines.append(
                f"{marker}: deterministic validation was not captured before closeout; "
                "successful tool evidence is preserved below."
            )
        elif marker in {"follow-ups", "next steps"}:
            lines.append(
                f"{marker}: rerun a narrower continuation if stronger proof or a "
                "more polished synthesis is needed."
            )
        elif marker == "recommendation":
            lines.append(
                "recommendation: use the preserved tool evidence below as the "
                "basis for the final direction; rerun a narrower continuation if "
                "a more polished synthesis is needed."
            )
        else:
            lines.append(
                f"{marker}: not captured before closeout; preserved tool evidence "
                "is reported below."
            )
    if not lines:
        lines = [f"result: {reason}"]
        if changed_paths:
            lines.append(f"files changed: {rendered_paths}")
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
    requested = requested_closeout_markers(loop_state)
    lines: list[str] = []
    for marker in requested:
        normalized = str(marker or "").strip().lower().rstrip(":")
        if not normalized:
            continue
        if normalized == "result":
            lines.append(
                "result: stopped after successful file mutations and returned "
                "preserved tool evidence."
            )
        elif normalized in {"files", "files changed"}:
            lines.append(f"{normalized}: {rendered_paths}")
        elif normalized in {"validation", "validation result"}:
            lines.append(
                f"{normalized}: deterministic validation was not captured before "
                "closeout; successful file-write evidence is preserved above."
            )
        elif normalized in {"follow-ups", "next steps", "remaining follow-ups"}:
            lines.append(
                f"{normalized}: rerun focused validation if stronger proof is "
                "needed."
            )
        else:
            lines.append(
                f"{normalized}: not captured before closeout; preserved "
                "written-file evidence is reported instead."
            )
    if not any(line.startswith(("files:", "files changed:")) for line in lines):
        lines.append(f"files changed: {rendered_paths}")
    if not any(line.startswith("result:") for line in lines):
        lines.append(
            "result: successful file writes were closed from tool evidence."
        )
    return "\n".join(lines)
