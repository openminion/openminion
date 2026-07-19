from __future__ import annotations

from ..errors import ToolRuntimeError

SCOPE_ORDER = {"READ_ONLY": 0, "WRITE_SAFE": 1, "POWER_USER": 2, "UI_AUTOMATION": 3}
_MKDIR_SCAFFOLD_HINT = {
    "suggested_tool": "file.write",
    "suggested_fix": (
        "For project scaffolding, write the target file directly with file.write; "
        "parent directories are created automatically by default."
    ),
}


def _invalid_argument(message: str) -> ToolRuntimeError:
    return ToolRuntimeError("INVALID_ARGUMENT", message)
