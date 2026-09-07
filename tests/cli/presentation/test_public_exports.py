from __future__ import annotations

import importlib

import openminion.cli.presentation as presentation


_EXPECTED_EXPORTS = [
    "ChatMessage",
    "MessageKind",
    "RuntimeHeaderContext",
    "ToolEvent",
    "build_tool_event_from_progress",
    "coerce_optional_int",
    "copy_to_clipboard",
    "format_chat_timestamp",
    "format_clock",
    "format_runtime_label",
    "format_progress_label",
    "looks_like_markdown",
    "render_body",
    "render_error_text",
    "render_markdown",
    "render_system_text",
    "render_user_text",
    "resolve_theme_data_root",
    "resolve_runtime_data_root",
    "shorten_session_id",
    "shorten_working_dir",
    "slash_help_rows",
    "tool_call_body",
    "tool_context_hint",
]


def test_presentation_public_exports_remain_compatible() -> None:
    assert presentation.__all__ == _EXPECTED_EXPORTS
    assert set(_EXPECTED_EXPORTS) <= set(dir(presentation))

    namespace: dict[str, object] = {}
    exec("from openminion.cli.presentation import *", namespace)
    for name in _EXPECTED_EXPORTS:
        value = getattr(presentation, name)
        assert namespace[name] is value
        assert getattr(presentation, name) is value


def test_presentation_submodule_and_formatter_reexports_remain_compatible() -> None:
    styles = importlib.import_module("openminion.cli.presentation.styles")
    from openminion.cli.presentation import styles as package_styles

    assert package_styles is styles
