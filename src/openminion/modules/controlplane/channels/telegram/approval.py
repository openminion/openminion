from typing import Any

from openminion.modules.tool.contracts.schemas import TOOL_ERROR_CONFIRM_REQUIRED


# Canonical typed approval choices shared by channel-facing approval payloads.
APPROVAL_CHOICES: tuple[str, ...] = (
    "allow_once",
    "allow_session",
    "allow_forever",
    "deny",
)


def _normalized_choices(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [
        normalized
        for item in raw
        if isinstance(item, str) and (normalized := item.strip())
    ]


def extract_approval_request(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return a normalized approval-request dict when the payload asks for it."""
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = str(error.get("code", "")).strip()
    if code != TOOL_ERROR_CONFIRM_REQUIRED:
        return None
    details = error.get("details")
    if not isinstance(details, dict):
        return None
    approval_id = str(details.get("approval_id", "")).strip()
    if not approval_id:
        return None
    choices = _normalized_choices(details.get("choices"))
    if not choices:
        return None
    reason = str(details.get("reason", "")).strip()
    session_id = str(payload.get("session_id", "")).strip()
    trace_id = ""
    payload_data = payload.get("data")
    if isinstance(payload_data, dict):
        trace_id = str(payload_data.get("trace_id", "")).strip()
    return {
        "approval_id": approval_id,
        "choices": choices,
        "reason": reason,
        "session_id": session_id,
        "trace_id": trace_id,
    }


def render_approval_prompt(
    request: dict[str, Any],
    *,
    answer_prefix: str = "",
) -> str:
    """Render the typed-choice approval prompt as Telegram-friendly text."""
    choices = _normalized_choices(request.get("choices")) or list(APPROVAL_CHOICES)
    reason = str(request.get("reason", "")).strip()
    approval_id = str(request.get("approval_id", "")).strip()
    lines: list[str] = []
    lines.append("Approval required to continue.")
    if reason:
        lines.append(f"Reason: {reason}")
    if approval_id:
        lines.append(f"Approval id: {approval_id}")
    prefix = answer_prefix.strip()
    join_with = " " if not prefix else f" {prefix} "
    lines.append("Reply with one of:" + join_with + " / ".join(choices))
    return "\n".join(lines)


def parse_approval_decision(text: str | None) -> str | None:
    """Parse a Telegram user reply into a typed approval decision."""
    if text is None:
        return None
    if not isinstance(text, str):
        return None
    normalized = text.strip().lower()
    if not normalized:
        return None
    if normalized in APPROVAL_CHOICES:
        return normalized
    return None


__all__ = [
    "APPROVAL_CHOICES",
    "extract_approval_request",
    "render_approval_prompt",
    "parse_approval_decision",
]
