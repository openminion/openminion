from __future__ import annotations

from importlib import import_module
from typing import Any

_LAZY_EXPORTS = {
    "ChatMessage": (".models", "ChatMessage"),
    "DEFAULT_PROGRESS_FALLBACK": (".status", "DEFAULT_PROGRESS_FALLBACK"),
    "DEFAULT_THINKING_LABEL": (".status", "DEFAULT_THINKING_LABEL"),
    "MessageKind": (".models", "MessageKind"),
    "RuntimeHeaderContext": (".header", "RuntimeHeaderContext"),
    "ThinkingIndicator": (".status", "ThinkingIndicator"),
    "ToolBlockWidget": (".tool.blocks", "ToolBlockWidget"),
    "ToolEvent": (".models", "ToolEvent"),
    "build_tool_event_from_progress": (
        ".tool.progress",
        "build_tool_event_from_progress",
    ),
    "coerce_optional_int": (".tool.progress", "coerce_optional_int"),
    "copy_to_clipboard": (".clipboard", "copy_to_clipboard"),
    "format_chat_timestamp": (".models", "format_chat_timestamp"),
    "format_clock": (".header", "format_clock"),
    "format_runtime_label": (".header", "format_runtime_label"),
    "format_progress_label": (
        "openminion.cli.status.formatting",
        "format_primary_status_text",
    ),
    "looks_like_markdown": (".messages", "looks_like_markdown"),
    "render_body": (".messages", "render_body"),
    "render_error_text": (".messages", "render_error_text"),
    "render_markdown": (".messages", "render_markdown"),
    "render_system_text": (".messages", "render_system_text"),
    "render_user_text": (".messages", "render_user_text"),
    "resolve_theme_data_root": (".theme_roots", "resolve_theme_data_root"),
    "resolve_runtime_data_root": (".theme_roots", "resolve_runtime_data_root"),
    "shorten_session_id": (".header", "shorten_session_id"),
    "shorten_working_dir": (".header", "shorten_working_dir"),
    "slash_help_rows": (".slash_commands", "slash_help_rows"),
    "tool_call_body": (".tool.formatting", "tool_call_body"),
    "tool_context_hint": (".tool.formatting", "tool_context_hint"),
}
__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute_name = target
    if module_name.startswith("."):
        module_name = f"{__name__}{module_name}"
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(globals().keys() | _LAZY_EXPORTS.keys())
