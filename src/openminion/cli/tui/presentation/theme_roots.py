from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_runtime_data_root(runtime: Any) -> Path | None:
    api_runtime = getattr(runtime, "_rt", None)
    data_root = getattr(api_runtime, "data_root", None)
    if data_root is not None:
        try:
            return Path(str(data_root))
        except (TypeError, ValueError):
            return None
    config_path = str(getattr(api_runtime, "config_path", "") or "").strip()
    if not config_path:
        return None
    try:
        from openminion.cli.config import resolve_cli_roots

        roots = resolve_cli_roots(config_path=config_path, fallback_to_cwd=True)
        return Path(str(getattr(roots, "data_root", "") or "")).resolve(strict=False)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def resolve_theme_data_root(runtime: Any) -> Path | None:
    return resolve_runtime_data_root(runtime)
