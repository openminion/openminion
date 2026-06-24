from pathlib import Path
from typing import Any

from .modes import mode_is_local, raise_if_strict


def create_compress_adapter(
    mode: str = "auto",
    db_path: str | Path | None = None,
    telemetryctl: Any | None = None,
) -> Any:
    del telemetryctl
    if mode_is_local(mode):
        return None
    try:
        from openminion.modules.context.compress.service import CompressionService

        return CompressionService()
    except ImportError:
        raise_if_strict(mode)
        return None
