from __future__ import annotations

from openminion.cli.config import resolve_cli_roots
from openminion.cli.presentation import styles
from openminion.services.brain import resolve_brain_runtime_db_path
from openminion.cli.commands.goal import execute_goal_cli_command


def handle_goal_command(
    line: str,
    *,
    session_id: str,
    config_path: str | None = None,
) -> bool:
    roots = resolve_cli_roots(config_path=config_path, fallback_to_cwd=True)
    db_path = resolve_brain_runtime_db_path(storage_path=roots.data_root)
    tone, message = execute_goal_cli_command(
        line, session_id=session_id, db_path=db_path
    )
    style_token = {
        "info": styles.StyleToken.INFO,
        "success": styles.StyleToken.SUCCESS,
        "error": styles.StyleToken.ERROR,
    }[tone]
    print(styles.style(style_token, message))
    return True


__all__ = ["handle_goal_command"]
