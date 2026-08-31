import shlex
from collections.abc import Sequence


_READ_ONLY_COMMAND_PREFIXES = (
    ("ps",),
    ("docker", "info"),
    ("docker", "version"),
    ("docker", "ps"),
    ("docker", "images"),
    ("docker", "inspect"),
    ("docker", "context", "ls"),
    ("docker", "context", "show"),
    ("docker", "context", "inspect"),
    ("docker", "container", "ls"),
    ("docker", "container", "inspect"),
    ("docker", "image", "ls"),
    ("docker", "image", "inspect"),
    ("docker", "network", "ls"),
    ("docker", "network", "inspect"),
    ("docker", "volume", "ls"),
    ("docker", "volume", "inspect"),
    ("systemctl", "status"),
    ("systemctl", "show"),
    ("systemctl", "is-active"),
    ("systemctl", "is-enabled"),
    ("systemctl", "is-failed"),
    ("systemctl", "list-units"),
    ("systemctl", "list-unit-files"),
)


def is_bounded_read_only_command(argv: Sequence[str]) -> bool:
    normalized = tuple(str(arg).strip().lower() for arg in argv)
    return any(
        normalized[: len(prefix)] == prefix
        for prefix in _READ_ONLY_COMMAND_PREFIXES
    )


def normalize_cd_prefixed_command(
    *,
    command: str,
    workdir: str | None,
) -> tuple[str, str | None]:
    raw_command = command.strip()
    if "&&" not in raw_command:
        return raw_command, workdir
    prefix, remainder = raw_command.split("&&", 1)
    try:
        argv = shlex.split(prefix.strip(), posix=True)
    except ValueError:
        return raw_command, workdir
    if len(argv) != 2 or argv[0].strip() != "cd":
        return raw_command, workdir
    if not (normalized_command := remainder.strip()):
        return raw_command, workdir
    effective_workdir = (workdir or "").strip() or argv[1].strip()
    return normalized_command, effective_workdir or None
