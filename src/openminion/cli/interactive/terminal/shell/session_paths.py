from __future__ import annotations

from pathlib import Path
from typing import Any


def discover_custom_commands_for(*, runtime: Any, working_dir: str) -> dict[str, Any]:
    from openminion.cli.presentation.custom_commands import discover_custom_commands

    project_dir = (
        Path(working_dir) / ".openminion" / "commands" if working_dir else None
    )
    user_dir: Path | None = None
    data_root = getattr(getattr(runtime, "api_runtime", runtime), "data_root", None)
    if data_root is not None:
        try:
            user_dir = Path(str(data_root)) / "commands"
        except (OSError, RuntimeError, TypeError, ValueError):
            user_dir = None
    try:
        return discover_custom_commands(project_dir=project_dir, user_dir=user_dir)
    except (OSError, RuntimeError, ValueError):
        return {}


def focus_history_path(runtime: Any) -> str | None:
    data_root = getattr(getattr(runtime, "api_runtime", None), "data_root", None)
    raw = str(data_root or "").strip()
    if not raw:
        return None
    history_dir = Path(raw).expanduser().resolve(strict=False) / "cli"
    history_dir.mkdir(parents=True, exist_ok=True)
    return str(history_dir / "terminal_history")
