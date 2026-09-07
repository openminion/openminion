"""Adaptive tool-loop prompt renderers."""

from __future__ import annotations


def build_seeded_policy_denial_recovery_message(
    *, blocked_tool: str, suggested_tool: str, suggested_fix: str = ""
) -> str:
    """Render recovery guidance after a seeded tool command is denied."""

    message = (
        f"The seeded {blocked_tool} command was blocked by policy. Do not "
        f"repeat it. Retry the same user task using {suggested_tool} if that "
        "structured tool can satisfy the intent."
    )
    fix = str(suggested_fix or "").strip()
    return f"{message} {fix}" if fix else message


def build_seeded_invalid_workdir_recovery_message() -> str:
    """Render recovery guidance after seeded exec.run uses an invalid workdir."""

    return (
        "The seeded exec.run command used a workdir that does not exist. "
        "Do not repeat it. exec.run already runs from the current workspace root. "
        "Retry without workdir when the command targets that root, or use an "
        "existing subdirectory relative to it."
    )


__all__ = [
    "build_seeded_invalid_workdir_recovery_message",
    "build_seeded_policy_denial_recovery_message",
]
