import shlex
from pathlib import Path

READ_ONLY_DISCOVERY_HINTS: dict[str, tuple[str, str]] = {
    "find": (
        "file.find",
        (
            "If you are enumerating files in the workspace, use file.find "
            "or file.list_dir(recursive=True) instead of shelling out to "
            "find; both stay inside the workspace sandbox and return "
            "structured results."
        ),
    ),
    "ls": (
        "file.list_dir",
        (
            "If you are listing directory contents, use file.list_dir "
            "instead of shelling out to ls; file.list_dir returns "
            "structured entries and supports recursive listing."
        ),
    ),
    "cat": (
        "file.read",
        (
            "If you are reading a file, use file.read instead of shelling "
            "out to cat; file.read returns structured content with "
            "encoding handling."
        ),
    ),
    "head": (
        "file.read",
        (
            "If you are reading the first lines of a file, use file.read "
            "with the appropriate range arguments instead of shelling "
            "out to head."
        ),
    ),
    "tail": (
        "file.read",
        (
            "If you are reading the last lines of a file, use file.read "
            "with the appropriate range arguments instead of shelling "
            "out to tail."
        ),
    ),
    "grep": (
        "file.search",
        (
            "If you are searching for a pattern across files, use "
            "file.search instead of shelling out to grep; file.search "
            "returns structured matches with file path and line context."
        ),
    ),
    "curl": (
        "web.fetch",
        (
            "If you are fetching web content, use web.fetch for a known URL "
            "or web.search when you need to discover sources. Do not shell "
            "out to curl; the web tools provide the bounded network surface."
        ),
    ),
    "wget": (
        "web.fetch",
        (
            "If you are fetching web content, use web.fetch for a known URL "
            "or web.search when you need to discover sources. Do not shell "
            "out to wget; the web tools provide the bounded network surface."
        ),
    ),
}


def first_executable_token(command: str) -> str:
    """Return a normalized executable token for lightweight policy hinting."""

    try:
        parts = shlex.split(str(command or ""), posix=True)
    except ValueError:
        return ""
    if not parts:
        return ""
    return Path(parts[0]).name.lower()


def read_only_discovery_hint_for_command(command: str) -> tuple[str, str] | None:
    executable = first_executable_token(command)
    if not executable:
        return None
    return READ_ONLY_DISCOVERY_HINTS.get(executable)


__all__ = [
    "READ_ONLY_DISCOVERY_HINTS",
    "first_executable_token",
    "read_only_discovery_hint_for_command",
]
