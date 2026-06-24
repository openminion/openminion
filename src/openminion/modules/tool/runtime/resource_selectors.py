from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceSelectors:
    """Policy-facing resource intent extracted from tool args."""

    paths_read: tuple[str, ...] = ()
    paths_write: tuple[str, ...] = ()
    paths_delete: tuple[str, ...] = ()
    command: str = ""
    args: tuple[str, ...] = ()
    cwd: str = ""
    env_keys_requested: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    hosts: tuple[str, ...] = ()
    ports: tuple[int, ...] = ()
    protocols: tuple[str, ...] = ()


__all__ = ["ResourceSelectors"]
