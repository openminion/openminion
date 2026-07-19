from __future__ import annotations

import re
from typing import Any

from ..evidence import _successful_substantive_tool_results

MUTATING_FILE_CLOSEOUT_KEY = "mutating_file_answer_only_closure_pending"
MUTATING_FILE_PATH_COUNTS_KEY = "mutating_file_success_path_counts"


def _requested_closeout_markers(loop_state: Any) -> tuple[str, ...]:
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
    for match in re.finditer(r"exact label\s+`([^`]+)`", combined, re.IGNORECASE):
        token = match.group(1).strip().rstrip(":").lower()
        if token:
            markers.append(token)
    if "validation result" in lowered:
        markers.append("validation")
    if "files changed" in lowered:
        markers.append("files")
    if "remaining follow-ups" in lowered:
        markers.append("follow-ups")
    unique: list[str] = []
    for marker in markers:
        if marker and marker not in unique:
            unique.append(marker)
    return tuple(unique)


def mutating_file_evidence_fallback_text(loop_state: Any) -> str:
    tool_results = _successful_substantive_tool_results(loop_state)
    changed_paths: list[str] = []
    for item in tool_results:
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        path = str(data.get("path", "") or "").strip()
        if path and path not in changed_paths:
            changed_paths.append(path)
    if not changed_paths:
        return ""
    rendered_paths = ", ".join(changed_paths[-8:])
    requested = set(_requested_closeout_markers(loop_state))
    lines: list[str] = []
    if "result" in requested:
        lines.append(
            "result: stopped after repeated successful file mutations and returned "
            "the preserved tool evidence."
        )
    lines.append(f"files changed: {rendered_paths}")
    if "validation" in requested:
        lines.append(
            "validation: not captured after the repeated-mutation closeout guard; "
            "successful file-write evidence is preserved above."
        )
    if "follow-ups" in requested:
        lines.append("follow-ups: rerun focused validation if stronger proof is needed.")
    if "result" not in requested:
        lines.append(
            "result: repeated successful file writes were closed from tool evidence."
        )
    return "\n".join(lines)
