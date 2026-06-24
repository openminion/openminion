"""Generated runtime artifact path helpers."""

from __future__ import annotations

import os
import warnings
from pathlib import Path

from openminion.base.constants import (
    OPENMINION_DATA_ROOT_ENV,
    OPENMINION_GENERATED_ROOT_ENV,
    OPENMINION_HOME_ENV,
)
from openminion.base.config.paths import (
    ensure_under_data_root,
    resolve_data_root,
    resolve_data_root_enforcement_mode,
)

_BASE_GENERATED_RUNTIME_DIRNAME = "runtime"
_BASE_GENERATED_CONFIGS_DIRNAME = "configs"
_BASE_GENERATED_STATE_DIRNAME = "state"


def _resolve_base_home_root(home_root: str | Path | None = None) -> Path:
    if home_root is not None and str(home_root).strip():
        return Path(home_root).expanduser().resolve(strict=False)

    env_home = os.getenv(OPENMINION_HOME_ENV, "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve(strict=False)

    return Path.cwd().resolve(strict=False)


def resolve_generated_root(home_root: str | Path | None = None) -> Path:
    base_root = _resolve_base_home_root(home_root)
    data_root = resolve_data_root(
        base_root, data_root=os.getenv(OPENMINION_DATA_ROOT_ENV)
    )
    generated_root = (data_root / _BASE_GENERATED_RUNTIME_DIRNAME).resolve(strict=False)

    configured = os.getenv(OPENMINION_GENERATED_ROOT_ENV, "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = generated_root / candidate
        else:
            if resolve_data_root_enforcement_mode() == "soft":
                try:
                    candidate.relative_to(generated_root)
                except ValueError:
                    rewritten = generated_root / (
                        candidate.name or _BASE_GENERATED_RUNTIME_DIRNAME
                    )
                    warnings.warn(
                        f"generated_root '{candidate}' rewritten to '{rewritten}' under data_root/{_BASE_GENERATED_RUNTIME_DIRNAME}",
                        RuntimeWarning,
                    )
                    candidate = rewritten
        return ensure_under_data_root(candidate, generated_root, label="generated_root")

    return ensure_under_data_root(
        generated_root,
        generated_root,
        label="generated_root",
    )


def resolve_generated_config_path(
    filename: str,
    *,
    home_root: str | Path | None = None,
) -> Path:
    return (
        resolve_generated_root(home_root) / _BASE_GENERATED_CONFIGS_DIRNAME / filename
    ).resolve(strict=False)


def resolve_generated_state_path(
    filename: str,
    *,
    module: str | None = None,
    home_root: str | Path | None = None,
) -> Path:
    state_root = resolve_generated_root(home_root) / _BASE_GENERATED_STATE_DIRNAME
    if module:
        state_root = state_root / module
    return (state_root / filename).resolve(strict=False)
