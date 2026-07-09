"""Shared prompt headings and render helpers for context blocks."""

GROUNDING_BLOCK_HEADER = "## Runtime Grounding"
PENDING_TURN_BLOCK_HEADER = "## Pending Turn Context"
PRIOR_TURN_BLOCK_HEADER = "## Prior Turn Context"
PROJECT_CONTEXT_FILE_HEADER = "## Project Context File"
THIRD_BRAIN_GRAPH_CONTEXT_HEADER = "## Third-brain graph context"
CURRENT_SESSION_SUMMARY_HEADER = "## Current session summary"
PRIOR_SESSION_SUMMARY_HEADER = "## Continuing from recent sessions"


def build_project_context_block(*, inbound_metadata: dict[str, str]) -> str:
    """Render the project context block appended to a system prompt."""

    body = str(inbound_metadata.get("project_context_body", "") or "").strip()
    if not body:
        return ""
    source_name = str(inbound_metadata.get("project_context_name", "") or "").strip()
    path_text = str(inbound_metadata.get("project_context_path", "") or "").strip()
    truncated = (
        str(inbound_metadata.get("project_context_truncated", "") or "").strip().lower()
        == "true"
    )
    lines = [PROJECT_CONTEXT_FILE_HEADER]
    if source_name:
        lines.append(f"- source_name: {source_name}")
    if path_text:
        lines.append(f"- path: {path_text}")
    if truncated:
        lines.append("- note: content was truncated to stay within shell limits.")
    lines.extend(
        [
            "",
            "Treat the following project context file as authoritative local guidance for this project:",
            body,
        ]
    )
    return "\n".join(lines).strip()


__all__ = [
    "CURRENT_SESSION_SUMMARY_HEADER",
    "GROUNDING_BLOCK_HEADER",
    "PENDING_TURN_BLOCK_HEADER",
    "PRIOR_SESSION_SUMMARY_HEADER",
    "PRIOR_TURN_BLOCK_HEADER",
    "PROJECT_CONTEXT_FILE_HEADER",
    "THIRD_BRAIN_GRAPH_CONTEXT_HEADER",
    "build_project_context_block",
]
