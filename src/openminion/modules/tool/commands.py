import shlex


def normalize_cd_prefixed_command(
    *,
    command: str,
    workdir: str | None,
) -> tuple[str, str | None]:
    raw_command = str(command or "").strip()
    if "&&" not in raw_command:
        return raw_command, workdir
    prefix, remainder = raw_command.split("&&", 1)
    try:
        argv = shlex.split(prefix.strip(), posix=True)
    except ValueError:
        return raw_command, workdir
    if len(argv) != 2 or str(argv[0]).strip() != "cd":
        return raw_command, workdir
    normalized_command = str(remainder or "").strip()
    if not normalized_command:
        return raw_command, workdir
    effective_workdir = str(workdir or "").strip() or str(argv[1]).strip()
    return normalized_command, effective_workdir or None
