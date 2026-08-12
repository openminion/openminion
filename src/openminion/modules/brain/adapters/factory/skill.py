from pathlib import Path
from typing import Any

from .modes import mode_is_local, raise_if_strict


def create_skill_adapter(
    mode: str = "auto",
    db_path: str | Path | None = None,
    *,
    home_root: str | Path | None = None,
    config: Any = None,
    telemetryctl: Any | None = None,
) -> Any:
    if mode_is_local(mode):
        return None
    try:
        from openminion.modules.skill.runtime.skill import Skill

        resolved_home_root = (
            Path(home_root).expanduser().resolve(strict=False) if home_root else None
        )
        return Skill(
            config=config if config is not None else {},
            home_root=resolved_home_root,
            telemetryctl=telemetryctl,
        )
    except ImportError:
        raise_if_strict(mode)
        return None
