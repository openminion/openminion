from __future__ import annotations

from dataclasses import dataclass

from openminion.base.config.action_policy import normalize_action_policy_mode_override
from openminion.base.config.runtime.profile import (
    PERMISSION_MODE_BYPASS,
    PERMISSION_MODE_DEFAULT,
    PERMISSION_MODE_READONLY,
    PERMISSION_MODE_VALUES,
)

PERMISSION_CHOICE_READONLY = "readonly"
PERMISSION_CHOICE_ASK = "ask"
PERMISSION_CHOICE_AUTO = "auto"
PERMISSION_CHOICE_FULL_ACCESS = "full_access"


@dataclass(frozen=True)
class PermissionMenuChoice:
    choice_id: str
    label: str
    description: str
    permission_mode: str
    action_policy_mode: str | None
    status_label: str
    requires_confirmation: bool = False


@dataclass(frozen=True)
class PermissionApplyResult:
    choice: PermissionMenuChoice
    permission_mode: str
    action_policy_mode: str | None
    message: str


@dataclass(frozen=True)
class PermissionOverrideApplyResult:
    tool_name: str
    mode: str
    message: str


PERMISSION_MENU_CHOICES: tuple[PermissionMenuChoice, ...] = (
    PermissionMenuChoice(
        choice_id=PERMISSION_CHOICE_READONLY,
        label="Read only",
        description="Inspect and reason, but block write-capable tools.",
        permission_mode=PERMISSION_MODE_READONLY,
        action_policy_mode=None,
        status_label="read-only",
    ),
    PermissionMenuChoice(
        choice_id=PERMISSION_CHOICE_ASK,
        label="Ask for approval",
        description="Ask before risky actions.",
        permission_mode=PERMISSION_MODE_DEFAULT,
        action_policy_mode="ask",
        status_label="ask",
    ),
    PermissionMenuChoice(
        choice_id=PERMISSION_CHOICE_AUTO,
        label="Approve for me",
        description="Auto-approve actions considered safe by policy.",
        permission_mode=PERMISSION_MODE_DEFAULT,
        action_policy_mode="auto",
        status_label="auto",
    ),
    PermissionMenuChoice(
        choice_id=PERMISSION_CHOICE_FULL_ACCESS,
        label="Full access",
        description="Do not prompt for tool/action approval in this session.",
        permission_mode=PERMISSION_MODE_BYPASS,
        action_policy_mode="bypass",
        status_label="full access",
        requires_confirmation=True,
    ),
)

_PERMISSION_CHOICES_BY_ID = {
    choice.choice_id: choice for choice in PERMISSION_MENU_CHOICES
}
_PERMISSION_CHOICE_ALIASES = {
    "read-only": PERMISSION_CHOICE_READONLY,
    "readonly": PERMISSION_CHOICE_READONLY,
    "ask": PERMISSION_CHOICE_ASK,
    "ask-for-approval": PERMISSION_CHOICE_ASK,
    "approve": PERMISSION_CHOICE_AUTO,
    "approve-for-me": PERMISSION_CHOICE_AUTO,
    "auto": PERMISSION_CHOICE_AUTO,
    "full": PERMISSION_CHOICE_FULL_ACCESS,
    "full-access": PERMISSION_CHOICE_FULL_ACCESS,
    "full_access": PERMISSION_CHOICE_FULL_ACCESS,
}


def permission_choice_for_id(choice_id: str) -> PermissionMenuChoice:
    normalized = str(choice_id or "").strip().lower().replace(" ", "-")
    normalized = _PERMISSION_CHOICE_ALIASES.get(normalized, normalized)
    try:
        return _PERMISSION_CHOICES_BY_ID[normalized]
    except KeyError as exc:
        valid = ", ".join(choice.choice_id for choice in PERMISSION_MENU_CHOICES)
        raise ValueError(
            f"unknown permission menu choice {choice_id!r}; valid: {valid}"
        ) from exc


def permission_choice_for_modes(
    *,
    permission_mode: str | None,
    action_policy_mode: str | None,
) -> PermissionMenuChoice | None:
    mode = str(permission_mode or PERMISSION_MODE_DEFAULT).strip().lower()
    action = normalize_action_policy_mode_override(action_policy_mode)
    if mode == PERMISSION_MODE_READONLY:
        return permission_choice_for_id(PERMISSION_CHOICE_READONLY)
    if mode == PERMISSION_MODE_BYPASS or action == "bypass":
        return permission_choice_for_id(PERMISSION_CHOICE_FULL_ACCESS)
    if action == "auto":
        return permission_choice_for_id(PERMISSION_CHOICE_AUTO)
    if action == "ask":
        return permission_choice_for_id(PERMISSION_CHOICE_ASK)
    return None


def format_permission_status_label(
    *,
    permission_mode: str | None,
    action_policy_mode: str | None,
) -> str:
    mode = str(permission_mode or PERMISSION_MODE_DEFAULT).strip().lower()
    action = normalize_action_policy_mode_override(action_policy_mode)
    if mode == PERMISSION_MODE_BYPASS or action == "bypass":
        return "full access"
    parts: list[str] = []
    if mode == PERMISSION_MODE_READONLY:
        parts.append("read-only")
    elif mode and mode != PERMISSION_MODE_DEFAULT:
        parts.append(mode)
    if action in {"ask", "auto"}:
        parts.append(action)
    return " + ".join(parts)


def format_permission_overrides_label(overrides: object) -> str:
    if not isinstance(overrides, dict) or not overrides:
        return ""
    parts = [
        f"{str(tool).strip()}: {str(mode).strip()}"
        for tool, mode in sorted(overrides.items())
        if str(tool).strip() and str(mode).strip()
    ]
    return ", ".join(parts)


def apply_permission_menu_choice(
    runtime: object,
    choice_id: str,
    *,
    confirmed: bool = False,
) -> PermissionApplyResult:
    choice = permission_choice_for_id(choice_id)
    if choice.requires_confirmation and not confirmed:
        raise PermissionError("Full access requires explicit confirmation.")
    mode = _set_runtime_permission_mode(runtime, choice.permission_mode)
    action_mode = None
    if choice.action_policy_mode is not None:
        action_mode = _set_runtime_action_policy_mode(
            runtime, choice.action_policy_mode
        )
    status = format_permission_status_label(
        permission_mode=mode,
        action_policy_mode=action_mode,
    )
    if not status:
        status = choice.status_label
    warning = " — full access for this session" if choice.requires_confirmation else ""
    return PermissionApplyResult(
        choice=choice,
        permission_mode=mode,
        action_policy_mode=action_mode,
        message=f"permissions → {status}{warning}",
    )


def apply_permission_override(
    runtime: object,
    tool_name: str,
    mode: str,
) -> PermissionOverrideApplyResult:
    tool = str(tool_name or "").strip()
    if not tool:
        raise ValueError("tool name is required")
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode in {"default", "reset", "clear"}:
        clearer = getattr(runtime, "clear_permission_override", None)
        if callable(clearer):
            clearer(tool)
        else:
            setter = getattr(runtime, "set_permission_override", None)
            if not callable(setter):
                raise RuntimeError(
                    "runtime does not expose clear_permission_override"
                )
            setter(tool, "default")
        return PermissionOverrideApplyResult(
            tool_name=tool,
            mode=PERMISSION_MODE_DEFAULT,
            message=f"permissions → cleared override for {tool}",
        )
    setter = getattr(runtime, "set_permission_override", None)
    if not callable(setter):
        raise RuntimeError("runtime does not expose set_permission_override")
    applied = str(setter(tool, normalized_mode) or normalized_mode)
    warning = " — full access for this tool in this session" if applied == "bypass" else ""
    return PermissionOverrideApplyResult(
        tool_name=tool,
        mode=applied,
        message=f"permissions → {tool}: {applied}{warning}",
    )


def _set_runtime_permission_mode(runtime: object, mode: str) -> str:
    normalized = str(mode or PERMISSION_MODE_DEFAULT).strip().lower()
    if normalized not in PERMISSION_MODE_VALUES:
        raise ValueError(f"unknown permission mode {mode!r}")
    setter = getattr(runtime, "set_permission_mode", None)
    if not callable(setter):
        raise RuntimeError("runtime does not expose set_permission_mode")
    return str(setter(normalized) or PERMISSION_MODE_DEFAULT)


def _set_runtime_action_policy_mode(runtime: object, mode: str) -> str:
    normalized = normalize_action_policy_mode_override(mode)
    if normalized is None:
        raise ValueError(f"unknown action policy mode {mode!r}")
    setter = getattr(runtime, "set_session_action_policy_mode", None)
    if not callable(setter):
        raise RuntimeError("runtime does not expose set_session_action_policy_mode")
    return str(setter(normalized) or normalized)


__all__ = [
    "PERMISSION_CHOICE_ASK",
    "PERMISSION_CHOICE_AUTO",
    "PERMISSION_CHOICE_FULL_ACCESS",
    "PERMISSION_CHOICE_READONLY",
    "PERMISSION_MENU_CHOICES",
    "PermissionApplyResult",
    "PermissionMenuChoice",
    "PermissionOverrideApplyResult",
    "apply_permission_override",
    "apply_permission_menu_choice",
    "format_permission_overrides_label",
    "format_permission_status_label",
    "permission_choice_for_id",
    "permission_choice_for_modes",
]
