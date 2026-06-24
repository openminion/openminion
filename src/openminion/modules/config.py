from pathlib import Path
from typing import Mapping

from openminion.base.config import resolve_data_root
from openminion.base.config.env import EnvironmentConfig

from openminion.base.constants import (
    OPENMINION_DATA_ROOT_ENV,
    OPENMINION_HOME_ENV,
    OPENMINION_MODULE_STANDALONE_ENV,
)

ModuleEnv = Mapping[str, str] | EnvironmentConfig

STANDALONE_MODE_TRUE_VALUES: frozenset[str] = frozenset({"1", "true", "yes"})


def _env_value(
    env: ModuleEnv | None,
    name: str,
    default: str = "",
) -> str:
    if env is None:
        return str(default or "")
    return str(env.get(name, default) or "").strip()


def _env_mapping(env: ModuleEnv | None) -> dict[str, str]:
    if env is None:
        return {}
    if isinstance(env, EnvironmentConfig):
        return env.snapshot()
    return dict(env)


def is_module_standalone_mode(
    env: ModuleEnv | None,
    *,
    env_name: str = OPENMINION_MODULE_STANDALONE_ENV,
    true_values: frozenset[str] = STANDALONE_MODE_TRUE_VALUES,
) -> bool:
    return _env_value(env, env_name, "").lower() in true_values


def resolve_module_home_root(
    home_root: Path | None,
    env: ModuleEnv | None = None,
    *,
    home_env_name: str = OPENMINION_HOME_ENV,
    fallback_to_cwd: bool = False,
) -> Path | None:
    if home_root is not None:
        return Path(home_root).expanduser().resolve(strict=False)
    raw = _env_value(env, home_env_name, "")
    if raw:
        return Path(raw).expanduser().resolve(strict=False)
    if fallback_to_cwd:
        return Path.cwd().resolve(strict=False)
    return None


def resolve_module_data_root(
    *,
    home_root: Path | None,
    env: ModuleEnv | None = None,
    data_root: Path | str | None = None,
    data_root_env_name: str = OPENMINION_DATA_ROOT_ENV,
    home_env_name: str = OPENMINION_HOME_ENV,
) -> Path | None:
    raw_override = str(data_root or "").strip()
    if raw_override:
        candidate = Path(raw_override).expanduser()
        if not candidate.is_absolute():
            base_root = home_root or Path.cwd().resolve(strict=False)
            candidate = base_root / candidate
        return candidate.resolve(strict=False)

    env_map = _env_mapping(env)
    env_root = _env_value(env, data_root_env_name, "")
    if env_root and home_root is not None:
        env_home = _env_value(env, home_env_name, "")
        if env_home:
            resolved_env_home = Path(env_home).expanduser().resolve(strict=False)
            resolved_home = Path(home_root).expanduser().resolve(strict=False)
            if resolved_env_home != resolved_home:
                env_root = ""
    if env_root:
        candidate = Path(env_root).expanduser()
        if not candidate.is_absolute():
            base_root = home_root or Path.cwd().resolve(strict=False)
            candidate = base_root / candidate
        return candidate.resolve(strict=False)

    if home_root is not None:
        return resolve_data_root(home_root, data_root=None, env=env_map)
    return None


def resolve_module_config_path(
    path_value: str | Path,
    *,
    home_root: Path | None = None,
) -> Path:
    candidate = Path(path_value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve(strict=False)
    if home_root is not None:
        return (home_root / candidate).resolve(strict=False)
    return candidate.resolve(strict=False)


def normalize_data_root_relative_path(path_value: Path) -> Path:
    if path_value.is_absolute():
        return path_value
    parts = path_value.parts
    if parts and parts[0] == ".openminion":
        return Path(*parts[1:]) if len(parts) > 1 else Path(".")
    return path_value


__all__ = [
    "ModuleEnv",
    "normalize_data_root_relative_path",
    "is_module_standalone_mode",
    "resolve_module_config_path",
    "resolve_module_data_root",
    "resolve_module_home_root",
]
