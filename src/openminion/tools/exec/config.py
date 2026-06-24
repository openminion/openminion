from dataclasses import dataclass

from .constants import EXEC_POLICY_PATH_ENV


@dataclass(frozen=True)
class ExecToolConfig:
    policy_path_env: str = EXEC_POLICY_PATH_ENV


def load_config(*_args: object, **_kwargs: object) -> ExecToolConfig:
    return ExecToolConfig()


__all__ = ["ExecToolConfig", "load_config"]
