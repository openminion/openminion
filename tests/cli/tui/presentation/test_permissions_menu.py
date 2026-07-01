from __future__ import annotations

import pytest

from openminion.cli.tui.presentation.permissions import (
    PERMISSION_CHOICE_ASK,
    PERMISSION_CHOICE_AUTO,
    PERMISSION_CHOICE_FULL_ACCESS,
    PERMISSION_CHOICE_READONLY,
    apply_permission_menu_choice,
    format_permission_status_label,
    permission_choice_for_id,
)


class _RuntimeDouble:
    def __init__(self) -> None:
        self.permission_mode = "default"
        self.action_policy_mode_override = ""

    def set_permission_mode(self, mode: str) -> str:
        self.permission_mode = mode
        return mode

    def set_session_action_policy_mode(self, mode: str) -> str:
        self.action_policy_mode_override = mode
        return mode


def test_permission_choice_aliases_cover_human_labels() -> None:
    assert permission_choice_for_id("read-only").choice_id == PERMISSION_CHOICE_READONLY
    assert permission_choice_for_id("ask").choice_id == PERMISSION_CHOICE_ASK
    assert permission_choice_for_id("approve-for-me").choice_id == PERMISSION_CHOICE_AUTO
    assert permission_choice_for_id("full access").choice_id == PERMISSION_CHOICE_FULL_ACCESS


def test_apply_ask_maps_to_default_permission_plus_action_policy_ask() -> None:
    runtime = _RuntimeDouble()

    result = apply_permission_menu_choice(runtime, PERMISSION_CHOICE_ASK)

    assert runtime.permission_mode == "default"
    assert runtime.action_policy_mode_override == "ask"
    assert result.message == "permissions → ask"


def test_apply_readonly_preserves_existing_action_policy_axis() -> None:
    runtime = _RuntimeDouble()
    runtime.action_policy_mode_override = "auto"

    result = apply_permission_menu_choice(runtime, PERMISSION_CHOICE_READONLY)

    assert runtime.permission_mode == "readonly"
    assert runtime.action_policy_mode_override == "auto"
    assert result.action_policy_mode is None


def test_full_access_requires_explicit_confirmation() -> None:
    runtime = _RuntimeDouble()

    with pytest.raises(PermissionError):
        apply_permission_menu_choice(runtime, PERMISSION_CHOICE_FULL_ACCESS)

    result = apply_permission_menu_choice(
        runtime,
        PERMISSION_CHOICE_FULL_ACCESS,
        confirmed=True,
    )
    assert runtime.permission_mode == "bypass"
    assert runtime.action_policy_mode_override == "bypass"
    assert "full access" in result.message


def test_status_label_keeps_permission_and_approval_axes_distinct() -> None:
    assert (
        format_permission_status_label(
            permission_mode="readonly",
            action_policy_mode="auto",
        )
        == "read-only + auto"
    )
    assert (
        format_permission_status_label(
            permission_mode="default",
            action_policy_mode="ask",
        )
        == "ask"
    )
    assert (
        format_permission_status_label(
            permission_mode="bypass",
            action_policy_mode="ask",
        )
        == "full access"
    )
