from dataclasses import dataclass

from .constants import DEFAULT_TASK_NAME_MAX_CHARS


@dataclass(frozen=True)
class TaskToolConfig:
    default_task_name_max_chars: int = DEFAULT_TASK_NAME_MAX_CHARS


def load_config(*_args: object, **_kwargs: object) -> TaskToolConfig:
    return TaskToolConfig()


__all__ = ["TaskToolConfig", "load_config"]
