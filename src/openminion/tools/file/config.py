from dataclasses import dataclass

from .constants import FILE_DEFAULT_MAX_ENTRIES


@dataclass(frozen=True)
class FileToolConfig:
    default_max_entries: int = FILE_DEFAULT_MAX_ENTRIES


def load_config(*_args: object, **_kwargs: object) -> FileToolConfig:
    return FileToolConfig()


__all__ = ["FileToolConfig", "load_config"]
