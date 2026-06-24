"""CLI parser entrypoints and shared argument flags."""

from .base import build_parser
from .flags import add_json_output_flag, add_tool_session_arg

__all__ = ["add_json_output_flag", "add_tool_session_arg", "build_parser"]
